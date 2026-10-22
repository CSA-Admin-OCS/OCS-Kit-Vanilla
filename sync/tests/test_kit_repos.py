""""Nothing broke" suite: run against the REAL kit repos.

Repos are found via $OCS_KIT_PAGES / $OCS_KIT_FLASK / $OCS_KIT_SPRING, defaulting to the sibling
directories OCS-Kit-Pages, OCS-Kit-Flask and OCS-Kit-Spring next to OCS-Kit. Missing repos (or a repo
without kits/catalog.json yet) are skipped. The canonical catalog is $OCS_KIT_CATALOG or
OCS-Kit/kits/catalog.json.

Slow checks (OCS_KIT_SLOW=1): for every kit in the Pages catalog, build the site in a temporary copy
(scripts/apply_kit.py --kit <kit>, then `make build` or `bundle exec jekyll build`) and check internal
links (the Pages repo's scripts/check_links.py if present, else kitsync.links).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import CANONICAL, OCS_KIT_DIR, SYNC_DIR

from kitsync import catalog, config, links, scrub

PARENT = os.path.dirname(OCS_KIT_DIR)
REPOS = {
    "pages": os.environ.get("OCS_KIT_PAGES") or os.path.join(PARENT, "OCS-Kit-Pages"),
    "flask": os.environ.get("OCS_KIT_FLASK") or os.path.join(PARENT, "OCS-Kit-Flask"),
    "spring": os.environ.get("OCS_KIT_SPRING") or os.path.join(PARENT, "OCS-Kit-Spring"),
}
CANON_PATH = os.environ.get("OCS_KIT_CATALOG") or CANONICAL
SLOW = os.environ.get("OCS_KIT_SLOW") == "1"


def load_cfg(name, root):
    p = os.path.join(root, "kit.sync.json")
    if os.path.isfile(p):
        return config.load(p)
    return config.load(os.path.join(SYNC_DIR, "configs", "%s.kit.sync.json" % name))


@unittest.skipUnless(os.path.isfile(CANON_PATH), "canonical catalog not found")
class CanonicalCatalogTest(unittest.TestCase):
    def setUp(self):
        self.canon = catalog.load_json(CANON_PATH)

    def test_canonical_is_self_consistent(self):
        errs = catalog.Catalog(json.loads(json.dumps(self.canon))).validate(check_setup=False)
        self.assertEqual(errs, [])

    def test_every_feature_has_setup(self):
        self.assertEqual(catalog.setup_errors(self.canon), [])

    def test_core_always_on_everywhere(self):
        for kid, kdef in self.canon.get("kits", {}).items():
            self.assertIn("core", kdef.get("features", []), kid)


class _RepoChecks(object):
    name = None

    @classmethod
    def setUpClass(cls):
        cls.root = REPOS[cls.name]
        if not os.path.isdir(cls.root):
            raise unittest.SkipTest("%s repo not found at %s" % (cls.name, cls.root))
        cls.cfg = load_cfg(cls.name, cls.root)
        cls.cat_path = os.path.join(cls.root, cls.cfg.catalog_file)
        cls.files = scrub.list_repo_files(cls.root)

    def catalog(self):
        if not os.path.isfile(self.cat_path):
            self.skipTest("%s has no %s yet" % (self.name, self.cfg.catalog_file))
        return catalog.Catalog.load(self.cat_path, self.cfg.catalog_key)

    def canonical(self):
        if not os.path.isfile(CANON_PATH):
            self.skipTest("canonical catalog not found")
        return catalog.load_json(CANON_PATH)

    def test_catalog_matches_canonical(self):
        cat, canon = self.catalog(), self.canonical()
        errs = cat.validate(canonical=canon, check_setup=False)
        self.assertEqual(errs, [], "\n".join(errs))

    def test_setup_blocks(self):
        cat = self.catalog()
        if not any("setup" in f for f in cat.features.values()):
            self.skipTest("repo catalog carries no setup blocks (canonical is checked separately)")
        errs = catalog.setup_errors(cat.data)
        self.assertEqual(errs, [], "\n".join(errs))

    def test_catalog_paths_exist_and_unique(self):
        cat = self.catalog()
        errs = cat.validate(self.root, universe=self.files, check_setup=False)
        self.assertEqual(errs, [], "\n".join(errs))

    def test_scrub_gate_passes(self):
        rep = scrub.ScrubGate(self.cfg.scrub, base_dir=self.root).check_files(self.root, self.files)
        self.assertTrue(rep.ok, "\n" + rep.format(limit=60) +
                        "\n(fix the files; for binary metadata: ocs_kit_sync.py strip-metadata <files>;"
                        " reviewed false positives go in kit.scrub-allow)")


class PagesRepoTest(_RepoChecks, unittest.TestCase):
    name = "pages"

    def test_apply_kit_check(self):
        script = os.path.join(self.root, "scripts", "apply_kit.py")
        if not os.path.isfile(script):
            self.skipTest("scripts/apply_kit.py not present yet")
        p = subprocess.run([sys.executable, script, "--check"], cwd=self.root, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=dict(os.environ, OCS_KIT_CATALOG=CANON_PATH))
        self.assertEqual(p.returncode, 0, p.stdout.decode())

    @unittest.skipUnless(SLOW, "slow: set OCS_KIT_SLOW=1 for per-kit Jekyll builds + link check")
    def test_per_kit_build_and_links(self):
        cat = self.catalog()
        if not shutil.which("bundle") and not shutil.which("jekyll"):
            self.skipTest("bundle/jekyll not installed")
        failures = []
        for kit in sorted(cat.data.get("kits", {})):
            with self.subTest(kit=kit):
                tmp = tempfile.mkdtemp(prefix="ocs-kit-build-%s-" % kit)
                try:
                    errs = _build_and_check(self.root, self.files, kit, tmp)
                    if errs:
                        failures.append("%s: %s" % (kit, errs))
                    self.assertEqual(errs, [], errs)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)


def _run(cmd, cwd, timeout=3600):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def _build_and_check(root, files, kit, tmp):
    work = os.path.join(tmp, "repo")
    for rel in files:
        dst = os.path.join(work, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dst)
    if os.path.isdir(os.path.join(root, "venv")):
        os.symlink(os.path.join(root, "venv"), os.path.join(work, "venv"))
    rc, out = _run([sys.executable, "scripts/apply_kit.py", "--kit", kit], work)
    if rc != 0:
        return ["apply_kit --kit %s failed:\n%s" % (kit, out[-3000:])]
    site = os.path.join(work, "_site")
    if os.path.isfile(os.path.join(work, "Makefile")) and os.path.isdir(os.path.join(work, "venv")):
        rc, out = _run(["make", "build", "KIT=%s" % kit], work)
    else:
        cfgs = "_config.yml" + (",_config.kit.yml" if os.path.isfile(os.path.join(work, "_config.kit.yml")) else "")
        rc, out = _run(["bundle", "exec", "jekyll", "build", "--config", cfgs, "-d", site], work)
    if rc != 0:
        return ["build failed:\n%s" % out[-3000:]]
    checker = os.path.join(work, "scripts", "check_links.py")
    if os.path.isfile(checker):
        rc, out = _run([sys.executable, checker, site], work)
        return [] if rc == 0 else ["link check failed:\n%s" % out[-3000:]]
    baseurl = ""
    for line in open(os.path.join(work, "_config.yml"), encoding="utf-8"):
        if line.startswith("baseurl:"):
            baseurl = line.split(":", 1)[1].split("#", 1)[0].strip().strip("\"'")
    broken = links.check_site(site, baseurl)
    return ["%s (from %s)" % (u, ", ".join(sorted(v)[:3])) for u, v in sorted(broken.items())]


class FlaskRepoTest(_RepoChecks, unittest.TestCase):
    name = "flask"


class SpringRepoTest(_RepoChecks, unittest.TestCase):
    name = "spring"


if __name__ == "__main__":
    unittest.main()
