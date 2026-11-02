#!/usr/bin/env python3
"""Generate this bundle's configuration: .env, flask.env, spring.env, CREDENTIALS.txt, out/.

    ./setup.py --domain myschool.org --admin-email it@myschool.org
    ./setup.py --domain newname.org          re-render everything; secrets and passwords stay
    ./setup.py --profile cockpit             apply profiles/cockpit.json
    ./setup.py --check-ports                 list what is listening, change nothing

Choices are remembered in .env, so a re-run only needs the options that change. Precedence:
option > environment variable (host ports) > existing .env > profile > default.
Other edits to flask.env / spring.env (API keys, ...) are kept across re-runs.
Standard library only.
"""
import argparse
import base64
import json
import os
import re
import secrets
import shlex
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
PLACEHOLDER_DOMAIN = "example.org"
KITS = ("vanilla", "education", "business")
# seeded uids per kit (kits/catalog.json): admin is always "admin"
ROLE_UIDS = {"vanilla": ("editor", "user"), "education": ("teacher", "student"), "business": ("manager", "member")}

# Spring's rule; '$' is never used because docker compose expands it inside env files.
SPRING_SYMBOLS = "~!@#%^&*()"
GENERATED_SYMBOLS = "~!@%^&*"

# name -> (variable in .env, default host port)
PORTS = {"flask": ("FLASK_HOST_PORT", 8587), "spring": ("SPRING_HOST_PORT", 8585),
         "spring_ws": ("SPRING_WS_HOST_PORT", 8589), "socket": ("SOCKET_HOST_PORT", 8500)}
DEFAULT_COOKIES = {"flask_jwt": "jwt_python_flask", "flask_session": "sess_python_flask",
                   "spring_jwt": "jwt_java_spring", "spring_session": "sess_java_spring"}
DEFAULT_PROJECT = "ocs-kit"

# .env keys a profile sets
PROFILE_KEYS = {"COMPOSE_PROJECT_NAME", "OCS_HOST_PREFIX", "OCS_COOKIE_DOMAIN"} | {v[0] for v in PORTS.values()}

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
PREFIX_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9-])?$")
EMAIL_RE = re.compile(r"^[^@\s$#'\"]+@[^@\s$#'\"]+\.[^@\s$#'\"]+$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}$")
COOKIE_RE = re.compile(r"^[A-Za-z0-9_]+$")


def fail(msg):
    sys.exit("setup: " + msg)


# --- secrets ---------------------------------------------------------------------------------

def password_problems(pw):
    checks = [(len(pw) >= 8, "at least 8 characters"), (re.search(r"[A-Z]", pw), "an uppercase letter"),
              (re.search(r"[a-z]", pw), "a lowercase letter"), (re.search(r"[0-9]", pw), "a digit"),
              (any(c in SPRING_SYMBOLS for c in pw), "a symbol from " + SPRING_SYMBOLS),
              ("$" not in pw, "no $")]
    return [what for ok, what in checks if not ok]


def generate_password(length=20):
    rng = secrets.SystemRandom()
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, GENERATED_SYMBOLS]
    chars = [rng.choice(p) for p in pools]
    everything = string.ascii_letters + string.digits + GENERATED_SYMBOLS
    chars += [rng.choice(everything) for _ in range(length - len(chars))]
    rng.shuffle(chars)
    return "".join(chars)


def generate_jwt_secret():
    return base64.b64encode(secrets.token_bytes(48)).decode("ascii")


# --- env files -------------------------------------------------------------------------------

def parse_env(text):
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def set_env_values(text, values):
    """Replace the first uncommented line for each key, else the first commented one, else append."""
    for key, value in values.items():
        rx = re.compile(r"^[ \t]*(#[ \t]*)?" + re.escape(key) + r"[ \t]*=.*$", re.M)
        matches = list(rx.finditer(text))
        line = "%s=%s" % (key, value)
        if not matches:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
            continue
        target = next((m for m in matches if m.group(1) is None), matches[0])
        text = text[:target.start()] + line + text[target.end():]
    return text


def write_private(path, text, mode=0o600):
    """Atomic write, created private (never world-readable, even briefly)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_text(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- nginx -----------------------------------------------------------------------------------

def strip_header(text):
    """Drop the template's leading how-to comment: it describes envsubst, not these files."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:]) + "\n"


def render_nginx(cfg):
    h, p = cfg["hosts"], cfg["ports"]
    header = "# Generated by setup.py from templates/%s. Edit the options and re-run setup.py instead.\n"

    def finish(name, text):
        # compose publishes on IPv4 only, and "localhost" may try ::1 first
        return header % name + strip_header(text).replace("http://localhost:", "http://127.0.0.1:")

    flask = (read_text(HERE / "templates/flask-nginx.conf.template")
             .replace("flask.${SITE_DOMAIN}", h["flask"])
             .replace("${FLASK_PORT}", str(p["flask"])).replace("${SOCKET_PORT}", str(p["socket"])))
    sock = (read_text(HERE / "templates/socket-nginx.conf.template")
            .replace("socket.${SITE_DOMAIN}", h["socket"]).replace("${SOCKET_PORT}", str(p["socket"])))
    spring = (read_text(HERE / "templates/spring-nginx.conf.template")
              .replace("${SITE_DOMAIN}", h["spring"])
              .replace("localhost:8585", "localhost:%d" % p["spring"])  # hard-coded in the template
              .replace("localhost:8589", "localhost:%d" % p["spring_ws"]))
    return {"ocs-kit-flask.conf": finish("flask-nginx.conf.template", flask),
            "ocs-kit-spring.conf": finish("spring-nginx.conf.template", spring),
            "ocs-kit-socket.conf": finish("socket-nginx.conf.template", sock)}


# --- ports -----------------------------------------------------------------------------------

def listening_ports():
    """{port: description} of TCP listeners plus ports Docker publishes (visible even without a
    userland proxy). Falls back to /proc when ss is missing."""
    found = {}
    if shutil.which("ss"):
        try:
            out = subprocess.run(["ss", "-ltnHp"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    port = parts[3].rsplit(":", 1)[-1]
                    if port.isdigit():
                        found[int(port)] = " ".join([parts[3]] + parts[5:])
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        for name in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(name) as f:
                    for row in f.readlines()[1:]:
                        cols = row.split()
                        if cols[3] == "0A":  # LISTEN
                            found.setdefault(int(cols[1].rsplit(":", 1)[1], 16), "listening")
            except OSError:
                pass
    if shutil.which("docker"):
        try:
            out = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
                                 capture_output=True, text=True, timeout=20).stdout
            for line in out.splitlines():
                name, _, ports = line.partition("\t")
                for port in re.findall(r":(\d+)->", ports):
                    found[int(port)] = "docker container " + name
        except (OSError, subprocess.SubprocessError):
            pass
    return found


def project_containers(project):
    """[(name, working dir)] of containers already in this compose project."""
    if not shutil.which("docker"):
        return []
    try:
        out = subprocess.run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + project,
                              "--format", '{{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}'],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [tuple(line.split("\t", 1)) for line in out.splitlines() if "\t" in line]


def check_ports(cfg):
    """Returns the number of conflicts; a port held by this very project is not one."""
    listeners = listening_ports()
    ours = project_containers(cfg["project"])
    here = str(HERE)
    foreign_project = [c for c in ours if c[1] and c[1] != here]
    own_names = {c[0] for c in ours if not c[1] or c[1] == here}
    conflicts = 0
    print("Ports for project %s (127.0.0.1):" % cfg["project"])
    for name, port in cfg["ports"].items():
        who = listeners.get(port)
        if who is None:
            state = "free"
        elif any(who == "docker container " + n for n in own_names):
            state = "this project (%s)" % who
        else:
            state = "IN USE: " + who
            conflicts += 1
        print("  %-10s %5d  %s" % (name, port, state))
    wanted = set(cfg["ports"].values())
    others = sorted(p for p in listeners if p not in wanted)
    print("Other listeners on this machine:")
    for p in others:
        print("  %5d  %s" % (p, listeners[p]))
    if not others:
        print("  (none)")
    if foreign_project:
        conflicts += 1
        print("Compose project %s already exists in another folder, so a compose command here would "
              "act on those containers: %s" % (cfg["project"], ", ".join("%s (%s)" % c for c in foreign_project)))
    return conflicts


# --- configuration ---------------------------------------------------------------------------

def load_profile(name):
    if not re.match(r"^[a-z0-9_-]+$", name):
        fail("bad profile name: %s" % name)
    path = HERE / "profiles" / (name + ".json")
    if not path.exists():
        fail("no such profile: %s (see profiles/)" % name)
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(args, state, environ=None):
    environ = os.environ if environ is None else environ
    profile_name = args.profile or state.get("OCS_PROFILE", "")
    if profile_name == "none":
        profile_name = ""
    if profile_name != state.get("OCS_PROFILE", ""):
        # Switching profile: what the old one (or the defaults) chose must not outrank the new one
        state = {k: v for k, v in state.items() if k not in PROFILE_KEYS}
    profile = load_profile(profile_name) if profile_name else {}

    def pick(arg, key, default):
        if arg is not None:
            return arg
        return state.get(key) or default

    domain = pick(args.domain, "OCS_DOMAIN", PLACEHOLDER_DOMAIN).lower().strip().rstrip(".")
    if not DOMAIN_RE.match(domain):
        fail("not a valid domain: %r" % domain)
    kit = pick(args.kit, "OCS_KIT", "education")
    if kit not in KITS:
        fail("KIT must be one of: " + ", ".join(KITS))
    # an email that was never chosen follows the domain
    chosen_email = pick(args.admin_email, "OCS_ADMIN_EMAIL", "")
    admin_email = chosen_email or "admin@" + domain
    if not EMAIL_RE.match(admin_email):
        fail("not a valid email: %r" % admin_email)
    school = pick(args.school_name, "OCS_SCHOOL_NAME", "OCS Kit")
    if re.search(r"[\n$#\"']", school):
        fail("the school name cannot contain newlines or any of $ # ' \"")
    prefix = pick(args.host_prefix, "OCS_HOST_PREFIX", profile.get("host_prefix", ""))
    if prefix and not PREFIX_RE.match(prefix):
        fail("not a valid hostname prefix: %r" % prefix)
    project = pick(args.project, "COMPOSE_PROJECT_NAME", profile.get("project", DEFAULT_PROJECT))
    if not PROJECT_RE.match(project):
        fail("not a valid compose project name: %r" % project)
    tag = pick(args.image_tag, "IMAGE_TAG", "latest")
    if not TAG_RE.match(tag):
        fail("not a valid image tag: %r" % tag)

    ports = {}
    for name, (var, default) in PORTS.items():
        value = getattr(args, name + "_port")
        if value is None:
            value = environ.get(var) or state.get(var) or profile.get("ports", {}).get(name) or default
        try:
            ports[name] = int(value)
        except ValueError:
            fail("%s must be a port number" % var)
        if not 1 <= ports[name] <= 65535:
            fail("%s must be a port number" % var)
    if len(set(ports.values())) != len(ports):
        fail("the host ports must all differ: %s" % ports)

    hosts = {name: "%s%s.%s" % (prefix, name, domain) for name in ("flask", "spring", "socket", "pages")}

    pages_url = pick(args.pages_url, "OCS_PAGES_URL", "")
    if pages_url:
        parts = urlsplit(pages_url)
        if parts.scheme not in ("http", "https") or not parts.netloc or re.search(r"[\s$#\"']", pages_url):
            fail("not a valid website URL: %r" % pages_url)
        pages_url = pages_url.rstrip("/")
    frontend = pages_url or "https://" + hosts["pages"]
    origins = ["https://" + hosts["pages"]]
    if pages_url:
        p = urlsplit(pages_url)
        origins.append("%s://%s" % (p.scheme, p.netloc))

    # cookie domain: explicit value, or "none" for host-only; a profile can default to host-only
    cookie_choice = pick(args.cookie_domain, "OCS_COOKIE_DOMAIN", "")
    if cookie_choice == "none":
        cookie_domain = ""
    elif cookie_choice:
        if not re.match(r"^\.[a-z0-9.-]+$", cookie_choice):
            fail("the cookie domain looks like .myschool.org (or 'none' for host-only cookies)")
        cookie_domain = cookie_choice
    else:
        cookie_domain = "" if profile.get("host_only_cookies") else "." + domain

    cookies = dict(DEFAULT_COOKIES, **profile.get("cookies", {}))
    for value in cookies.values():
        if not COOKIE_RE.match(value):
            fail("bad cookie name in the profile: %r" % value)

    return {"profile": profile_name, "target_dir": profile.get("target_dir", ""), "domain": domain, "kit": kit,
            "admin_email": admin_email, "chosen_email": chosen_email, "school": school, "prefix": prefix,
            "project": project, "tag": tag, "ports": ports, "hosts": hosts, "frontend": frontend,
            "pages_url": pages_url, "cors": ",".join(dict.fromkeys(origins)), "cookie_domain": cookie_domain,
            "cookie_choice": cookie_choice, "cookies": cookies}


def compose_env_values(cfg):
    uid, gid = os.getuid(), os.getgid()
    values = {"COMPOSE_PROJECT_NAME": cfg["project"], "IMAGE_TAG": cfg["tag"], "APP_UID": uid, "APP_GID": gid,
              "CORS_ORIGINS": cfg["cors"],
              "OCS_PROFILE": cfg["profile"], "OCS_DOMAIN": cfg["domain"], "OCS_KIT": cfg["kit"],
              "OCS_ADMIN_EMAIL": cfg["chosen_email"], "OCS_SCHOOL_NAME": cfg["school"],
              "OCS_HOST_PREFIX": cfg["prefix"], "OCS_COOKIE_DOMAIN": cfg["cookie_choice"],
              "OCS_PAGES_URL": cfg["pages_url"]}
    for name, (var, _default) in PORTS.items():
        values[var] = cfg["ports"][name]
    return values


COMPOSE_ENV_HEADER = """# Generated by setup.py: compose variables, plus the choices setup.py remembers (OCS_*).
# Change them by re-running setup.py with the matching option, so nginx and the env files follow.
"""


def backend_values(cfg, flask_old, spring_old):
    """Managed values for flask.env and spring.env. Existing secrets and passwords win."""
    def keep(*sources_key):
        key = sources_key[0]
        return (flask_old.get(key) or spring_old.get(key) or "").strip()

    reset = keep("RESET_TOKEN_SECRET") or secrets.token_hex(32)
    passwords = {key: keep(key) or generate_password()
                 for key in ("ADMIN_PASSWORD", "TEACHER_PASSWORD", "DEFAULT_PASSWORD")}
    common = dict(passwords, SCHOOL_NAME=cfg["school"], FRONTEND_URL=cfg["frontend"], CORS_ORIGINS=cfg["cors"],
                  COOKIE_DOMAIN=cfg["cookie_domain"], KIT=cfg["kit"], IS_PRODUCTION="true",
                  RESET_TOKEN_SECRET=reset)
    flask = dict(common, SITE_DOMAIN=cfg["domain"],
                 SECRET_KEY=(flask_old.get("SECRET_KEY") or "").strip() or secrets.token_hex(32),
                 JWT_TOKEN_NAME=cfg["cookies"]["flask_jwt"], SESSION_COOKIE_NAME=cfg["cookies"]["flask_session"],
                 DB_INIT="false")
    spring = dict(common, SITE_DOMAIN=cfg["hosts"]["spring"],
                  JWT_SECRET=(spring_old.get("JWT_SECRET") or "").strip() or generate_jwt_secret(),
                  JWT_TOKEN_NAME=cfg["cookies"]["spring_jwt"], SESSION_COOKIE_NAME=cfg["cookies"]["spring_session"],
                  KIT_DEV_MODE="false", ADMIN_EMAIL=cfg["admin_email"], MY_EMAIL="myuser@" + cfg["domain"],
                  PAGES_BOT_EMAIL="pages-bot@" + cfg["domain"])
    return flask, spring


def credentials_text(cfg, flask):
    staff, member = ROLE_UIDS[cfg["kit"]]
    return """OCS-in-a-Kit credentials (generated by setup.py). Keep private.

Kit:      {kit}
Domain:   {domain}{placeholder}

Same accounts on both backends:
  admin     uid: admin      password: {admin}
  staff     uid: {staff:<10} password: {teacher}
  member    uid: {member:<10} password: {default}

Default password (uid above, sample accounts, what an admin "reset password" sets): {default}
The seed also creates sample accounts with the default password, including an extra ADMIN on
Spring (uid myuser). Delete or change them after the first login (see DEPLOY.md).
Changing a password here does nothing once the database exists: accounts are seeded on first boot.

Backends: https://{flask_host}  https://{spring_host}
Website:  {frontend}
""".format(kit=cfg["kit"], domain=cfg["domain"],
           placeholder="   (PLACEHOLDER: change it before going live)" if cfg["domain"] == PLACEHOLDER_DOMAIN else "",
           admin=flask["ADMIN_PASSWORD"], teacher=flask["TEACHER_PASSWORD"], default=flask["DEFAULT_PASSWORD"],
           staff=staff, member=member, flask_host=cfg["hosts"]["flask"], spring_host=cfg["hosts"]["spring"],
           frontend=cfg["frontend"])


def next_steps_text(cfg):
    h = cfg["hosts"]
    names = ["ocs-kit-flask", "ocs-kit-spring"]
    lines = ["# Generated by setup.py. Run from this folder.", "", "# DNS: A records to this server",
             *("#   %s" % h[n] for n in ("flask", "spring", "socket")),
             "# and point %s at the website host." % h["pages"], "", "# nginx"]
    for n in names:
        lines.append("sudo cp out/nginx/%s.conf /etc/nginx/sites-available/%s.conf" % (n, n))
        lines.append("sudo ln -sf /etc/nginx/sites-available/%s.conf /etc/nginx/sites-enabled/%s.conf" % (n, n))
    lines += ["sudo nginx -t && sudo systemctl reload nginx",
              "sudo certbot --nginx -d %s -d %s" % (h["flask"], h["spring"]),
              "sudo nginx -t && sudo systemctl reload nginx", "",
              "# Website _config.yml", "kit:", "  backend:",
              '    flask: "https://%s"' % h["flask"], '    spring: "https://%s"' % h["spring"], ""]
    return "\n".join(lines)


DATA_DIRS = ("data/flask/instance", "data/spring/volumes")


def foreign_data():
    """Files under the data folders that are not ours (symlinks not followed)."""
    uid = os.getuid()
    found = []
    for rel in DATA_DIRS:
        top = HERE / rel
        if not top.is_dir():
            continue
        for root, dirs, files in os.walk(top):
            found += [os.path.join(root, n) for n in [""] + dirs + files
                      if os.lstat(os.path.join(root, n)).st_uid != uid]
    return found


def foreign_data_message(found):
    """The containers run as this user, never as root, so data they cannot write stops setup."""
    owner = "%d:%d" % (os.getuid(), os.getgid())
    fixes = ["  docker run --rm -v %s:/d alpine chown -R %s /d" % (shlex.quote(str(HERE / rel)), owner)
             for rel in DATA_DIRS if any(p.startswith(str(HERE / rel)) for p in found)]
    return ("%d file(s) in the data folders are not owned by you (uid %d), e.g. %s.\nThe containers run as "
            "your user and could not write them. Stop the stack ('docker compose stop' in this folder), "
            "run this once, then re-run setup:\n%s" % (len(found), os.getuid(), found[0], "\n".join(fixes)))


def make_data_dirs():
    for rel in DATA_DIRS:
        path = HERE / rel
        if not path.exists():
            path.mkdir(parents=True, mode=0o750)
    os.chmod(HERE / "data", 0o750)


def parser():
    p = argparse.ArgumentParser(description="Generate configuration for the OCS-in-a-Kit bundle.",
                                epilog="Re-running keeps secrets and passwords. Options are remembered in .env.")
    p.add_argument("--domain", help="base domain (default: %s, a placeholder)" % PLACEHOLDER_DOMAIN)
    p.add_argument("--kit", help="vanilla, education (default) or business")
    p.add_argument("--admin-email", help="default: admin@<domain>")
    p.add_argument("--school-name", help="default: OCS Kit")
    p.add_argument("--profile", help="settings from profiles/<name>.json, e.g. cockpit; 'none' to leave one")
    p.add_argument("--host-prefix", help="prefix for hostnames, e.g. kit- gives kit-flask.<domain>")
    p.add_argument("--pages-url", help="website URL when it is not https://<prefix>pages.<domain>, "
                                       "e.g. https://org.github.io/site (added to CORS)")
    p.add_argument("--cookie-domain", help="login cookie domain, e.g. .myschool.org; 'none' = host-only")
    p.add_argument("--project", help="compose project name (default: %s)" % DEFAULT_PROJECT)
    p.add_argument("--image-tag", help="image version (default: latest)")
    p.add_argument("--flask-port", help="host port for Flask (default 8587, or FLASK_HOST_PORT)")
    p.add_argument("--spring-port", help="host port for Spring (default 8585, or SPRING_HOST_PORT)")
    p.add_argument("--spring-ws-port", help="host port for the Spring websocket (default 8589, or SPRING_WS_HOST_PORT)")
    p.add_argument("--socket-port", help="host port for Socket.IO (default 8500, or SOCKET_HOST_PORT)")
    p.add_argument("--check-ports", action="store_true", help="list what is listening and exit (changes nothing); "
                                                              "exit code 1 on a conflict")
    return p


def main(argv=None):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        fail("do not run this as root. Use a normal user in the docker group.")
    args = parser().parse_args(argv)
    env_path = HERE / ".env"
    old_compose_text = read_text(env_path)
    cfg = resolve(args, parse_env(old_compose_text))

    if args.check_ports:
        conflicts = check_ports(cfg)
        print("%d conflict(s)." % conflicts)
        return 1 if conflicts else 0

    found = foreign_data()
    if found:
        fail(foreign_data_message(found))

    flask_path, spring_path = HERE / "flask.env", HERE / "spring.env"
    flask_text = read_text(flask_path) or read_text(HERE / "templates/flask.env.example")
    spring_text = read_text(spring_path) or read_text(HERE / "templates/spring.env.example")
    kept = flask_path.exists() or spring_path.exists()
    flask, spring = backend_values(cfg, parse_env(read_text(flask_path)), parse_env(read_text(spring_path)))
    for key in ("ADMIN_PASSWORD", "TEACHER_PASSWORD", "DEFAULT_PASSWORD"):
        problems = password_problems(flask[key])
        if problems:
            print("WARNING: %s needs %s, or Spring refuses to seed." % (key, ", ".join(problems)))

    make_data_dirs()
    write_private(env_path, set_env_values(old_compose_text or COMPOSE_ENV_HEADER, compose_env_values(cfg)))
    write_private(flask_path, set_env_values(flask_text, flask))
    write_private(spring_path, set_env_values(spring_text, spring))
    write_private(HERE / "CREDENTIALS.txt", credentials_text(cfg, flask))
    for name, text in render_nginx(cfg).items():
        write_private(HERE / "out/nginx" / name, text, 0o644)
    write_private(HERE / "out/next-steps.txt", next_steps_text(cfg), 0o644)
    os.chmod(HERE / "out", 0o755)
    os.chmod(HERE / "out/nginx", 0o755)

    print("%s .env, flask.env, spring.env, CREDENTIALS.txt, out/ (secrets and passwords %s)."
          % ("Updated" if kept else "Wrote", "kept" if kept else "generated"))
    print("Project %s, kit %s, image tag %s%s" % (cfg["project"], cfg["kit"], cfg["tag"],
                                                 ", profile " + cfg["profile"] if cfg["profile"] else ""))
    print("Hosts: %s" % ", ".join(cfg["hosts"].values()))
    print("Ports on 127.0.0.1: %s" % ", ".join("%s %d" % kv for kv in cfg["ports"].items()))
    print("Cookies: %s, domain %s" % (", ".join(cfg["cookies"].values()), cfg["cookie_domain"] or "host-only"))
    if cfg["target_dir"] and Path(cfg["target_dir"]).expanduser().resolve() != HERE:
        print("NOTE: profile %s expects to live in %s; this is %s." % (cfg["profile"], cfg["target_dir"], HERE))
    if cfg["domain"] == PLACEHOLDER_DOMAIN:
        print("WARNING: %s is a placeholder. Before going live: ./setup.py --domain yourdomain" % PLACEHOLDER_DOMAIN)
    print("Next: ./setup.py --check-ports, then see out/next-steps.txt and DEPLOY.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
