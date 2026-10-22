"""Tests for ocs_kit_installer.py (stdlib unittest).

Run:  python3 -m unittest discover -s tests -v

Logic tests use a small fixture catalog with made-up feature ids so they don't break when the
real kits/catalog.json changes; separate tests check the real catalog is consistent.
"""

import base64
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ocs_kit_installer as ki  # noqa: E402

FIXTURE = {
    "version": 1,
    "categories": {"core": {"name": "Core", "description": "Always on."},
                   "general": {"name": "General", "description": "For anyone."},
                   "school": {"name": "School", "description": "Classroom only."}},
    "labels": {"needs-api-key": "Needs a key.", "privacy-sensitive": "Careful.", "safe": "Fine."},
    "features": {
        "base": {"name": "Base", "always_on": True, "category": "core", "labels": ["safe"]},
        "alpha": {"name": "Alpha", "category": "general", "labels": ["safe"]},
        "beta": {"name": "Beta", "category": "general", "labels": ["needs-api-key"]},
        "gamma": {"name": "Gamma", "category": "school", "labels": []},
        "delta": {"name": "Delta", "category": "general", "labels": ["privacy-sensitive"]},
    },
    "kits": {
        "small": {"name": "Small Kit", "features": ["base"]},
        "class": {"name": "Class Kit", "features": ["base", "alpha", "gamma"]},
        "all": {"name": "All Kit", "features": ["*"]},
    },
}

FLASK_ENV_EXAMPLE = """# Flask
SCHOOL_NAME=
SITE_DOMAIN=
FRONTEND_URL=
CORS_ORIGINS=
COOKIE_DOMAIN=
IS_PRODUCTION=false
SECRET_KEY=
RESET_TOKEN_SECRET=
DEFAULT_PASSWORD=
ADMIN_PASSWORD=
TEACHER_PASSWORD=
GEMINI_API_KEY=
"""

SPRING_ENV_EXAMPLE = """# Spring
SCHOOL_NAME=My School CS
FRONTEND_URL=http://localhost:4500
SITE_DOMAIN=api.example.org
CORS_ORIGINS=http://localhost:4500
COOKIE_DOMAIN=
JWT_SECRET=
RESET_TOKEN_SECRET=
KIT_DEV_MODE=false
ADMIN_EMAIL=admin@example.org
ADMIN_PASSWORD=
TEACHER_PASSWORD=
DEFAULT_PASSWORD=
IS_PRODUCTION=false
# ---------- Local HTTP development ----------
# jwt.cookie.secure=false
# jwt.cookie.same-site=Lax
# server.servlet.session.cookie.secure=false
# server.servlet.session.cookie.same-site=lax
"""

CONFIG_YML = """# Site settings
title: Your School Coding
description: "Your School computer science pages"
owner_name: Your School
github_username: your-github-username
github_repo: "your-repo-name"
baseurl: ""

# OCS-in-a-Kit settings
kit:
  school_name: "Your School"
  schools: []            # optional list
  backend:
    flask: "https://flask.example.com"
    spring: "https://spring.example.com"
    flask_local: "http://localhost:8587"
    spring_local: "http://localhost:8585"
  voicerss_api_key: ""     # text-to-speech
  features: []           # filled by presets later

# Build settings
remote_theme: jekyll/minima
plugins:
  - jekyll-remote-theme
"""


def good_answers(**kw):
    a = ki.normalize_answers({"kit": "class", "school_name": "Test High", "admin_email": "admin@test.org",
                              "install_dir": "/tmp/x"}, env={})
    a.update(kw)
    return a


class CatalogTests(unittest.TestCase):
    cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))

    def test_preset_includes_always_on_and_star(self):
        self.assertEqual(ki.preset_features(self.cat, "small"), ["base"])
        self.assertEqual(ki.preset_features(self.cat, "all"), ["base", "alpha", "beta", "gamma", "delta"])

    def test_resolve_overrides(self):
        self.assertEqual(ki.resolve_features(self.cat, "class", "+beta,-gamma"), ["base", "alpha", "beta"])
        self.assertEqual(ki.resolve_features(self.cat, "class", ["+delta"]), ["base", "alpha", "gamma", "delta"])
        self.assertEqual(ki.resolve_features(self.cat, "class", ""), ["base", "alpha", "gamma"])

    def test_resolve_explicit_and_star(self):
        self.assertEqual(ki.resolve_features(self.cat, "small", "beta, alpha"), ["base", "alpha", "beta"])
        self.assertEqual(ki.resolve_features(self.cat, "small", "*"), ki.feature_ids(self.cat))

    def test_resolve_errors(self):
        with self.assertRaises(ki.CatalogError):
            ki.resolve_features(self.cat, "class", "+alpha,beta")       # mixed styles
        with self.assertRaises(ki.CatalogError):
            ki.resolve_features(self.cat, "class", "+nope")
        with self.assertRaises(ki.CatalogError):
            ki.resolve_features(self.cat, "class", "-base")             # always on
        with self.assertRaises(ki.CatalogError):
            ki.preset_features(self.cat, "missing")

    def test_overrides_roundtrip(self):
        ov = ki.compute_overrides(self.cat, "class", ["alpha", "beta"])
        self.assertEqual(ov, ["+beta", "-gamma"])
        self.assertEqual(ki.resolve_features(self.cat, "class", ov), ["base", "alpha", "beta"])
        self.assertEqual(ki.compute_overrides(self.cat, "class", ki.preset_features(self.cat, "class")), [])
        # explicit list normalizes to overrides
        self.assertEqual(ki.normalize_overrides(self.cat, "small", "alpha"), ["+alpha"])

    def test_describe(self):
        self.assertEqual(ki.describe_selection(self.cat, "class", ["+beta", "-gamma"]),
                         "Class Kit + Beta − Gamma")
        self.assertEqual(ki.features_env_value(["+beta", "-gamma"]), "+beta,-gamma")

    def test_groups_follow_category_order(self):
        groups = ki.feature_groups(self.cat)
        self.assertEqual([g["id"] for g, _ in groups], ["core", "general", "school"])
        self.assertEqual(groups[1][1], ["alpha", "beta", "delta"])
        self.assertEqual(groups[2][0]["description"], "Classroom only.")

    def test_notes_and_api_key(self):
        notes = ki.selection_notes(self.cat, "small", ["+gamma", "+delta"])
        self.assertTrue(any("Gamma is a School feature" in n for n in notes))
        self.assertTrue(any("privacy-sensitive" in n and "Delta" in n for n in notes))
        self.assertEqual(ki.needs_api_key(self.cat, "small", []), [])
        self.assertEqual(ki.needs_api_key(self.cat, "small", ["+beta"]), ["beta"])
        info = ki.selection_info(self.cat, "all", "-beta")
        self.assertEqual(info["api_key_features"], [])
        self.assertEqual(info["overrides"], ["-beta"])

    def test_invalid_catalog(self):
        bad = json.loads(json.dumps(FIXTURE))
        bad["kits"]["small"]["features"].append("unknown")
        with self.assertRaises(ki.CatalogError):
            ki.validate_catalog(bad)

    def test_list_features_output(self):
        text = ki.format_feature_list(self.cat)
        self.assertIn("School features - Classroom only.", text)
        self.assertIn("labels: needs-api-key", text)


class RealCatalogTests(unittest.TestCase):
    def test_real_catalog_is_consistent(self):
        cat = ki.load_catalog(str(ROOT / "kits" / "catalog.json"))
        cats = cat.get("categories") or {}
        labels = cat.get("labels") or {}
        for fid, f in cat["features"].items():
            if cats:
                self.assertIn(f.get("category"), cats, fid)
            for label in f.get("labels") or []:
                self.assertIn(label, labels, "%s: unknown label %s" % (fid, label))
        for kit in cat["kits"]:
            sel = ki.preset_features(cat, kit)
            self.assertTrue(set(ki.always_on_features(cat)) <= set(sel))
            self.assertEqual(ki.compute_overrides(cat, kit, sel), [])

    def test_installer_loads_catalog_next_to_script(self):
        self.assertIn("kits", ki.load_catalog())


class PasswordTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(ki.validate_password("Abcdef1!"), [])
        self.assertTrue(ki.validate_password("short1!"))
        self.assertIn("an uppercase letter", ki.validate_password("abcdefg1!"))
        self.assertIn("a symbol from " + ki.SPRING_SYMBOLS, ki.validate_password("Abcdefg12"))
        self.assertTrue(any("not allowed" in p for p in ki.validate_password("Abcdef1!$")))
        self.assertTrue(any("not allowed" in p for p in ki.validate_password("Abc def1!")))

    def test_generation(self):
        seen = set()
        for _ in range(300):
            pw = ki.generate_password()
            self.assertEqual(ki.validate_password(pw), [], pw)
            self.assertNotIn("$", pw)
            seen.add(pw)
        self.assertEqual(len(seen), 300)

    def test_secrets(self):
        jwt = ki.generate_secret("JWT_SECRET")
        self.assertGreaterEqual(len(base64.b64decode(jwt)), 32)
        self.assertEqual(len(ki.generate_secret("SECRET_KEY")), 64)


class EnvTests(unittest.TestCase):
    def test_set_env_values(self):
        text = "A=1\n#B=2\n# C = 3\nD=4\n"
        out = ki.set_env_values(text, {"A": "x", "B": "y", "C": "z", "E": "new"})
        self.assertEqual(out, "A=x\nB=y\nC=z\nD=4\nE=new\n")

    def test_prefers_uncommented_and_keeps_special_chars(self):
        text = "# KEY=doc example\nKEY=old\n"
        out = ki.set_env_values(text, {"KEY": r"a\1&b\g<0>"})
        self.assertEqual(out, "# KEY=doc example\nKEY=a\\1&b\\g<0>\n")

    def test_append_without_trailing_newline(self):
        self.assertEqual(ki.set_env_values("A=1", {"B": "2"}), "A=1\nB=2\n")

    def test_dotted_keys_and_comment(self):
        out = ki.apply_env("spring", SPRING_ENV_EXAMPLE, {}, local=True)
        for k, v in ki.SPRING_LOCAL_COOKIE_LINES:
            self.assertIn("\n%s=%s\n" % (k, v), out)
        prod = ki.apply_env("spring", out, {}, local=False)
        for k, v in ki.SPRING_LOCAL_COOKIE_LINES:
            self.assertIn("\n# %s=%s\n" % (k, v), prod)
            self.assertNotIn(k, ki.parse_env(prod))

    def test_parse_env(self):
        env = ki.parse_env("A=1\n# B=2\nC='x y'\nD=\"q\"\n\nE=a=b\n")
        self.assertEqual(env, {"A": "1", "C": "x y", "D": "q", "E": "a=b"})

    def test_build_env_fresh_local(self):
        cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))
        a = good_answers(preview_port=14500)
        shared = {}
        f = ki.build_env_values("flask", a, ["+beta"], {}, shared)
        s = ki.build_env_values("spring", a, ["+beta"], {}, shared)
        self.assertEqual(f["RESET_TOKEN_SECRET"], s["RESET_TOKEN_SECRET"])
        self.assertEqual(f["ADMIN_PASSWORD"], s["ADMIN_PASSWORD"])
        self.assertEqual(ki.validate_password(f["DEFAULT_PASSWORD"]), [])
        self.assertEqual(f["KIT"], "class")
        self.assertEqual(f["FEATURES"], "+beta")
        self.assertEqual(f["IS_PRODUCTION"], "false")
        self.assertEqual(f["COOKIE_DOMAIN"], "")
        self.assertIn("http://localhost:14500", f["CORS_ORIGINS"])
        self.assertEqual(f["FRONTEND_URL"], "http://localhost:14500")
        self.assertEqual(s["ADMIN_EMAIL"], "admin@test.org")
        self.assertIn("JWT_SECRET", s)
        self.assertNotIn("GEMINI_API_KEY", f)  # blank key never wipes an existing one
        del cat

    def test_build_env_production(self):
        a = good_answers(mode="production", domain="myschool.org", github_owner="MySchool")
        f = ki.build_env_values("flask", a, [], {}, {})
        s = ki.build_env_values("spring", a, [], {}, {})
        self.assertEqual(f["CORS_ORIGINS"], "https://pages.myschool.org,https://myschool.github.io")
        self.assertEqual(f["COOKIE_DOMAIN"], ".myschool.org")
        self.assertEqual(f["SITE_DOMAIN"], "myschool.org")
        self.assertEqual(s["SITE_DOMAIN"], "spring.myschool.org")
        self.assertEqual(f["FRONTEND_URL"], "https://pages.myschool.org")
        self.assertEqual(f["IS_PRODUCTION"], "true")

    def test_preserve_on_rerun(self):
        existing = {"SECRET_KEY": "keep-me", "RESET_TOKEN_SECRET": "rts", "ADMIN_PASSWORD": "Old1!pass",
                    "TEACHER_PASSWORD": "Teach1!x", "DEFAULT_PASSWORD": "Def1!xyz"}
        a = good_answers(admin_password="New1!pass")
        v = ki.build_env_values("flask", a, [], existing, {})
        self.assertEqual(v["SECRET_KEY"], "keep-me")
        self.assertEqual(v["RESET_TOKEN_SECRET"], "rts")
        self.assertEqual(v["ADMIN_PASSWORD"], "New1!pass")     # typed wins
        self.assertEqual(v["TEACHER_PASSWORD"], "Teach1!x")    # blank keeps existing
        self.assertEqual(v["DEFAULT_PASSWORD"], "Def1!xyz")


class InstallerConfigureTests(unittest.TestCase):
    """step_configure_backend on disk: fresh install, then a re-run keeps secrets."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for kind, text in (("flask", FLASK_ENV_EXAMPLE), ("spring", SPRING_ENV_EXAMPLE)):
            (self.root / kind).mkdir()
            (self.root / kind / ".env.example").write_text(text)
        self.cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kw):
        a = good_answers(install_dir=str(self.root), **kw)
        return ki.Installer(a, self.cat, repo_base=str(self.root), ref="main")

    def test_fresh_then_rerun(self):
        self.make(features=["+beta"]).step_configure_backend()
        f1 = ki.parse_env((self.root / "flask" / ".env").read_text())
        s1 = ki.parse_env((self.root / "spring" / ".env").read_text())
        self.assertEqual(oct((self.root / "flask" / ".env").stat().st_mode & 0o777), "0o600")
        self.assertEqual(f1["RESET_TOKEN_SECRET"], s1["RESET_TOKEN_SECRET"])
        self.assertEqual(s1["jwt.cookie.secure"], "false")
        self.assertEqual(s1["FEATURES"], "+beta")
        self.assertEqual(f1["COMPOSE_PROJECT_NAME"], "ocskit-flask")
        # user edits something unrelated; re-run with a new teacher password and another kit
        p = self.root / "flask" / ".env"
        p.write_text(p.read_text() + "MY_CUSTOM=1\n")
        inst = self.make(kit="all", features=["-delta"], teacher_password="Brand1!new")
        inst.step_configure_backend()
        f2 = ki.parse_env(p.read_text())
        s2 = ki.parse_env((self.root / "spring" / ".env").read_text())
        for k in ("SECRET_KEY", "RESET_TOKEN_SECRET", "ADMIN_PASSWORD", "DEFAULT_PASSWORD"):
            self.assertEqual(f1[k], f2[k], k)
        self.assertEqual(s1["JWT_SECRET"], s2["JWT_SECRET"])
        self.assertEqual(f2["TEACHER_PASSWORD"], "Brand1!new")
        self.assertEqual(f2["MY_CUSTOM"], "1")
        self.assertEqual((f2["KIT"], f2["FEATURES"]), ("all", "-delta"))
        self.assertIn("Brand1!new", inst.credentials_text())

    def test_change_features_only_touches_kit(self):
        self.make().step_configure_backend()
        before = (self.root / "flask" / ".env").read_text()
        a = good_answers(install_dir=str(self.root), action="change-features", kit="small",
                         features=["+alpha"])
        inst = ki.Installer(a, self.cat, repo_base=str(self.root), ref="main")
        inst.compose = lambda *x, **k: (0, "")
        inst.wait_http = lambda *x, **k: None
        inst.step_change_features()
        after = (self.root / "flask" / ".env").read_text()
        diff = [l for l in after.splitlines() if l not in before.splitlines()]
        self.assertEqual(sorted(diff), ["FEATURES=+alpha", "KIT=small"])


class NginxTests(unittest.TestCase):
    def test_render(self):
        flask_tpl = "server_name flask.${SITE_DOMAIN};\nproxy_pass http://localhost:${FLASK_PORT};\n" \
                    "location /socket.io/ { proxy_pass http://localhost:${SOCKET_PORT}; }\n" \
                    "proxy_set_header Host $host; ${UNKNOWN}\n"
        spring_tpl = "server_name ${SITE_DOMAIN};\nproxy_set_header Upgrade $http_upgrade;\n"
        out = ki.render_nginx_configs(flask_tpl, spring_tpl, "myschool.org")
        self.assertIn("server_name flask.myschool.org;", out["flask.conf"])
        self.assertIn("localhost:8587;", out["flask.conf"])
        self.assertIn("localhost:8500;", out["flask.conf"])
        self.assertIn("$host; ${UNKNOWN}", out["flask.conf"])
        self.assertIn("server_name spring.myschool.org;", out["spring.conf"])
        self.assertIn("$http_upgrade", out["spring.conf"])

    def test_real_templates_if_present(self):
        base = ROOT.parent
        ft, st = base / "OCS-Kit-Flask/deploy/nginx.conf.template", base / "OCS-Kit-Spring/deploy/nginx.conf.template"
        if not (ft.exists() and st.exists()):
            self.skipTest("backend repos not next to this repo")
        out = ki.render_nginx_configs(ft.read_text(), st.read_text(), "example.edu")
        for text in out.values():
            self.assertNotRegex(text, r"\$\{(SITE_DOMAIN|FLASK_PORT|SOCKET_PORT)\}")
        self.assertIn("server_name flask.example.edu;", out["flask.conf"])
        self.assertIn("server_name spring.example.edu;", out["spring.conf"])

    def test_commands_and_dns(self):
        cmds = ki.nginx_commands("/srv/ocs/nginx", "myschool.org")
        self.assertTrue(any("certbot --nginx -d flask.myschool.org -d spring.myschool.org" in c for c in cmds))
        a = good_answers(mode="production", domain="myschool.org", website_publish=True, github_owner="Org")
        names = [r[1] for r in ki.dns_records(a)]
        self.assertEqual(names, ["flask.myschool.org", "spring.myschool.org", "pages.myschool.org"])


class ConfigYmlTests(unittest.TestCase):
    def test_edit(self):
        out = ki.edit_config_yml(CONFIG_YML, top={"title": "Test High", "github_repo": "site"},
                                 kit={"school_name": "Test High", "name": "class", "features": ["+beta", "-gamma"]},
                                 backend={"flask": "https://flask.t.org", "flask_local": "http://localhost:8587"})
        self.assertIn('title: "Test High"\n', out)
        self.assertIn('github_repo: "site"\n', out)
        self.assertIn('  name: "class"\n', out)
        self.assertIn('  features: ["+beta", "-gamma"]           # filled by presets later\n', out)
        self.assertIn('    flask: "https://flask.t.org"\n', out)
        self.assertIn('    spring: "https://spring.example.com"\n', out)
        self.assertIn('  voicerss_api_key: ""     # text-to-speech\n', out)
        self.assertIn("plugins:\n  - jekyll-remote-theme\n", out)
        self.assertIn("\n# Build settings\nremote_theme", out)
        self.assertEqual(out.count("features:"), 1)
        # idempotent
        again = ki.edit_config_yml(out, top={"title": "Test High", "github_repo": "site"},
                                   kit={"school_name": "Test High", "name": "class", "features": ["+beta", "-gamma"]},
                                   backend={"flask": "https://flask.t.org", "flask_local": "http://localhost:8587"})
        self.assertEqual(out, again)

    def test_multiline_list_and_missing_block(self):
        text = "title: x\nkit:\n  features:\n    - a\n    - b\n  name: old\nother: 1\n"
        out = ki.edit_config_yml(text, kit={"name": "new", "features": []})
        self.assertEqual(out, 'title: x\nkit:\n  features: []\n  name: "new"\nother: 1\n')
        out2 = ki.edit_config_yml("title: x\n", kit={"name": "k"}, backend={"flask": "u"})
        self.assertIn('kit:\n  name: "k"\n  backend:\n    flask: "u"\n', out2)

    def test_real_config_if_present(self):
        p = ROOT.parent / "OCS-Kit-Pages" / "_config.yml"
        if not p.exists():
            self.skipTest("pages repo not next to this repo")
        s = ki.pages_config_settings(good_answers(), ["+beta"], "preview")
        out = ki.edit_config_yml(p.read_text(), s["top"], s["kit"], s["backend"])
        kit_block = out[out.index("\nkit:"):]
        self.assertIn('  name: "class"', kit_block)
        self.assertIn('  features: ["+beta"]', kit_block)
        self.assertEqual(len(out.splitlines()) - len(p.read_text().splitlines()) in (0, 1), True)


class AnswerTests(unittest.TestCase):
    cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))

    def test_valid(self):
        self.assertEqual(ki.validate_answers(good_answers(), self.cat), {})

    def test_errors(self):
        a = good_answers(mode="production", domain="https://bad", admin_email="nope", admin_password="weak",
                         school_name="A $chool", install_website=True, website_preview=False, website_publish=False)
        e = ki.validate_answers(ki.normalize_answers(a, env={}), self.cat)
        for k in ("domain", "admin_email", "admin_password", "school_name", "website_preview"):
            self.assertIn(k, e)
        e2 = ki.validate_answers(good_answers(kit="nope"), self.cat)
        self.assertIn("kit", e2)
        e3 = ki.validate_answers(good_answers(features=["+zzz"]), self.cat)
        self.assertIn("features", e3)
        e4 = ki.validate_answers(good_answers(install_backend=False, install_website=False), self.cat)
        self.assertIn("install_backend", e4)
        e5 = ki.validate_answers(good_answers(preview_port=8587), self.cat)
        self.assertIn("preview_port", e5)

    def test_normalize(self):
        a = ki.normalize_answers({"domain": "https://MySchool.org/", "features": "+a, -b", "install_website": "no"},
                                 env={"OCS_ADMIN_PASSWORD": "Env1!pass"})
        self.assertEqual(a["domain"], "myschool.org")
        self.assertEqual(a["features"], ["+a", "-b"])
        self.assertFalse(a["website_preview"])
        self.assertEqual(a["admin_password"], "Env1!pass")
        saved = ki.saved_answers(a)
        self.assertNotIn("admin_password", saved)
        self.assertNotIn("gemini_api_key", saved)

    def test_warnings(self):
        self.assertTrue(ki.website_warnings(good_answers(website_publish=True)))
        self.assertFalse(ki.website_warnings(good_answers(mode="production", domain="a.org", website_publish=True)))


class DryRunTests(unittest.TestCase):
    def test_publish_dry_run_commands(self):
        cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))
        with tempfile.TemporaryDirectory() as tmp:
            a = good_answers(install_dir=tmp, mode="production", domain="myschool.org", install_backend=True,
                             website_preview=False, website_publish=True, github_owner="my-org",
                             github_repo="cs-site")
            inst = ki.Installer(a, cat, dry_run=True)
            self.assertTrue(inst.run())
            log = "\n".join(inst.lines)
            self.assertEqual(os.listdir(tmp), [])  # nothing written
            for expected in ["gh repo fork CSA-Admin-OCS/OCS-Kit-Pages --clone=false --default-branch-only "
                             "--fork-name cs-site --org my-org",
                             "gh api -X PUT repos/my-org/cs-site/actions/permissions -F enabled=true",
                             "gh api -X POST repos/my-org/cs-site/pages -f build_type=workflow",
                             "cname=pages.myschool.org",
                             "gh workflow run jekyll-gh-pages.yml -R my-org/cs-site --ref main",
                             "git push --force origin HEAD:main",
                             "docker compose up -d --build"]:
                self.assertIn(expected, log)
            self.assertIsNotNone(inst.summary["production"])
            self.assertIn("pages.myschool.org", [r["name"] for r in inst.summary["production"]["dns"]])


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cat = ki.load_catalog()
        args = SimpleNamespace(ref="main", dry_run=True, repo_base=str(ROOT.parent), port=0, echo_log=False)
        answers = ki.normalize_answers({"install_dir": cls.tmp.name}, env={})
        cls.state = ki.GuiState(cls.cat, answers, args)
        cls.srv = ki.make_gui_server(cls.state, 0)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def req(self, path, body=None, token=True, host=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        if token:
            url += ("&" if "?" in path else "?") + "t=" + self.state.token
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                   headers={"Content-Type": "application/json"})
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_token_enforced(self):
        self.assertEqual(self.req("/", token=False)[0], 403)
        self.assertEqual(self.req("/api/state", token=False)[0], 403)
        self.assertEqual(self.req("/api/validate", {}, token=False)[0], 403)
        self.assertEqual(self.req("/?t=wrong", token=False)[0], 403)
        self.assertEqual(self.req("/", host="evil.example:%d" % self.port)[0], 403)  # DNS rebinding

    def test_page_and_state(self):
        code, html = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn("OCS-in-a-Kit installer", html)
        self.assertIn(self.state.token, html)
        self.assertNotIn("https://cdn", html)
        code, body = self.req("/api/state")
        st = json.loads(body)
        self.assertEqual(set(st["catalog"]["kits"]), set(self.cat["kits"]))
        self.assertEqual([g["id"] for g in st["groups"]], [g["id"] for g, _ in ki.feature_groups(self.cat)])
        self.assertTrue(st["dry_run"])

    def test_validation_errors(self):
        code, body = self.req("/api/validate", {"kit": "nope", "admin_email": "x", "admin_password": "weak$",
                                                 "mode": "production", "domain": ""})
        e = json.loads(body)["errors"]
        for k in ("kit", "admin_email", "admin_password", "domain", "school_name"):
            self.assertIn(k, e)
        code, body = self.req("/api/install", {"kit": "nope"})
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(self.req("/api/nothing")[0], 404)

    def test_selection(self):
        kit = next(iter(self.cat["kits"]))
        extra = [f for f in ki.feature_ids(self.cat) if f not in ki.preset_features(self.cat, kit)][:1]
        code, body = self.req("/api/selection", {"kit": kit, "features": ["+" + f for f in extra]})
        r = json.loads(body)
        self.assertEqual(r["overrides"], ["+" + f for f in extra])
        self.assertTrue(r["summary"].startswith(self.cat["kits"][kit]["name"]))
        self.assertEqual(self.req("/api/selection", {"kit": "nope"})[0], 400)

    def test_dry_run_install_via_gui(self):
        kit = next(iter(self.cat["kits"]))
        a = {"kit": kit, "school_name": "GUI High", "admin_email": "a@b.org", "install_dir": self.tmp.name,
             "install_backend": True, "install_website": True, "website_preview": True, "preview_port": 14501}
        code, body = self.req("/api/install", a)
        self.assertTrue(json.loads(body)["ok"], body)
        deadline = time.time() + 30
        while time.time() < deadline:
            p = json.loads(self.req("/api/progress?since=0")[1])
            if p["done"]:
                break
            time.sleep(0.2)
        self.assertTrue(p["done"])
        self.assertTrue(p["ok"], "\n".join(p["lines"]))
        self.assertTrue(all(s["status"] == "done" for s in p["steps"]))
        self.assertIn("[dry-run] $ docker compose up -d --build", "\n".join(p["lines"]))
        self.assertEqual(os.listdir(self.tmp.name), [])


if __name__ == "__main__":
    unittest.main()
