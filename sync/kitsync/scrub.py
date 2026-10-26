"""The scrub gate: nothing school-specific, secret or personal gets into the kit.

Checks every file it is given for:

  deny:*     school/teacher identifiers (DNHS, Del Norte, Poway, Nighthawk, jm1021, jmort, Mortensen,
             dnhsbathroom, *.opencodingsociety.com) -- in text, and in printable strings of binaries
             (image metadata such as Canva's "brand=Del Norte High").
  secret:*   API keys and credentials (Google AIza..., Slack xox?-, Groq gsk_, GitHub ghp_/github_pat_,
             AWS AKIA..., OpenAI-style sk-..., private key blocks, password/api-key literals).
  pii:*      data files that look like personal data (many e-mail addresses or GitHub handles,
             roster/grade/backup paths, databases, cookie jars).
  size:large files over scrub.max_file_kb.

False positives are silenced, after review, by
  * an allowlist entry  {"path": "<glob>", "rule": "<rule glob>", "match": "<substring>", "reason": "..."}
    in kit.sync.json scrub.allow, or a line ``<path-glob> <rule-glob> [match text]  # reason`` in the
    allowlist file (scrub.allowlist_file, default ``kit.scrub-allow``), or
  * the marker ``kit-scrub: allow`` on the offending line itself.
"""

import fnmatch
import json
import os
import re

from . import globs

DEFAULT_DENYLIST = [
    ("deny:dnhs", r"(?<![a-z0-9])dnhs"),
    ("deny:del-norte", r"del[\s_\-]?norte"),
    ("deny:poway", r"poway"),
    ("deny:nighthawk", r"nighthawk"),
    ("deny:jm1021", r"jm1021"),
    ("deny:jmort", r"jmort"),
    ("deny:mortensen", r"mortensen"),
    ("deny:ocs-domain", r"(?:[a-z0-9\-]+\.)*opencodingsociety\.com"),
]

_PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|\.+|<.*>|\{\{.*\}\}|\$\{.*\}|%\(.*\)s?|your[_\-a-z]*|change[_\-]?me.*|example.*|"
    r"password\d*|passw(or)?d|secret|test(ing)?\d*|dummy|placeholder|none|null|undefined|todo|"
    r"process\.env.*|os\.environ.*|env\(.*\)|.*\{.*\}.*)$")

SECRET_PATTERNS = [
    ("secret:google-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("secret:slack-token", re.compile(r"xox[bpoars]-[0-9A-Za-z\-]{10,}")),
    ("secret:groq-key", re.compile(r"gsk_[0-9A-Za-z]{20,}")),
    ("secret:github-token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{22,}")),
    ("secret:aws-access-key", re.compile(r"(?<![0-9A-Z])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])")),
    ("secret:openai-style-key", re.compile(r"(?<![A-Za-z0-9_\-])sk-(?:proj-|ant-|live-)?[A-Za-z0-9_\-]{20,}")),
    ("secret:private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
]

# key = "literal"  /  "key": "literal"  /  key: 'literal'
_PASSWORD_LITERAL = re.compile(
    r"""(?ix)\b([a-z0-9_\-]*pass(?:word|wd)|pwd)\b["']?\s*(?:=|:|==)\s*["']([^"'\s]{4,})["']""")
_PASSWORD_ENV = re.compile(r"(?im)^\s*(?:export\s+)?([A-Z0-9_]*PASS(?:WORD|WD)[A-Z0-9_]*)\s*=\s*([^\s#\"']{4,})")
_API_KEY_LITERAL = re.compile(
    r"""(?ix)\b([a-z0-9_\-]*(?:api[_\-]?key|apikey|secret[_\-]?key|access[_\-]?token|auth[_\-]?token|client[_\-]?secret))\b["']?\s*(?:=|:)\s*["']([A-Za-z0-9_\-\.]{16,})["']""")
_ENV_FILES = globs.PathSet([".env", ".env.*", "*.properties", "*.ini", "*.cfg", "*.conf", "*.sh", "*.toml"])
_ENV_EXAMPLE = globs.PathSet(["*.example", "*.sample", "*.template"])

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_BENIGN_EMAIL = re.compile(r"(?i)@(?:example\.(?:com|org|net)|test\.com|localhost|domain\.com|email\.com|"
                           r"users\.noreply\.github\.com|noreply\.github\.com|2x\.png|3x\.png)$|\.(?:png|jpg|gif|svg|webp)$")
_HANDLE_KEYS = re.compile(r"(?i)^(?:github(?:_?(?:id|user(?:name)?|handle))?|gh_?(?:id|user)?|uid|username|user_?name|"
                          r"login|email|student(?:_?(?:id|name))?)$")
_ROSTER_HEADER = re.compile(r"(?i)\b(?:student|github|email|uid|period|roster|first_?name|last_?name)\b")

PII_PATHS = [
    ("pii:backup", "instance/backup"),
    ("pii:backup", "**/backup/*.json"),
    ("pii:roster", "**/*roster*.{csv,json,tsv,xlsx}"),
    ("pii:roster", "**/*roster*"),
    ("pii:grades", "**/*grades*.csv"),
    ("pii:students", "**/students*.csv"),
    ("pii:students", "**/students*.xlsx"),
    ("pii:database", "*.db"),
    ("pii:database", "*.sqlite"),
    ("pii:database", "*.sqlite3"),
    ("pii:cookies", "cookies.txt"),
    ("secret:env-file", ".env"),
]

DATA_EXT = (".json", ".csv", ".tsv", ".yml", ".yaml")
DEFAULT_MAX_KB = 10240
DEFAULT_PII_MIN = 10
BINARY_STRING_MIN = 6


class Finding(object):
    __slots__ = ("path", "line", "rule", "excerpt", "message")

    def __init__(self, path, line, rule, excerpt="", message=""):
        self.path, self.line, self.rule, self.excerpt, self.message = path, line, rule, excerpt, message

    @property
    def category(self):
        return self.rule.split(":", 1)[0]

    def to_dict(self):
        return {"path": self.path, "line": self.line, "rule": self.rule, "category": self.category,
                "excerpt": self.excerpt, "message": self.message}

    def format(self):
        loc = "%s:%s" % (self.path, self.line) if self.line else self.path
        s = "%s: [%s] %s" % (loc, self.rule, self.excerpt)
        if self.message:
            s += "  -- " + self.message
        return s

    def __repr__(self):
        return "Finding(%s)" % self.format()


def _redact(value):
    if len(value) <= 8:
        return value[:2] + "..."
    return "%s...(%d chars)" % (value[:6], len(value))


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _line_text(text, pos):
    s = text.rfind("\n", 0, pos) + 1
    e = text.find("\n", pos)
    return text[s:e if e >= 0 else len(text)]


def is_binary(data):
    return b"\x00" in data[:8192]


def binary_strings(data, minimum=BINARY_STRING_MIN):
    return [m.group().decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minimum, data)]


class Allowlist(object):
    def __init__(self, entries=None):
        self.entries = []
        for e in entries or []:
            self.add(e.get("path", "**"), e.get("rule", "*"), e.get("match"), e.get("reason", ""))

    def add(self, path, rule="*", match=None, reason=""):
        self.entries.append({"path": path, "rule": rule, "match": match, "reason": reason})

    @classmethod
    def parse_file(cls, filename, into=None):
        al = into or cls()
        with open(filename, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
                if not line:
                    continue
                parts = line.split(None, 2)
                reason = raw.split(" #", 1)[1].strip() if " #" in raw else ""
                al.add(parts[0], parts[1] if len(parts) > 1 else "*", parts[2] if len(parts) > 2 else None, reason)
        return al

    def allows(self, finding, line_text=""):
        for e in self.entries:
            if not globs.match(e["path"], finding.path):
                continue
            if not fnmatch.fnmatchcase(finding.rule, e["rule"]):
                continue
            if e["match"] and e["match"] not in (finding.excerpt + " " + line_text):
                continue
            return True
        return False


class Report(object):
    def __init__(self):
        self.findings = []
        self.allowed = []
        self.files_checked = 0

    @property
    def ok(self):
        return not self.findings

    def extend(self, other):
        self.findings.extend(other.findings)
        self.allowed.extend(other.allowed)
        self.files_checked += other.files_checked

    def by_category(self):
        out = {}
        for f in self.findings:
            out[f.category] = out.get(f.category, 0) + 1
        return out

    def by_rule(self):
        out = {}
        for f in self.findings:
            out[f.rule] = out.get(f.rule, 0) + 1
        return out

    def to_dict(self):
        return {"ok": self.ok, "files_checked": self.files_checked, "allowed": len(self.allowed),
                "by_category": self.by_category(), "findings": [f.to_dict() for f in self.findings]}

    def format(self, limit=None):
        lines = []
        fs = sorted(self.findings, key=lambda f: (f.path, f.line, f.rule))
        for f in fs[:limit] if limit else fs:
            lines.append(f.format())
        if limit and len(fs) > limit:
            lines.append("... and %d more" % (len(fs) - limit))
        summary = ", ".join("%s %d" % kv for kv in sorted(self.by_category().items())) or "none"
        lines.append("scrub gate: %s -- %d file(s) checked, %d finding(s) [%s], %d allowlisted" % (
            "PASS" if self.ok else "FAIL", self.files_checked, len(self.findings), summary, len(self.allowed)))
        return "\n".join(lines)


class ScrubGate(object):
    def __init__(self, scrub_cfg=None, base_dir=None):
        cfg = dict(scrub_cfg or {})
        self.max_bytes = int(cfg.get("max_file_kb", DEFAULT_MAX_KB)) * 1024
        self.pii_min = int(cfg.get("pii_min_records", DEFAULT_PII_MIN))
        self.skip = globs.PathSet(cfg.get("skip") or [])
        deny = list(DEFAULT_DENYLIST)
        for d in cfg.get("denylist") or []:
            deny.append((d.get("id") or "deny:" + d["regex"], d["regex"]))
        self.denylist = [(rid, re.compile(rx, re.IGNORECASE)) for rid, rx in deny]
        self.allowlist = Allowlist(cfg.get("allow") or [])
        af = cfg.get("allowlist_file", "kit.scrub-allow")
        if af and base_dir:
            p = af if os.path.isabs(af) else os.path.join(base_dir, af)
            if os.path.isfile(p):
                Allowlist.parse_file(p, into=self.allowlist)

    # ---- individual checks -------------------------------------------------
    def mask(self, text, token="[masked]"):
        """Replace every denylisted term in ``text`` (used for reports that may be posted publicly)."""
        n = 0
        for _, rx in self.denylist:
            text, k = rx.subn(token, text)
            n += k
        return text, n

    def _deny(self, path, text, line_offset=None):
        out = []
        for rid, rx in self.denylist:
            for m in rx.finditer(text):
                line = line_offset if line_offset is not None else _line_of(text, m.start())
                out.append((Finding(path, line, rid, m.group(), "school/teacher-specific term"), m.start()))
        return out

    def _secrets(self, path, text, line_offset=None):
        out = []

        def add(rid, m, value, msg, pos=None):
            pos = m.start() if pos is None else pos
            line = line_offset if line_offset is not None else _line_of(text, pos)
            out.append((Finding(path, line, rid, _redact(value), msg), pos))

        for rid, rx in SECRET_PATTERNS:
            for m in rx.finditer(text):
                add(rid, m, m.group(), "looks like a credential; remove it and revoke it at the source")
        if line_offset is None:
            for m in _PASSWORD_LITERAL.finditer(text):
                val = m.group(2)
                if not _PLACEHOLDER.match(val):
                    add("secret:password-literal", m, "%s=%s" % (m.group(1), val), "hard-coded password")
            for m in _API_KEY_LITERAL.finditer(text):
                val = m.group(2)
                if not _PLACEHOLDER.match(val) and not val.startswith(("AIza", "gsk_", "ghp_", "sk-")):
                    add("secret:api-key-literal", m, "%s=%s" % (m.group(1), val), "hard-coded API key/token")
            if path in _ENV_FILES and path not in _ENV_EXAMPLE:
                for m in _PASSWORD_ENV.finditer(text):
                    if not _PLACEHOLDER.match(m.group(2)):
                        add("secret:password-literal", m, "%s=%s" % (m.group(1), m.group(2)), "password in config file")
        return out

    def _pii_data(self, path, text):
        if not path.lower().endswith(DATA_EXT):
            return []
        emails = set(e.lower() for e in EMAIL_RE.findall(text) if not _BENIGN_EMAIL.search(e))
        out = []
        if len(emails) >= self.pii_min:
            out.append(Finding(path, 0, "pii:emails", "%d distinct e-mail addresses" % len(emails),
                               "looks like personal data; do not ship real users' e-mails"))
        handles = set()
        if path.lower().endswith(".json"):
            try:
                handles = _json_handles(json.loads(text))
            except ValueError:
                handles = set()
        elif path.lower().endswith((".csv", ".tsv")):
            first = text.split("\n", 1)[0]
            rows = [r for r in text.splitlines()[1:] if r.strip()]
            if _ROSTER_HEADER.search(first) and len(rows) >= self.pii_min:
                out.append(Finding(path, 1, "pii:roster", "%d rows, header: %s" % (len(rows), first[:80]),
                                   "looks like a student roster / gradebook"))
        if len(handles) >= self.pii_min:
            out.append(Finding(path, 0, "pii:handles", "%d distinct user handles/ids" % len(handles),
                               "looks like personal data (GitHub handles / usernames)"))
        return out

    # ---- entry points --------------------------------------------------------
    def check_bytes(self, path, data):
        """Return (findings, allowed) for repo path ``path`` with content ``data``."""
        raw = []
        size = len(data)
        for rid, pat in PII_PATHS:
            if _brace_match(pat, path):
                raw.append((Finding(path, 0, rid, path, "path looks like personal data or secrets"), None, ""))
                break
        if size > self.max_bytes:
            raw.append((Finding(path, 0, "size:large", "%d KB" % (size // 1024),
                                "over the %d KB limit; host it elsewhere or shrink it" % (self.max_bytes // 1024)), None, ""))
        if is_binary(data):
            for s in binary_strings(data):
                for f, _ in self._deny(path, s, line_offset=0) + self._secrets(path, s, line_offset=0):
                    f.message = "in binary content/metadata (strip metadata: e.g. exiftool -all= or re-export)"
                    raw.append((f, None, s))
        else:
            text = data.decode("utf-8", errors="replace")
            for f, pos in self._deny(path, text) + self._secrets(path, text):
                raw.append((f, pos, _line_text(text, pos)))
            for f in self._pii_data(path, text):
                raw.append((f, None, ""))
        findings, allowed = [], []
        seen = set()
        for f, pos, line_text in raw:
            key = (f.path, f.line, f.rule, f.excerpt)
            if key in seen:
                continue
            seen.add(key)
            if "kit-scrub: allow" in line_text or self.allowlist.allows(f, line_text):
                allowed.append(f)
            else:
                findings.append(f)
        return findings, allowed

    def check_text(self, path, text):
        return self.check_bytes(path, text.encode("utf-8"))[0]

    def check_files(self, root, paths):
        rep = Report()
        for rel in paths:
            if rel in self.skip:
                continue
            full = os.path.join(root, rel)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            with open(full, "rb") as f:
                data = f.read()
            fs, al = self.check_bytes(rel, data)
            rep.findings.extend(fs)
            rep.allowed.extend(al)
            rep.files_checked += 1
        return rep

    def check_tree(self, root, paths=None):
        if paths is None:
            paths = list_repo_files(root)
        return self.check_files(root, paths)


def _brace_match(pattern, path):
    m = re.match(r"^(.*)\{([^}]*)\}(.*)$", pattern)
    if m:
        return any(globs.match(m.group(1) + alt + m.group(3), path) for alt in m.group(2).split(","))
    return globs.match(pattern, path)


def _json_handles(obj, acc=None, depth=0):
    acc = set() if acc is None else acc
    if depth > 30:
        return acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and _HANDLE_KEYS.match(str(k)) and v.strip():
                acc.add((str(k).lower(), v.strip().lower()))
            elif isinstance(v, (dict, list)):
                _json_handles(v, acc, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _json_handles(v, acc, depth + 1)
    return acc


def list_repo_files(root):
    """git-tracked files (plus untracked, non-ignored) if ``root`` is a git work tree, else a walk."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
        files = sorted(set(p for p in out.decode("utf-8", "replace").split("\0") if p))
        return [p for p in files if os.path.isfile(os.path.join(root, p))]
    except (subprocess.CalledProcessError, OSError):
        pass
    from .config import ALWAYS_EXCLUDE
    skip = globs.PathSet(ALWAYS_EXCLUDE)
    res = []
    for d, dirs, files in os.walk(root):
        reld = os.path.relpath(d, root).replace(os.sep, "/")
        reld = "" if reld == "." else reld + "/"
        dirs[:] = [x for x in dirs if (reld + x) not in skip]
        for fn in files:
            p = reld + fn
            if p not in skip:
                res.append(p)
    return sorted(res)
