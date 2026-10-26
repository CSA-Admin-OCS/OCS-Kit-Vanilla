#!/usr/bin/env python3
"""ocs_kit_sync: bring features from the upstream Open Coding Society repos into OCS-in-a-Kit.

  scan     what upstream has that the kit does not (new) or has differently (changed)
  import   copy upstream paths (plus what they need) into a kit feature, scrubbed, on a new branch
  update   pull upstream changes for the paths a feature already owns
  scrub    run the scrub gate over a directory (or the given files)
  check    "nothing broke" checks: catalog vs canonical, catalog paths exist, no double claims, scrub
  strip-metadata  remove text metadata (Canva/EXIF/XMP) from PNG/JPEG files in place

Python 3.8+ standard library only. See sync/README.md.
Exit codes: 0 ok, 1 error, 2 usage, 3 scrub gate failed, 4 checks failed after writing.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kitsync import catalog as catalog_mod  # noqa: E402
from kitsync import config as config_mod  # noqa: E402
from kitsync import gitops, media, scan, scrub, transfer  # noqa: E402
from kitsync.core import KitContext  # noqa: E402
from kitsync.urls import redact_text  # noqa: E402

DEFAULT_CANONICAL = os.path.join(os.path.dirname(HERE), "kits", "catalog.json")


def _log(msg):
    sys.stderr.write(redact_text(str(msg)) + "\n")


def _context(args):
    cfg = config_mod.find_config(args.kit, args.config)
    spec = args.upstream or cfg.upstream_url
    up = gitops.open_upstream(spec, branch=args.branch or cfg.upstream_branch, cache=args.cache,
                              fetch=not args.no_fetch, log=_log)
    return KitContext(args.kit, cfg, up, canonical_path=args.canonical, log=_log)


def cmd_scan(args):
    ctx = _context(args)
    rep = scan.run_scan(ctx, with_deps=not args.no_deps, with_scrub=not args.no_scrub)
    if args.json:
        out = json.dumps(rep, indent=2)
    else:
        out = scan.format_scan(rep, markdown=args.markdown or bool(args.output and args.output.endswith(".md")))
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        _log("wrote %s" % args.output)
    else:
        print(out)
    return 0


def _print_plan(plan, as_json):
    if as_json:
        print(json.dumps(plan.to_dict(), indent=2, default=list))
    else:
        print(plan.format())


def cmd_import(args):
    ctx = _context(args)
    nf = None
    if args.new_feature:
        if not args.category:
            _log("--new-feature needs --category (core|general|education)")
            return 2
        nf = {"name": args.name, "description": args.description, "category": args.category,
              "labels": _csv(args.labels), "kits": _csv(args.kits) if args.kits is not None else None,
              "setup_summary": args.setup_summary, "verify": args.verify}
    try:
        plan = transfer.do_import(ctx, args.feature, args.paths, new_feature=nf, move=args.move, dry_run=args.dry_run,
                                  follow_deps=not args.no_deps, commit=not args.no_commit, pr=args.pr,
                                  skip_tests=args.skip_tests)
    except (transfer.TransferError, catalog_mod.CatalogError) as e:
        _log(str(e))
        return getattr(e, "exit_code", 1)
    _print_plan(plan, args.json)
    return 0


def cmd_update(args):
    ctx = _context(args)
    try:
        plan = transfer.do_update(ctx, args.feature, dry_run=args.dry_run, follow_deps=not args.no_deps,
                                  commit=not args.no_commit, pr=args.pr, skip_tests=args.skip_tests)
    except (transfer.TransferError, catalog_mod.CatalogError) as e:
        _log(str(e))
        return getattr(e, "exit_code", 1)
    _print_plan(plan, args.json)
    return 0


def cmd_scrub(args):
    cfg = config_mod.find_config(args.path, args.config) if (args.config or os.path.isfile(os.path.join(args.path, "kit.sync.json"))) else None
    gate = scrub.ScrubGate(cfg.scrub if cfg else {}, base_dir=args.path)
    rep = gate.check_tree(args.path, args.files or None)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
    else:
        print(rep.format(limit=args.limit))
    return 0 if rep.ok else 3


def run_repo_checks(kit, cfg, canonical_path=None, with_scrub=True):
    """The fast "nothing broke" checks. Returns a list of (name, [problems])."""
    results = []
    cat_path = os.path.join(kit, cfg.catalog_file)
    if not os.path.isfile(cat_path):
        return [("catalog present", ["%s is missing" % cfg.catalog_file])]
    cat = catalog_mod.Catalog.load(cat_path, cfg.catalog_key)
    canonical = catalog_mod.load_json(canonical_path) if canonical_path and os.path.isfile(canonical_path) else None
    files = scrub.list_repo_files(kit)
    errs = cat.validate(kit, universe=files, canonical=canonical)
    results.append(("catalog", errs))
    if canonical is not None:
        results.append(("canonical setup blocks", catalog_mod.setup_errors(canonical)))
    if with_scrub:
        rep = scrub.ScrubGate(cfg.scrub, base_dir=kit).check_files(kit, files)
        results.append(("scrub gate", [f.format() for f in rep.findings]))
    return results


def cmd_check(args):
    cfg = config_mod.find_config(args.kit, args.config)
    res = run_repo_checks(args.kit, cfg, args.canonical, with_scrub=not args.no_scrub)
    bad = 0
    for name, problems in res:
        print("%-24s %s" % (name, "ok" if not problems else "FAIL (%d)" % len(problems)))
        for p in problems[:args.limit]:
            print("    " + p)
        bad += len(problems)
    return 0 if not bad else 4


def cmd_strip(args):
    changed = 0
    for p in args.files:
        with open(p, "rb") as f:
            data = f.read()
        new = media.strip_metadata(p, data)
        if new != data:
            changed += 1
            if not args.dry_run:
                with open(p, "wb") as f:
                    f.write(new)
            print("%s %s (%d -> %d bytes)" % ("would strip" if args.dry_run else "stripped", p, len(data), len(new)))
    print("%d file(s) %s" % (changed, "would change" if args.dry_run else "changed"))
    return 0


def _csv(v):
    if v is None:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def build_parser():
    ap = argparse.ArgumentParser(prog="ocs_kit_sync.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def common(p, upstream=True):
        p.add_argument("--kit", default=".", help="kit repo path (default: .)")
        p.add_argument("--config", help="kit.sync.json (default: <kit>/kit.sync.json, else sync/configs/<repo>.kit.sync.json)")
        p.add_argument("--canonical", default=DEFAULT_CANONICAL if os.path.isfile(DEFAULT_CANONICAL) else None,
                       help="canonical OCS-Kit/kits/catalog.json")
        if upstream:
            p.add_argument("--upstream", help="upstream git URL or local checkout (default: upstream.url from config)")
            p.add_argument("--branch", help="upstream branch (default: from config)")
            p.add_argument("--cache", help="clone cache dir (default: ~/.cache/ocs-kit-sync)")
            p.add_argument("--no-fetch", action="store_true", help="use the cached clone as is")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("scan", help="list new/changed upstream paths and suggested features")
    common(p)
    p.add_argument("--output", "-o", help="write the report to a file (.md -> markdown)")
    p.add_argument("--markdown", action="store_true")
    p.add_argument("--no-deps", action="store_true", help="skip dependency resolution per group (faster)")
    p.add_argument("--no-scrub", action="store_true", help="skip scrub preview per group (faster)")
    p.set_defaults(func=cmd_scan)

    def transfer_opts(p):
        p.add_argument("--feature", required=True, help="catalog feature id")
        p.add_argument("--dry-run", action="store_true", help="show everything, write nothing")
        p.add_argument("--no-deps", action="store_true", help="do not follow layout/include/asset references")
        p.add_argument("--no-commit", action="store_true", help="write files but do not branch/commit")
        p.add_argument("--skip-tests", action="store_true", help="run validators but not kit.sync.json tests")
        p.add_argument("--pr", action="store_true", help="push the branch and open a PR with gh")

    p = sub.add_parser("import", help="import upstream paths into a kit feature")
    common(p)
    transfer_opts(p)
    p.add_argument("--paths", nargs="+", required=True, help="upstream paths, directories or globs")
    p.add_argument("--new-feature", action="store_true", help="create --feature in the kit catalog")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--category", choices=["core", "general", "education"])
    p.add_argument("--labels", help="comma-separated labels (validated against the catalog 'labels' map)")
    p.add_argument("--kits", help="comma-separated kits to add a new feature to (default: from category)")
    p.add_argument("--setup-summary", help="setup.summary for a new feature")
    p.add_argument("--verify", help="setup.verify for a new feature")
    p.add_argument("--move", action="store_true", help="take paths already claimed by another feature")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("update", help="pull upstream changes for a feature's paths")
    common(p)
    transfer_opts(p)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("scrub", help="run the scrub gate over a directory")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("files", nargs="*", help="only these repo-relative files")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_scrub)

    p = sub.add_parser("check", help="'nothing broke' checks for a kit repo")
    common(p, upstream=False)
    p.add_argument("--no-scrub", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("strip-metadata", help="strip PNG/JPEG text metadata in place")
    p.add_argument("files", nargs="+")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_strip)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    try:
        return args.func(args)
    except (config_mod.ConfigError, gitops.GitError, catalog_mod.CatalogError) as e:
        _log("error: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
