"""scan -> import -> update against a fake upstream repo and a fake kit repo (both real git repos)."""

import json
import os
import unittest

from helpers import DENY, Sandbox, git, read, write

from kitsync import catalog, transfer


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.kit = self.sb.kit

    def tearDown(self):
        self.sb.cleanup()

    def catalog(self):
        return catalog.Catalog.load(os.path.join(self.kit, "kits", "catalog.json"))

    def branches(self):
        return sorted(b.strip("* ").strip() for b in git(self.kit, "branch").splitlines())

    def assert_protected_untouched(self):
        self.assertEqual(read(self.kit, "_config.yml"), "title: My School\nkit:\n  features: []\n")
        self.assertEqual(read(self.kit, "README.md"), "kit readme\n")

    def test_full_flow(self):
        # ---- scan
        rc, out, err = self.sb.cli("scan", "--json")
        self.assertEqual(rc, 0, err)
        rep = json.loads(out)
        groups = {g["group"]: g for g in rep["groups"]}
        self.assertIn("navigation/games", groups)
        self.assertEqual(groups["navigation/games"]["suggested_feature"], "games")
        self.assertIn("navigation/about.md", rep["excluded"])
        self.assertIn("images/about/me.png", rep["excluded"])
        self.assertEqual(rep["changed_by_feature"], {"extras": ["media/media.md"]})
        self.assertEqual(sorted(p["kit"] for p in rep["protected"]), ["README.md", "_config.yml", "_projects/.makeprojects"])
        self.assertGreater(groups["navigation/bad"]["scrub_findings"], 0)
        rc, out, _ = self.sb.cli("scan")
        self.assertIn("| navigation/games |", out)

        # ---- dry run writes nothing
        snap = self.sb.snapshot()
        rc, out, err = self.sb.cli("import", "--feature", "arcade", "--new-feature", "--name", "Arcade",
                                   "--description", "Small arcade games", "--category", "general",
                                   "--labels", "student-safe", "--paths", "navigation/games/pong.md", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("_layouts/game.html", out)
        self.assertIn("dry run", out)
        self.assertEqual(self.sb.snapshot(), snap)
        self.assertEqual(self.branches(), ["main"])

        # ---- the scrub gate blocks a leak, nothing written
        rc, out, err = self.sb.cli("import", "--feature", "games", "--paths", "navigation/bad")
        self.assertEqual(rc, 3)
        self.assertIn("secret:google-api-key", err)
        self.assertIn("navigation/bad/leak.md:4", err)
        self.assertEqual(self.sb.snapshot(), snap)

        # ---- --new-feature needs a category
        rc, _, err = self.sb.cli("import", "--feature", "arcade", "--new-feature", "--name", "A",
                                 "--description", "d", "--paths", "navigation/games/pong.md")
        self.assertEqual(rc, 2)

        # ---- real import of a new feature
        rc, out, err = self.sb.cli("import", "--feature", "arcade", "--new-feature", "--name", "Arcade",
                                   "--description", "Small arcade games", "--category", "general",
                                   "--labels", "student-safe", "--paths", "navigation/games/pong.md")
        self.assertEqual(rc, 0, out + err)
        branch = git(self.kit, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.assertTrue(branch.startswith("import/arcade-"), branch)
        msg = git(self.kit, "log", "-1", "--format=%B")
        self.assertTrue(msg.startswith("Import arcade from upstream"))
        for bad in ("Claude", "Co-Authored", "Generated with"):
            self.assertNotIn(bad, msg)
        self.assertEqual(git(self.kit, "status", "--porcelain"), "")
        for p in ("navigation/games/pong.md", "_layouts/game.html", "_includes/game-nav.html",
                  "assets/js/engine/core.js", "assets/js/engine/util.js", "assets/js/pong/pong.js", "images/pong/bg.png"):
            self.assertTrue(os.path.isfile(os.path.join(self.kit, p)), p)
        self.assertIn('(window.baseurl || "") + "/navigation/games"', read(self.kit, "assets/js/pong/pong.js"))
        self.assertIn("{{site.baseurl}}/navigation/games", read(self.kit, "navigation/games/pong.md"))
        self.assertNotIn(DENY["delnorte"].encode(), read(self.kit, "images/pong/bg.png", binary=True))
        self.assertIn("validator: python3 scripts/apply_kit.py --check -> ok", out)
        cat = self.catalog()
        self.assertEqual(cat.paths("arcade")[0], "navigation/games/pong.md")
        self.assertIn("_layouts/game.html", cat.paths("arcade"))
        self.assertEqual(cat.features["arcade"]["category"], "general")
        self.assertEqual(cat.features["arcade"]["labels"], ["student-safe"])
        self.assertIn("arcade", cat.data["kits"]["business"]["features"])
        self.assertNotIn("arcade", cat.data["kits"]["education"]["features"])
        self.assertEqual(cat.paths("extras"), ["features/media"])
        self.assertEqual(cat.validate(self.kit), [])
        self.assert_protected_untouched()

        # ---- project import registers it with make
        rc, out, err = self.sb.cli("import", "--feature", "games", "--paths", "_projects/games/tiny")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("games/tiny:dev", read(self.kit, "_projects/.makeprojects"))
        self.assertEqual(self.catalog().paths("games"), ["_projects/games/tiny"])

        # ---- conflict with another feature, then --move
        rc, out, err = self.sb.cli("import", "--feature", "games", "--paths", "media")
        self.assertEqual(rc, 1)
        self.assertIn("already claimed by feature 'extras'", err)
        rc, out, err = self.sb.cli("import", "--feature", "games", "--paths", "media", "--move")
        self.assertEqual(rc, 0, out + err)
        cat = self.catalog()
        self.assertEqual(cat.paths("extras"), [])
        self.assertIn("features/media", cat.paths("games"))
        self.assertEqual(read(self.kit, "features/media/media.md"), "---\nlayout: base\n---\nmedia lesson\n")

        # ---- upstream moves on; update pulls only the feature's paths
        write(self.sb.up, "assets/js/pong/pong.js", "import { x } from '../engine/core.js';\nexport default x + 1;\n")
        write(self.sb.up, "navigation/games/pong.md", read(self.sb.up, "navigation/games/pong.md") + "\nv2\n")
        write(self.sb.up, "README.md", "upstream readme v2\n")
        write(self.sb.up, "_config.yml", "title: OCS v2\n")
        write(self.sb.up, "navigation/games/new.md", "not part of arcade\n")
        git(self.sb.up, "add", "-A")
        git(self.sb.up, "commit", "-q", "-m", "v2")
        rc, out, err = self.sb.cli("update", "--feature", "arcade", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("update    assets/js/pong/pong.js (+1 -2)", out)
        self.assertNotIn("new.md", out)
        rc, out, err = self.sb.cli("update", "--feature", "arcade")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(git(self.kit, "rev-parse", "--abbrev-ref", "HEAD").strip().startswith("update/arcade-"))
        self.assertIn("x + 1", read(self.kit, "assets/js/pong/pong.js"))
        self.assertTrue(read(self.kit, "navigation/games/pong.md").endswith("v2\n"))
        self.assertFalse(os.path.exists(os.path.join(self.kit, "navigation/games/new.md")))
        self.assert_protected_untouched()
        self.assertEqual(self.catalog().validate(self.kit), [])
        # nothing left to do
        rc, out, err = self.sb.cli("update", "--feature", "arcade")
        self.assertEqual(rc, 0)
        self.assertIn("up to date", out)

        # ---- check command on the result
        rc, out, err = self.sb.cli("check", "--canonical", "")
        self.assertEqual(rc, 0, out + err)

    def test_scan_masks_denylisted_terms(self):
        term = DENY["nighthawk"].lower()
        write(self.sb.up, "navigation/%s-arcade/game.md" % term, "---\nlayout: base\n---\nhi\n")
        git(self.sb.up, "add", "-A")
        git(self.sb.up, "commit", "-q", "-m", "more")
        report = os.path.join(self.sb.tmp, "report.md")
        rc, out, err = self.sb.cli("scan", "-o", report)
        self.assertEqual(rc, 0, err)
        text = read(self.sb.tmp, "report.md")
        self.assertNotIn(term, text.lower())
        self.assertIn("navigation/[masked]-arcade", text)
        self.assertIn("masked as [masked]", text)
        rc, out, err = self.sb.cli("scan", "--json")
        self.assertNotIn(term, out.lower())
        json.loads(out)
        rc, out, err = self.sb.cli("scan", "--no-mask")
        self.assertIn("navigation/%s-arcade" % term, out)

    def test_update_blocks_leak(self):
        rc, out, err = self.sb.cli("import", "--feature", "games", "--paths", "navigation/games/pong.md")
        self.assertEqual(rc, 0, out + err)
        head = git(self.kit, "rev-parse", "HEAD")
        write(self.sb.up, "assets/js/pong/pong.js", "const team = '%s';\n" % DENY["nighthawk"])
        git(self.sb.up, "commit", "-qam", "leak")
        snap = self.sb.snapshot()
        rc, out, err = self.sb.cli("update", "--feature", "games")
        self.assertEqual(rc, 3)
        self.assertIn("deny:nighthawk", err)
        self.assertEqual(self.sb.snapshot(), snap)
        self.assertEqual(git(self.kit, "rev-parse", "HEAD"), head)

    def test_failed_validator_leaves_uncommitted(self):
        write(self.kit, "scripts/apply_kit.py", "import sys; print('broken kit'); sys.exit(1)\n")
        git(self.kit, "commit", "-qam", "break validator")
        head = git(self.kit, "rev-parse", "HEAD")
        rc, out, err = self.sb.cli("import", "--feature", "games", "--paths", "navigation/games/pong.md")
        self.assertEqual(rc, 4)
        self.assertIn("broken kit", err)
        self.assertEqual(git(self.kit, "rev-parse", "HEAD"), head)  # nothing committed

    def test_pr_uses_gh(self):
        calls = []
        ctx = self.sb.context()
        plan = transfer.do_import(ctx, "games", ["navigation/games/pong.md"], pr=True, date="20260101",
                                  pr_runner=lambda cmd: calls.append(cmd) or 0)
        self.assertEqual(plan.branch, "import/games-20260101")
        self.assertEqual(calls[0], ["git", "push", "-u", "origin", "import/games-20260101"])
        self.assertEqual(calls[1][:3], ["gh", "pr", "create"])
        self.assertNotIn("Claude", " ".join(calls[1]))

    def test_branch_names_unique(self):
        ctx = self.sb.context()
        transfer.do_import(ctx, "games", ["navigation/games/pong.md"], date="20260101")
        git(self.kit, "checkout", "-q", "main")
        ctx = self.sb.context()
        plan = transfer.do_import(ctx, "games", ["navigation/games/breakout.md"], date="20260101")
        self.assertEqual(plan.branch, "import/games-20260101-2")
        self.assertIn("images/breakout/missing.png", plan.resolution.missing)

    def test_excluded_and_unknown(self):
        rc, _, err = self.sb.cli("import", "--feature", "games", "--paths", "navigation/about.md")
        self.assertEqual(rc, 1)
        self.assertIn("excluded", err)
        rc, _, err = self.sb.cli("import", "--feature", "nope", "--paths", "navigation/games/pong.md")
        self.assertEqual(rc, 1)
        self.assertIn("unknown feature", err)
        rc, _, err = self.sb.cli("import", "--feature", "games", "--paths", "does/not/exist")
        self.assertEqual(rc, 1)
        self.assertIn("no upstream files match", err)

    def test_protected_never_overwritten_by_import(self):
        ctx = self.sb.context()
        plan = transfer.do_import(ctx, "games", ["README.md", "navigation/games/pong.md"], dry_run=True)
        rec = [f for f in plan.files if f["kit"] == "README.md"][0]
        self.assertEqual(rec["action"], "protected")
        self.assertNotIn("README.md", plan.registration["added"])

    def test_no_catalog_is_reported(self):
        os.remove(os.path.join(self.kit, "kits", "catalog.json"))
        rc, out, err = self.sb.cli("scan")
        self.assertEqual(rc, 0)
        self.assertIn("catalog: MISSING", out)
        rc, out, err = self.sb.cli("import", "--feature", "games", "--paths", "navigation/games/pong.md")
        self.assertEqual(rc, 1)
        self.assertIn("catalog", err)


if __name__ == "__main__":
    unittest.main()
