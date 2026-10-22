"""Read and update a kit repo's kits/catalog.json.

Shape (shared with the canonical OCS-Kit/kits/catalog.json, plus one repo-specific key per feature):

  {"categories": {...}, "labels": {...},
   "features": {"<id>": {"name", "category", "labels", "description", "<key>": [paths...]}},
   "kits": {"vanilla": {"features": [...]}, "education": {...}, "business": {...}}}

``<key>`` is "pages" for OCS-Kit-Pages. Its value is a list of repo paths (files or directories;
globs allowed); a dict with a "paths" list is accepted too. A path is *claimed* by a feature when one
of the feature's entries equals it, is a parent directory of it, or a glob that matches it.

Kit membership of a NEW feature (import --new-feature), unless --kits overrides it:
  category education -> education kit
  category general   -> business kit
  category core      -> every kit
"""

import json
import os
import re

from . import globs

KIT_RULE = {"education": ["education"], "general": ["business"], "core": None}  # None = every kit
ID_RE = re.compile(r"^[a-z][a-z0-9\-]*$")


class CatalogError(Exception):
    pass


class ConflictError(CatalogError):
    def __init__(self, conflicts):
        self.conflicts = conflicts  # [(path, other_feature, other_entry)]
        msg = "; ".join("%s is already claimed by feature '%s' (entry %s)" % c for c in conflicts)
        CatalogError.__init__(self, msg + " -- pass --move to move it")


def _indent_of(text):
    m = re.search(r"\n( +)\"", text)
    return len(m.group(1)) if m else 2


class Catalog(object):
    def __init__(self, data, path=None, key="pages", indent=2):
        self.data = data
        self.path = path
        self.key = key
        self.indent = indent
        self.data.setdefault("features", {})

    @classmethod
    def load(cls, path, key="pages"):
        if not os.path.isfile(path):
            raise CatalogError("catalog not found: %s" % path)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        try:
            data = json.loads(text)
        except ValueError as e:
            raise CatalogError("%s: invalid JSON: %s" % (path, e))
        return cls(data, path, key, _indent_of(text))

    def save(self, path=None):
        path = path or self.path
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=self.indent, ensure_ascii=False)
            f.write("\n")

    # ---- reading ----------------------------------------------------------
    @property
    def features(self):
        return self.data["features"]

    def has(self, fid):
        return fid in self.features

    def paths(self, fid):
        v = self.features.get(fid, {}).get(self.key)
        if v is None:
            return []
        if isinstance(v, dict):
            return list(v.get("paths") or [])
        return list(v)

    def _set_paths(self, fid, paths):
        feat = self.features[fid]
        cur = feat.get(self.key)
        if isinstance(cur, dict):
            cur["paths"] = paths
        else:
            feat[self.key] = paths

    def all_entries(self):
        for fid in self.features:
            for e in self.paths(fid):
                yield fid, e

    def owners(self, path):
        """[(feature, entry)] of every catalog entry that claims ``path``."""
        return [(fid, e) for fid, e in self.all_entries() if _claims(e, path)]

    def owner(self, path):
        o = self.owners(path)
        if not o:
            return None
        # most specific entry wins
        return sorted(o, key=lambda t: -len(t[1]))[0][0]

    def files_of(self, fid, universe):
        return sorted(p for p in universe if any(_claims(e, p) for e in self.paths(fid)))

    # ---- writing ----------------------------------------------------------
    def create_feature(self, fid, name, description, category, labels=(), kits=None, canonical=None,
                       setup_summary=None, verify=None):
        if not ID_RE.match(fid):
            raise CatalogError("feature id %r must be lowercase letters, digits and '-'" % fid)
        if self.has(fid):
            raise CatalogError("feature '%s' already exists (drop --new-feature)" % fid)
        cats = (canonical or self.data).get("categories") or {}
        if category not in (cats or KIT_RULE):
            raise CatalogError("category must be one of %s" % ", ".join(sorted(cats or KIT_RULE)))
        known = (canonical or self.data).get("labels")
        if known is not None:
            bad = [l for l in labels if l not in known]
            if bad:
                raise CatalogError("unknown label(s) %s; known: %s" % (", ".join(bad), ", ".join(sorted(known))))
        if not name or not description:
            raise CatalogError("a new feature needs --name and --description")
        self.features[fid] = {"name": name, "category": category, "labels": list(labels),
                              "description": description,
                              "setup": {"summary": setup_summary or "Works out of the box once the feature is enabled.",
                                        "settings": [], "steps": [],
                                        "verify": verify or "Open the feature's pages (listed under '%s' in kits/catalog.json) "
                                                            "and check they load without console errors." % self.key},
                              self.key: []}
        kit_defs = self.data.get("kits") or {}
        if kits is None:
            rule = KIT_RULE.get(category)
            kits = list(kit_defs) if rule is None else rule
        for k in kits:
            if k not in kit_defs:
                raise CatalogError("unknown kit '%s'; known: %s" % (k, ", ".join(sorted(kit_defs))))
            fl = kit_defs[k].setdefault("features", [])
            if "*" not in fl and fid not in fl:
                fl.append(fid)
        return kits

    def add_paths(self, fid, paths, move=False):
        """Register ``paths`` under ``fid``. Returns {"added": [...], "moved": [(path, from)], "already": [...]}."""
        if not self.has(fid):
            raise CatalogError("unknown feature '%s' (use --new-feature to create it)" % fid)
        cur = self.paths(fid)
        conflicts, moves, already, added = [], [], [], []
        for p in paths:
            if any(_claims(e, p) for e in cur):
                already.append(p)
                continue
            others = [(f, e) for f, e in self.owners(p) if f != fid]
            # entries of other features that live *inside* the new path are claimed too
            inside = [(f, e) for f, e in self.all_entries() if f != fid and e != p and _claims(p, e)]
            for f, e in others + inside:
                if not move:
                    conflicts.append((p, f, e))
                elif e != p and not _claims(p, e):
                    # claimed via a parent dir / glob of another feature: cannot move a piece of it
                    conflicts.append((p, f, e))
                else:
                    moves.append((e, f))
        if conflicts:
            raise ConflictError(conflicts)
        for e, f in moves:
            self._set_paths(f, [x for x in self.paths(f) if x != e])
        for p in paths:
            if p not in already and p not in cur:
                cur.append(p)
                added.append(p)
        self._set_paths(fid, cur)
        return {"added": added, "moved": moves, "already": already}

    # ---- validation ---------------------------------------------------------
    def validate(self, root=None, universe=None, canonical=None, check_setup=True):
        """Return a list of problems (empty = valid)."""
        errs = []
        feats = self.features
        if canonical is not None:
            cf = canonical.get("features", {})
            if sorted(cf) != sorted(feats):
                missing = sorted(set(cf) - set(feats))
                extra = sorted(set(feats) - set(cf))
                errs.append("feature ids differ from canonical (missing: %s; extra: %s)" % (missing, extra))
            for fid in sorted(set(cf) & set(feats)):
                for k in ("category", "labels", "name"):
                    if k in cf[fid] and cf[fid].get(k) != feats[fid].get(k):
                        errs.append("feature '%s': %s %r differs from canonical %r" % (fid, k, feats[fid].get(k), cf[fid].get(k)))
            for k in ("categories", "labels", "roles", "feature_management"):
                if k in canonical and canonical[k] != self.data.get(k):
                    errs.append("'%s' differs from canonical" % k)
            for kid, kdef in (canonical.get("kits") or {}).items():
                mine = (self.data.get("kits") or {}).get(kid, {})
                if "roles" in kdef and kdef["roles"] != mine.get("roles"):
                    errs.append("kit '%s': roles differ from canonical" % kid)
            ck = {k: sorted(v.get("features", [])) for k, v in (canonical.get("kits") or {}).items()}
            mk = {k: sorted(v.get("features", [])) for k, v in (self.data.get("kits") or {}).items()}
            if ck != mk:
                errs.append("kit feature lists differ from canonical: %s vs %s" % (mk, ck))
        for kid, kdef in (self.data.get("kits") or {}).items():
            for f in kdef.get("features", []):
                if f != "*" and f not in feats:
                    errs.append("kit '%s' lists unknown feature '%s'" % (kid, f))
        labels = self.data.get("labels")
        cats = self.data.get("categories")
        for fid, fd in feats.items():
            if cats is not None and fd.get("category") not in cats:
                errs.append("feature '%s' has unknown category %r" % (fid, fd.get("category")))
            if labels is not None:
                for l in fd.get("labels", []):
                    if l not in labels:
                        errs.append("feature '%s' has unknown label %r" % (fid, l))
        if check_setup:
            errs.extend(setup_errors(self.data))
        seen = {}
        for fid, e in self.all_entries():
            if e in seen:
                errs.append("path %s is listed by both '%s' and '%s'" % (e, seen[e], fid))
            seen[e] = fid
        entries = list(seen.items())
        for e, f in entries:
            for e2, f2 in entries:
                if f != f2 and e != e2 and _claims(e, e2):
                    errs.append("path %s ('%s') is inside %s ('%s')" % (e2, f2, e, f))
        if root is not None:
            if universe is None:
                from .scrub import list_repo_files
                universe = list_repo_files(root)
            uni = set(universe)
            for fid, e in self.all_entries():
                if globs.is_glob(e):
                    if not any(globs.match(e, p) for p in uni):
                        errs.append("feature '%s': glob %s matches no file" % (fid, e))
                elif not os.path.exists(os.path.join(root, e)):
                    errs.append("feature '%s': path %s does not exist" % (fid, e))
        return errs


SETUP_SERVICES = ("flask", "spring", "pages")


def setup_errors(data):
    """Every feature needs setup.summary and setup.verify; every setting a known service."""
    errs = []
    for fid, fd in sorted((data.get("features") or {}).items()):
        st = fd.get("setup")
        if not isinstance(st, dict):
            errs.append("feature '%s' has no setup block" % fid)
            continue
        for k in ("summary", "verify"):
            if not str(st.get(k) or "").strip():
                errs.append("feature '%s': setup.%s is empty" % (fid, k))
        for s in st.get("settings") or []:
            if s.get("service") not in SETUP_SERVICES:
                errs.append("feature '%s': setting %s has service %r (expected one of %s)" % (
                    fid, s.get("key"), s.get("service"), "|".join(SETUP_SERVICES)))
    return errs


def _claims(entry, path):
    e = entry.rstrip("/")
    if globs.is_glob(e):
        return globs.match("/" + e, path)
    return path == e or path.startswith(e + "/")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
