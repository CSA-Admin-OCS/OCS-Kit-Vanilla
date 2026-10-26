"""scan: what upstream has that the kit does not (new) or has differently (changed)."""

import fnmatch
import json
import os
import re

from . import globs
from .urls import redact_url


def group_key(upath):
    """Suggested feature unit for a new upstream path."""
    parts = upath.split("/")
    top = parts[0]
    if len(parts) == 1:
        return "(root files)"
    if top == "_projects" and len(parts) >= 4:
        return "/".join(parts[:3])
    if top in ("_notebooks", "_posts", "_docx", "navigation", "hacks", "images", "media", "_data", "_sass",
               "_includes", "_themes", "pages", "docs", "design", "tests", "scripts", "capstone"):
        if len(parts) == 2:
            return top + " (top-level files)"
        return "/".join(parts[:2])
    if top == "assets" and len(parts) >= 3:
        if len(parts) >= 4 and parts[1] in ("js", "css", "images", "audio", "sounds"):
            return "/".join(parts[:3])
        return "/".join(parts[:2]) if len(parts) >= 3 else top
    if top == "_layouts":
        return "_layouts"
    return top


def suggest_feature(key, files, suggest_map, valid_ids):
    """Best-guess existing feature id for a group, using kit.sync.json 'suggest' keyword lists."""
    hay = (key + " " + " ".join(files[:50])).lower()
    best, score = None, 0
    for fid, words in (suggest_map or {}).items():
        if valid_ids is not None and fid not in valid_ids:
            continue
        s = sum(hay.count(w.lower()) for w in words)
        if s > score:
            best, score = fid, s
    return best


def run_scan(ctx, with_deps=True, with_scrub=True, limit_groups=None):
    cfg = ctx.cfg
    view = ctx.upstream_kit_view()
    excluded = sorted(u for u in ctx.up.files if cfg.is_excluded(u))
    new, changed, same, protected = [], [], [], []
    for u, k in view.items():
        kb = ctx.kit_bytes(k) if k in ctx.kit_files or os.path.isfile(os.path.join(ctx.root, k)) else None
        if kb is None:
            if cfg.is_protected(k):
                protected.append((u, k, "new"))
            else:
                new.append(u)
            continue
        _, ub, _ = ctx.transformed(u)
        if ub == kb:
            same.append(u)
        elif cfg.is_protected(k):
            protected.append((u, k, "changed"))
        else:
            changed.append(u)

    valid_ids = set(ctx.catalog.features) if ctx.catalog else None
    if valid_ids is None and ctx.canonical:
        valid_ids = set(ctx.canonical.get("features", {}))
    suggest_map = cfg.data.get("suggest") or {}

    # changed paths, by owning feature
    changed_by_feature = {}
    for u in changed:
        k = cfg.to_kit(u)
        own = ctx.owner(k) if ctx.catalog else None
        changed_by_feature.setdefault(own or ("(unclaimed: core)" if ctx.catalog else "(no catalog yet)"), []).append(u)

    # new paths, grouped
    groups = {}
    for u in new:
        groups.setdefault(group_key(u), []).append(u)
    group_rows = []
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        files = sorted(groups[key])
        size = sum(len(ctx.up.read_bytes(f) or b"") for f in files)
        row = {"group": key, "files": len(files), "kb": size // 1024, "kit_path": cfg.to_kit(key.split(" ")[0]),
               "suggested_feature": suggest_feature(key, files, suggest_map, valid_ids),
               "paths": files}
        if with_deps and (limit_groups is None or len(group_rows) < limit_groups):
            res = ctx.resolve(files)
            row["needs_new"] = sorted(set(res.new_deps) - set(files))
            row["missing"] = sorted(res.missing)
            row["needs_excluded"] = sorted(res.excluded)
        if with_scrub:
            fnd = []
            for f in files:
                kp, data, _ = ctx.transformed(f)
                if data is None:
                    continue
                fs, _ = ctx.gate.check_bytes(kp, data)
                fnd.extend(fs)
            row["scrub_findings"] = len(fnd)
            row["scrub_rules"] = _count([x.rule for x in fnd])
            row["scrub_examples"] = [x.format() for x in fnd[:5]]
        group_rows.append(row)

    changed_scrub = {}
    if with_scrub:
        for u in changed:
            kp, data, _ = ctx.transformed(u)
            fs, _ = ctx.gate.check_bytes(kp, data or b"")
            if fs:
                changed_scrub[u] = [x.format() for x in fs[:3]]

    return {
        "upstream": {"root": ctx.up.root, "url": redact_url(getattr(ctx.up, "url", "")), "sha": ctx.up.sha, "files": len(ctx.up.files)},
        "kit": {"root": ctx.root, "files": len(ctx.kit_files), "catalog": bool(ctx.catalog)},
        "counts": {"new": len(new), "changed": len(changed), "same": len(same),
                   "protected": len(protected), "excluded": len(excluded), "groups": len(group_rows)},
        "changed_by_feature": {k: sorted(v) for k, v in sorted(changed_by_feature.items())},
        "changed_scrub": changed_scrub,
        "protected": [{"upstream": u, "kit": k, "state": s} for u, k, s in protected],
        "excluded": excluded,
        "groups": group_rows,
    }


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def format_scan(r, markdown=False, max_paths=8):
    L = []
    c = r["counts"]
    h = "## " if markdown else ""
    L.append("%sUpstream scan" % ("# " if markdown else ""))
    L.append("")
    L.append("- upstream: %s @ %s (%d files)" % (redact_url(r["upstream"].get("url") or r["upstream"]["root"]), r["upstream"]["sha"][:10] or "?", r["upstream"]["files"]))
    L.append("- kit: %s (%d files, catalog: %s)" % (r["kit"]["root"], r["kit"]["files"], "yes" if r["kit"]["catalog"] else "MISSING"))
    L.append("- new: %d, changed: %d, identical: %d, protected (kit-owned, skipped): %d, excluded by config: %d" % (
        c["new"], c["changed"], c["same"], c["protected"], c["excluded"]))
    L.append("")
    L.append("%sSuggested features from new upstream content (%d groups)" % (h, c["groups"]))
    L.append("")
    L.append("| group (upstream) | files | KB | suggested feature | new deps | missing refs | scrub findings |")
    L.append("|---|---:|---:|---|---:|---:|---|")
    for g in r["groups"]:
        rules = ", ".join("%s=%d" % kv for kv in sorted(g.get("scrub_rules", {}).items()))
        L.append("| %s | %d | %d | %s | %s | %s | %s |" % (
            g["group"] + ("" if g["kit_path"] == g["group"].split(" ")[0] else " -> " + g["kit_path"]),
            g["files"], g["kb"], g["suggested_feature"] or "?",
            len(g["needs_new"]) if "needs_new" in g else "-", len(g["missing"]) if "missing" in g else "-",
            ("%d (%s)" % (g["scrub_findings"], rules)) if g.get("scrub_findings") else "0"))
    L.append("")
    L.append("%sChanged upstream paths, by kit feature" % h)
    L.append("")
    if not r["changed_by_feature"]:
        L.append("(none)")
    for fid, paths in r["changed_by_feature"].items():
        L.append("- **%s** (%d): %s%s" % (fid, len(paths), ", ".join(paths[:max_paths]),
                                         " ..." if len(paths) > max_paths else ""))
    if r["changed_scrub"]:
        L.append("")
        L.append("Changed paths that would fail the scrub gate (%d):" % len(r["changed_scrub"]))
        L.append("")
        for p, fs in sorted(r["changed_scrub"].items())[:40]:
            L.append("- %s: %s" % (p, fs[0]))
    if r["protected"]:
        L.append("")
        L.append("%sProtected kit files that differ upstream (never overwritten)" % h)
        L.append("")
        for p in r["protected"][:40]:
            L.append("- %s (%s)" % (p["kit"], p["state"]))
    L.append("")
    return "\n".join(L)
