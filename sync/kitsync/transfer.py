"""import / update: bring upstream files into a kit feature, safely.

Order of operations (nothing is written until every check that can run in memory has passed):
  1. expand the requested upstream paths (minus exclude globs)
  2. resolve dependencies (layouts, includes, scripts, images, ...); new ones are imported too
  3. transform: path map, replace rules, image metadata strip; protected kit paths are skipped
  4. SCRUB GATE on the transformed content -> abort on any finding
  5. plan catalog registration (conflicts with other features abort unless --move)
  6. --dry-run stops here
  7. new branch, write files + catalog (+ _projects/.makeprojects), run validators and tests
  8. commit on the branch (only the touched paths); optional PR via gh
"""

import datetime
import difflib
import json
import os
import shlex
import subprocess
import sys

from . import catalog as catalog_mod
from . import globs, gitops, scrub
from .urls import redact_text, redact_url


class TransferError(Exception):
    exit_code = 1


class ScrubFailed(TransferError):
    exit_code = 3


class CheckFailed(TransferError):
    exit_code = 4


class Plan(object):
    def __init__(self, mode, feature):
        self.mode = mode
        self.feature = feature
        self.files = []          # dicts: upstream, kit, action(add|update|same|protected), data, hits, diff
        self.excluded = []
        self.resolution = None
        self.registration = None
        self.new_feature = None
        self.scrub = None
        self.branch = None
        self.makeprojects = []
        self.kit_only = []       # (update) kit files of the feature that upstream no longer has
        self.notes = []
        self.checks = []
        self.commit = None

    def writes(self):
        return [f for f in self.files if f["action"] in ("add", "update")]

    def to_dict(self):
        return {
            "mode": self.mode, "feature": self.feature, "branch": self.branch,
            "files": [{k: v for k, v in f.items() if k != "data"} for f in self.files],
            "excluded": self.excluded,
            "dependencies": self.resolution.to_dict() if self.resolution else None,
            "catalog": self.registration, "new_feature": self.new_feature,
            "scrub": self.scrub.to_dict() if self.scrub else None,
            "makeprojects": self.makeprojects, "kit_only": self.kit_only,
            "notes": self.notes, "checks": self.checks, "commit": self.commit,
        }

    def format(self):
        L = ["%s feature '%s'" % (self.mode, self.feature)]
        counts = {}
        for f in self.files:
            counts[f["action"]] = counts.get(f["action"], 0) + 1
        L.append("files: " + (", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "none"))
        for f in self.files:
            if f["action"] == "same":
                continue
            extra = ""
            if f.get("diff"):
                extra = " (+%d -%d)" % (f["diff"][0], f["diff"][1])
            if f.get("hits"):
                extra += " [replaced: %s]" % ", ".join("%s x%d" % h for h in f["hits"])
            if f.get("dep_of"):
                extra += " [dependency of %s]" % f["dep_of"]
            arrow = "" if f["upstream"] == f["kit"] else "  <- " + f["upstream"]
            L.append("  %-9s %s%s%s" % (f["action"], f["kit"], arrow, extra))
        if self.excluded:
            L.append("excluded by kit.sync.json (not imported): %d" % len(self.excluded))
            for p in self.excluded[:20]:
                L.append("  - %s" % p)
        r = self.resolution
        if r is not None:
            if r.missing:
                L.append("MISSING dependencies (referenced, but in neither upstream nor kit) -- fix or ignore after review:")
                for k, v in sorted(r.missing.items()):
                    L.append("  - %s  (from %s)" % (k, ", ".join("%s:%s" % t for t in v[:3])))
            if r.excluded:
                L.append("dependencies excluded by config (will be missing in the kit):")
                for k, v in sorted(r.excluded.items()):
                    L.append("  - %s  (from %s)" % (k, ", ".join(sorted(v)[:3])))
            if r.generated:
                L.append("generated outputs (built from sources by make): %d" % len(r.generated))
        if self.kit_only:
            L.append("kit files of this feature that upstream no longer has (left alone): %d" % len(self.kit_only))
            for p in self.kit_only[:20]:
                L.append("  - %s" % p)
        if self.new_feature:
            L.append("new feature: %s" % json.dumps(self.new_feature))
        if self.registration is not None:
            reg = self.registration
            L.append("catalog: add %s%s%s" % (reg.get("added") or "nothing",
                                              ("; moved %s" % reg["moved"]) if reg.get("moved") else "",
                                              ("; already listed %s" % reg["already"]) if reg.get("already") else ""))
        if self.makeprojects:
            L.append("_projects/.makeprojects: register %s" % ", ".join(self.makeprojects))
        for n in self.notes:
            L.append("note: " + n)
        if self.scrub is not None:
            L.append(self.scrub.format())
        for c in self.checks:
            L.append("check: %s -> %s" % (c["name"], c["result"]))
        if self.branch:
            L.append("branch: %s" % self.branch)
        if self.commit:
            L.append("commit: %s" % self.commit)
        return "\n".join(L)


# ---------------------------------------------------------------------------------------------
def expand_requested(ctx, patterns):
    """Upstream files named by ``patterns`` (exact files, directories or globs, repo-relative)."""
    out, unmatched = set(), []
    for pat in patterns:
        pat = pat.strip().lstrip("./") if pat.startswith("./") else pat.strip()
        anchored = "/" + pat.lstrip("/")
        hits = [u for u in ctx.up.files if globs.match(anchored, u)]
        if not hits:
            unmatched.append(pat)
        out.update(hits)
    if unmatched:
        raise TransferError("no upstream files match: %s" % ", ".join(unmatched))
    return sorted(out)


def _registration_entries(ctx, patterns, requested):
    """Catalog entries (kit paths) for what the user asked for: dirs stay dirs, files stay files,
    globs become the matched files."""
    entries = []
    for pat in patterns:
        p = pat.strip().strip("/")
        if globs.is_glob(p):
            for u in requested:
                if globs.match("/" + p, u):
                    entries.append(ctx.cfg.to_kit(u))
        elif p in ctx.up.files:
            entries.append(ctx.cfg.to_kit(p))
        else:  # directory
            entries.append(ctx.cfg.to_kit(p + "/").rstrip("/"))
    seen, out = set(), []
    for e in entries:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _diffstat(old, new):
    if old is None:
        return None
    try:
        a = old.decode("utf-8").splitlines()
        b = new.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return (0, 0)
    plus = minus = 0
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            plus += 1
        elif line.startswith("-") and not line.startswith("---"):
            minus += 1
    return (plus, minus)


def _plan_files(ctx, plan, upaths, dep_of=None):
    for u in sorted(upaths):
        kpath, data, hits = ctx.transformed(u)
        if data is None:
            continue
        rec = {"upstream": u, "kit": kpath, "hits": hits, "data": data}
        if dep_of and u in dep_of:
            rec["dep_of"] = sorted(dep_of[u])[0]
        old = ctx.kit_bytes(kpath)
        prot = ctx.cfg.is_protected(kpath)
        if prot:
            rec["action"] = "protected"
            rec["protect"] = prot
        elif old is None:
            rec["action"] = "add"
        elif old == data:
            rec["action"] = "same"
        else:
            rec["action"] = "update"
            rec["diff"] = _diffstat(old, data)
        plan.files.append(rec)


def _scrub_plan(ctx, plan):
    rep = scrub.Report()
    for f in plan.writes():
        fs, al = ctx.gate.check_bytes(f["kit"], f["data"])
        rep.findings.extend(fs)
        rep.allowed.extend(al)
        rep.files_checked += 1
    plan.scrub = rep
    return rep


def _makeprojects_lines(ctx, plan):
    if not ctx.cfg.post_import.get("makeprojects"):
        return []
    mp = os.path.join(ctx.root, "_projects", ".makeprojects")
    have = set()
    if os.path.isfile(mp):
        with open(mp, "r", encoding="utf-8") as f:
            for line in f:
                s = line.split("#", 1)[0].strip()
                if s:
                    have.add(s.split(":", 1)[0])
    upstream_lines = {}
    up_mp = ctx.up.read_text("_projects/.makeprojects") or ""
    for line in up_mp.splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            upstream_lines[s.split(":", 1)[0]] = s
    want = []
    for f in plan.writes():
        parts = f["kit"].split("/")
        if parts[0] == "_projects" and len(parts) >= 4 and not parts[1].startswith("_"):
            name = "/".join(parts[1:3])
            if name not in have and name not in [w.split(":", 1)[0] for w in want]:
                want.append(upstream_lines.get(name, name))
    return want


def _write_files(ctx, plan):
    written = []
    for f in plan.writes():
        dest = os.path.join(ctx.root, f["kit"])
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(f["data"])
        written.append(f["kit"])
    if plan.makeprojects:
        mp = os.path.join(ctx.root, "_projects", ".makeprojects")
        text = ""
        if os.path.isfile(mp):
            with open(mp, "r", encoding="utf-8") as fh:
                text = fh.read()
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(l + "\n" for l in plan.makeprojects)
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with open(mp, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append("_projects/.makeprojects")
    return written


def _run_cmd(root, cmd, timeout=1800):
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    cmd = [sys.executable if c in ("python", "python3", "{python}") else c for c in cmd]
    p = subprocess.run(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode, redact_text(p.stdout.decode("utf-8", "replace"))


def _default_runner(root):
    """Run a git/gh command, echoing its output with any URL credentials removed."""
    def run(cmd):
        p = subprocess.run(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = redact_text(p.stdout.decode("utf-8", "replace")).strip()
        if out:
            print(out)
        return p.returncode
    return run


def _cmd_available(root, cmd):
    """A command is skipped when the script/dir it points at does not exist in the kit repo."""
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    for c in cmd[1:]:
        if c.startswith("-"):
            continue
        if "/" in c or c.endswith(".py"):
            return os.path.exists(os.path.join(root, c))
    return True


def run_checks(ctx, plan, before_errors, skip_tests=False):
    """Catalog validation, scrub of the written files, then kit.sync.json validators and tests."""
    ok = True
    if ctx.catalog is not None:
        cat = catalog_mod.Catalog.load(ctx.catalog_path, ctx.cfg.catalog_key)
        errs = cat.validate(ctx.root, canonical=None)
        new_errs = [e for e in errs if e not in before_errors]
        plan.checks.append({"name": "catalog", "result": "ok" if not new_errs else "FAIL", "output": "\n".join(new_errs)})
        ok = ok and not new_errs
    cmds = [("validator", c) for c in ctx.cfg.validators]
    if not skip_tests:
        cmds += [("test", c) for c in ctx.cfg.tests]
    for kind, cmd in cmds:
        name = "%s: %s" % (kind, cmd if isinstance(cmd, str) else " ".join(cmd))
        if not _cmd_available(ctx.root, cmd):
            plan.checks.append({"name": name, "result": "skipped (not present in kit repo)", "output": ""})
            continue
        rc, out = _run_cmd(ctx.root, cmd)
        plan.checks.append({"name": name, "result": "ok" if rc == 0 else "FAIL (exit %d)" % rc, "output": out[-4000:]})
        ok = ok and rc == 0
    return ok


def _commit(ctx, plan, paths, message):
    root = ctx.root
    gitops.git(root, "add", "--", *paths)
    gitops.git(root, "-c", "user.name=%s" % _git_ident(root, "user.name", "OCS Kit Sync"),
               "-c", "user.email=%s" % _git_ident(root, "user.email", "ocs-kit-sync@localhost"),
               "commit", "-q", "-m", message, "--", *paths)
    plan.commit = gitops.head(root)


def _git_ident(root, key, default):
    v = gitops.git(root, "config", "--get", key, check=False).strip()
    return v or default


def _open_pr(ctx, plan, title, body, runner=None):
    runner = runner or _default_runner(ctx.root)
    title, body = redact_text(title), redact_text(body)
    if runner(["git", "push", "-u", "origin", plan.branch]) != 0:
        raise TransferError("git push failed; branch %s is committed locally" % plan.branch)
    if runner(["gh", "pr", "create", "--head", plan.branch, "--title", title, "--body", body]) != 0:
        raise TransferError("gh pr create failed; push succeeded, open the PR by hand")


def _finish(ctx, plan, catalog_changed, dry_run, commit, pr, title, body, skip_tests, before_errors, pr_runner=None):
    """Steps 6-8 shared by import and update."""
    if dry_run:
        plan.notes.append("dry run: nothing was written")
        return plan
    is_git = gitops.is_repo(ctx.root)
    if commit and is_git:
        plan.branch = gitops.unique_branch(ctx.root, plan.branch)
        gitops.git(ctx.root, "checkout", "-q", "-b", plan.branch)
    else:
        plan.branch = None
    written = _write_files(ctx, plan)
    if catalog_changed:
        ctx.catalog.save()
        written.append(ctx.cfg.catalog_file)
    ok = run_checks(ctx, plan, before_errors, skip_tests=skip_tests)
    if not ok:
        failed = [c for c in plan.checks if c["result"].startswith("FAIL")]
        raise CheckFailed("checks failed after writing (changes left uncommitted%s):\n%s" % (
            (" on branch " + plan.branch) if plan.branch else "",
            "\n".join("%s\n%s" % (c["name"], c["output"]) for c in failed)))
    if commit and is_git and written:
        _commit(ctx, plan, written, title + "\n\n" + body)
        if pr:
            _open_pr(ctx, plan, title, body, runner=pr_runner)
    return plan


def _today(date=None):
    return date or datetime.date.today().strftime("%Y%m%d")


# ---------------------------------------------------------------------------------------------
def do_import(ctx, feature, patterns, new_feature=None, move=False, dry_run=False, follow_deps=True,
              commit=True, pr=False, skip_tests=False, date=None, pr_runner=None):
    """``new_feature``: None, or {"name", "description", "category", "labels", "kits"} to create ``feature``."""
    plan = Plan("import", feature)
    if ctx.catalog is None:
        raise TransferError("kit repo has no %s yet; the catalog interface is required to register features" % ctx.cfg.catalog_file)
    before_errors = ctx.catalog.validate(ctx.root)
    if new_feature is None and not ctx.catalog.has(feature):
        raise TransferError("unknown feature '%s'; known: %s (or pass --new-feature)" % (
            feature, ", ".join(sorted(ctx.catalog.features))))
    if new_feature is not None:
        kits = ctx.catalog.create_feature(feature, new_feature.get("name"), new_feature.get("description"),
                                          new_feature.get("category"), new_feature.get("labels") or [],
                                          new_feature.get("kits"), canonical=ctx.canonical,
                                          setup_summary=new_feature.get("setup_summary"),
                                          verify=new_feature.get("verify"))
        nf = {k: v for k, v in ctx.catalog.features[feature].items() if k != ctx.cfg.catalog_key}
        plan.new_feature = {"id": feature, "kits": kits, "canonical_entry": nf}
        plan.notes.append("new feature: also add it to the canonical OCS-Kit/kits/catalog.json "
                          "(features.%s = %s; kits %s) and to the other kit repos' catalogs" % (
                              feature, json.dumps(nf), ", ".join(kits)))

    all_req = expand_requested(ctx, patterns)
    requested = [u for u in all_req if not ctx.cfg.is_excluded(u)]
    plan.excluded = [u for u in all_req if ctx.cfg.is_excluded(u)]
    if not requested:
        raise TransferError("everything requested is excluded by kit.sync.json")
    res = ctx.resolve(requested, follow=follow_deps)
    plan.resolution = res
    _plan_files(ctx, plan, res.import_paths, dep_of=res.new_deps)

    rep = _scrub_plan(ctx, plan)

    # catalog registration: requested entries + new dependency files not already owned by a feature
    entries = _registration_entries(ctx, patterns, requested)
    entries = [e for e in entries if not ctx.cfg.is_protected(e)]
    for f in plan.files:
        if f.get("dep_of") and f["action"] == "add":
            own = ctx.catalog.owner(f["kit"])
            if own is None:
                entries.append(f["kit"])
            elif own != feature:
                plan.notes.append("dependency %s lands inside feature '%s' (left registered there)" % (f["kit"], own))
    plan.registration = ctx.catalog.add_paths(feature, entries, move=move)
    plan.makeprojects = _makeprojects_lines(ctx, plan)
    plan.branch = "import/%s-%s" % (feature, _today(date))

    if not rep.ok:
        raise ScrubFailed("SCRUB GATE FAILED -- nothing written.\n" + rep.format())
    if not plan.writes() and not plan.registration["added"] and not plan.registration["moved"] and new_feature is None:
        plan.notes.append("nothing to do: the kit already has these files and they are registered")
        return plan
    title = "Import %s from upstream%s" % (feature, (" " + ctx.up.sha[:10]) if ctx.up.sha else "")
    body = _body(ctx, plan, patterns)
    return _finish(ctx, plan, True, dry_run, commit, pr, title, body, skip_tests, before_errors, pr_runner)


def do_update(ctx, feature, dry_run=False, follow_deps=True, commit=True, pr=False, skip_tests=False,
              date=None, pr_runner=None):
    plan = Plan("update", feature)
    if ctx.catalog is None:
        raise TransferError("kit repo has no %s" % ctx.cfg.catalog_file)
    if not ctx.catalog.has(feature):
        raise TransferError("unknown feature '%s'" % feature)
    before_errors = ctx.catalog.validate(ctx.root)
    entries = ctx.catalog.paths(feature)
    if not entries:
        raise TransferError("feature '%s' lists no %s paths" % (feature, ctx.cfg.catalog_key))
    kit_owned = set(ctx.catalog.files_of(feature, ctx.kit_files))
    up_paths = set()
    for u in ctx.up.files:
        if ctx.cfg.is_excluded(u):
            continue
        k = ctx.cfg.to_kit(u)
        if k in kit_owned or any(catalog_mod._claims(e, k) for e in entries):
            # paths of other, more specific features stay with them
            own = ctx.catalog.owner(k)
            if own in (None, feature):
                up_paths.add(u)
    mapped = set(ctx.cfg.to_kit(u) for u in up_paths)
    plan.kit_only = sorted(k for k in kit_owned if k not in mapped and not ctx.cfg.is_protected(k))
    res = ctx.resolve(sorted(up_paths), follow=follow_deps)
    plan.resolution = res
    _plan_files(ctx, plan, res.import_paths, dep_of=res.new_deps)
    rep = _scrub_plan(ctx, plan)
    new_dep_entries = []
    for f in plan.files:
        if f.get("dep_of") and f["action"] == "add" and ctx.catalog.owner(f["kit"]) is None:
            new_dep_entries.append(f["kit"])
    plan.registration = ctx.catalog.add_paths(feature, new_dep_entries) if new_dep_entries else {"added": [], "moved": [], "already": []}
    plan.makeprojects = _makeprojects_lines(ctx, plan)
    plan.branch = "update/%s-%s" % (feature, _today(date))
    if not rep.ok:
        raise ScrubFailed("SCRUB GATE FAILED -- nothing written.\n" + rep.format())
    if not plan.writes():
        plan.notes.append("feature '%s' is up to date with upstream" % feature)
        return plan
    title = "Update %s from upstream%s" % (feature, (" " + ctx.up.sha[:10]) if ctx.up.sha else "")
    body = _body(ctx, plan, entries)
    return _finish(ctx, plan, bool(new_dep_entries), dry_run, commit, pr, title, body, skip_tests, before_errors, pr_runner)


def _body(ctx, plan, patterns):
    adds = [f["kit"] for f in plan.files if f["action"] == "add"]
    upds = [f["kit"] for f in plan.files if f["action"] == "update"]
    lines = ["Upstream: %s%s" % (redact_url(ctx.cfg.upstream_url or ctx.up.root), (" @ " + ctx.up.sha) if ctx.up.sha else ""),
             "Requested: %s" % ", ".join(patterns),
             "Added %d file(s), updated %d file(s)." % (len(adds), len(upds))]
    if plan.resolution and plan.resolution.missing:
        lines.append("Missing references to review: %s" % ", ".join(sorted(plan.resolution.missing)[:20]))
    if plan.scrub:
        lines.append("Scrub gate: passed on %d file(s), %d allowlisted." % (plan.scrub.files_checked, len(plan.scrub.allowed)))
    lines.append("Generated by sync/ocs_kit_sync.py %s." % plan.mode)
    return "\n".join(lines)
