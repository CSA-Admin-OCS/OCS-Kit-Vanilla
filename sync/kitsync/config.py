"""kit.sync.json: how one kit repo relates to its upstream repo.

Keys (all optional except ``upstream.url``):

  name          short repo name ("pages", "flask", "spring")
  upstream      {"url": "...", "branch": "main"}
  catalog       {"file": "kits/catalog.json", "key": "pages"}  -- where features list their paths
  map           [{"upstream": "media/", "kit": "features/media-literacy/"}]  -- moved paths
                (trailing '/' = directory prefix, otherwise an exact file)
  exclude       globs (upstream paths) never brought into the kit
  protect       globs (kit paths) owned by the kit and never overwritten by import/update
  replace       [{"id", "pattern", "replacement", "regex": true|false, "files": [globs], "ignore_case"}]
                applied in order to every text file copied from upstream
  generated     [{"prefix": "assets/js/projects/", "source": "_projects/*/{name}"}]
                outputs built from another path; a reference to them is satisfied by the source
  scrub         {"max_file_kb", "allow": [...], "allowlist_file", "skip": [globs],
                 "denylist": [{"id", "regex"}], "pii_min_records"}
  validators    commands run in the kit repo after an import (skipped if their script is missing)
  tests         commands run in the kit repo after an import (skipped if the path is missing)
  post_import   {"makeprojects": true}  -- register imported _projects/<cat>/<name> in _projects/.makeprojects
"""

import json
import os
import re

from . import globs

DEFAULTS = {
    "name": "",
    "upstream": {"url": "", "branch": "main"},
    "catalog": {"file": "kits/catalog.json", "key": "pages"},
    "map": [],
    "exclude": [],
    "protect": [],
    "replace": [],
    "generated": [],
    "scrub": {},
    "validators": [],
    "tests": [],
    "post_import": {},
}

# Never copied, never scanned: VCS/build/vendor output.
ALWAYS_EXCLUDE = [".git", "/_site", ".jekyll-cache", ".sass-cache", "node_modules", "/venv", "/.venv",
                  "/vendor", "__pycache__", "*.pyc", ".DS_Store"]


class ConfigError(Exception):
    pass


class SyncConfig(object):
    def __init__(self, data, path=None):
        merged = dict(DEFAULTS)
        merged.update(data or {})
        self.data = merged
        self.path = path
        up = merged.get("upstream") or {}
        if isinstance(up, str):
            up = {"url": up}
        self.upstream_url = up.get("url", "")
        self.upstream_branch = up.get("branch", "main")
        cat = merged.get("catalog") or {}
        self.catalog_file = cat.get("file", "kits/catalog.json")
        self.catalog_key = cat.get("key", merged.get("name") or "pages")
        self.name = merged.get("name") or self.catalog_key
        self.exclude = globs.PathSet(list(merged.get("exclude") or []) + ALWAYS_EXCLUDE)
        self.protect = globs.PathSet(merged.get("protect") or [])
        self.maps = []
        for m in merged.get("map") or []:
            if "upstream" not in m or "kit" not in m:
                raise ConfigError("map entries need 'upstream' and 'kit': %r" % (m,))
            self.maps.append((m["upstream"], m["kit"]))
        # longest prefix first
        self.maps.sort(key=lambda t: -len(t[0]))
        self.replace = [ReplaceRule(r) for r in merged.get("replace") or []]
        self.generated = list(merged.get("generated") or [])
        self.scrub = dict(merged.get("scrub") or {})
        self.validators = list(merged.get("validators") or [])
        self.tests = list(merged.get("tests") or [])
        self.post_import = dict(merged.get("post_import") or {})

    # ---- path mapping -------------------------------------------------
    def to_kit(self, upath):
        for up, kit in self.maps:
            if up.endswith("/"):
                if upath.startswith(up):
                    return kit + upath[len(up):]
            elif upath == up:
                return kit
        return upath

    def to_upstream(self, kpath):
        for up, kit in sorted(self.maps, key=lambda t: -len(t[1])):
            if kit.endswith("/"):
                if kpath.startswith(kit):
                    return up + kpath[len(kit):]
            elif kpath == kit:
                return up
        return kpath

    def is_excluded(self, upath):
        return self.exclude.first(upath)

    def is_protected(self, kpath):
        return self.protect.first(kpath)

    # ---- replace rules --------------------------------------------------
    def apply_replace(self, path, text):
        """Apply replace rules to ``text`` of kit path ``path``. Returns (text, [(rule_id, count)])."""
        hits = []
        for rule in self.replace:
            if not rule.applies_to(path):
                continue
            text, n = rule.apply(text)
            if n:
                hits.append((rule.id, n))
        return text, hits


class ReplaceRule(object):
    def __init__(self, d):
        if "pattern" not in d:
            raise ConfigError("replace rule needs 'pattern': %r" % (d,))
        self.id = d.get("id") or d["pattern"]
        self.files = globs.PathSet(d.get("files") or ["**"])
        flags = re.IGNORECASE if d.get("ignore_case") else 0
        if d.get("regex", False):
            self.rx = re.compile(d["pattern"], flags)
            self.replacement = d.get("replacement", "")
        else:
            self.rx = re.compile(re.escape(d["pattern"]), flags)
            self.replacement = d.get("replacement", "").replace("\\", "\\\\")
        self.description = d.get("description", "")

    def applies_to(self, path):
        return path in self.files

    def apply(self, text):
        return self.rx.subn(self.replacement, text)

    def handles(self, path, text):
        """True if this rule would rewrite something in ``text``."""
        return self.applies_to(path) and bool(self.rx.search(text))


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise ConfigError("%s: invalid JSON: %s" % (path, e))
    return SyncConfig(data, path)


def find_config(kit_path, explicit=None):
    """--config, else <kit>/kit.sync.json, else sync/configs/<name>.kit.sync.json guessed from the repo name."""
    if explicit:
        return load(explicit)
    p = os.path.join(kit_path, "kit.sync.json")
    if os.path.isfile(p):
        return load(p)
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs")
    base = os.path.basename(os.path.abspath(kit_path)).lower()
    for name in ("pages", "flask", "spring"):
        if base.endswith(name):
            cand = os.path.join(here, "%s.kit.sync.json" % name)
            if os.path.isfile(cand):
                return load(cand)
    raise ConfigError("no kit.sync.json in %s; pass --config" % kit_path)
