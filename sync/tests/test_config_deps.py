import json
import os
import shutil
import tempfile
import unittest

from helpers import DENY, SYNC_CONFIG, Sandbox

from kitsync import config, deps, globs


class GlobTest(unittest.TestCase):
    def test_semantics(self):
        m = globs.match
        self.assertTrue(m("*.pyc", "a/b/c.pyc"))
        self.assertTrue(m(".env", ".env"))
        self.assertTrue(m(".env", "sub/.env"))
        self.assertFalse(m(".env", ".env.example"))
        self.assertTrue(m("/git", "git"))
        self.assertFalse(m("/git", "x/git"))
        self.assertTrue(m("linuxcyberA/", "linuxcyberA/a/b.html"))
        self.assertTrue(m("images/about/**", "images/about/x.png"))
        self.assertTrue(m("images/about", "images/about/x.png"))
        self.assertFalse(m("images/about", "images/aboutme.png"))
        self.assertTrue(m("_projects/*/tiny", "_projects/games/tiny/a.js"))
        self.assertFalse(m("a/*.md", "a/b/c.md"))
        self.assertTrue(m("a/**/c.md", "a/c.md"))
        self.assertTrue(m(".github/workflows/**", ".github/workflows/x.yml"))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.SyncConfig(json.loads(json.dumps(SYNC_CONFIG)))

    def test_exclude_and_always_excluded(self):
        self.assertTrue(self.cfg.is_excluded("navigation/about.md"))
        self.assertTrue(self.cfg.is_excluded("images/about/a.png"))
        self.assertTrue(self.cfg.is_excluded("x/debug.log"))
        self.assertTrue(self.cfg.is_excluded("node_modules/x.js"))
        self.assertTrue(self.cfg.is_excluded("_site/index.html"))
        self.assertFalse(self.cfg.is_excluded("navigation/games/pong.md"))
        self.assertFalse(self.cfg.is_excluded("assets/js/vendor/lib.js"))

    def test_protect(self):
        for p in ("_config.yml", "kits/catalog.json", "README.md", "scripts/apply_kit.py"):
            self.assertTrue(self.cfg.is_protected(p), p)
        self.assertFalse(self.cfg.is_protected("navigation/README.md.bak"))

    def test_map_roundtrip(self):
        self.assertEqual(self.cfg.to_kit("media/a/b.md"), "features/media/a/b.md")
        self.assertEqual(self.cfg.to_upstream("features/media/a/b.md"), "media/a/b.md")
        self.assertEqual(self.cfg.to_kit("mediax/a.md"), "mediax/a.md")

    def test_replace_rules(self):
        dom = "https://" + DENY["domain"]
        js, hits = self.cfg.apply_replace("a/x.js", 'location.href = "%s/stocks/home";' % dom)
        self.assertEqual(js, 'location.href = (window.baseurl || "") + "/stocks/home";')
        self.assertEqual(hits, [("js-domain", 1)])
        md, hits = self.cfg.apply_replace("a/x.md", "[home](%s/navigation)" % dom)
        self.assertEqual(md, "[home]({{site.baseurl}}/navigation)")
        # rules are file-type scoped
        py, hits = self.cfg.apply_replace("a/x.py", dom)
        self.assertEqual((py, hits), (dom, []))

    def test_literal_replace(self):
        c = config.SyncConfig({"replace": [{"pattern": "a.b", "replacement": "X\\1"}]})
        self.assertEqual(c.apply_replace("f.md", "a.b axb")[0], "X\\1 axb")

    def test_bad_config(self):
        with self.assertRaises(config.ConfigError):
            config.SyncConfig({"replace": [{"replacement": "x"}]})
        with self.assertRaises(config.ConfigError):
            config.SyncConfig({"map": [{"upstream": "a/"}]})

    def test_shipped_configs_load(self):
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs")
        for n in ("pages", "flask", "spring"):
            c = config.load(os.path.join(here, "%s.kit.sync.json" % n))
            self.assertTrue(c.upstream_url.startswith("https://github.com/Open-Coding-Society/"))
            self.assertTrue(c.is_protected("kits/catalog.json"))
            self.assertTrue(c.is_excluded(".env"))
        pages = config.load(os.path.join(here, "pages.kit.sync.json"))
        for p in ("navigation/about.md", "images/about/a.png", "linuxcyberA/x", "git", "newsignup.html",
                  "assets/js/projects/x/y.js", "CNAME"):
            self.assertTrue(pages.is_excluded(p), p)
        for p in ("_config.yml", "README.md", ".env.example", "assets/js/api/config.js",
                  "_includes/head-custom.html", "scripts/apply_kit.py", ".github/workflows/a.yml"):
            self.assertTrue(pages.is_protected(p), p)
        self.assertEqual(pages.to_kit("navigation/predictors/grade-predictor.html"), "features/predictors/grade-predictor.html")

    def test_find_config(self):
        d = tempfile.mkdtemp()
        try:
            with self.assertRaises(config.ConfigError):
                config.find_config(d)
            with open(os.path.join(d, "kit.sync.json"), "w") as f:
                json.dump({"name": "x", "upstream": {"url": "u"}}, f)
            self.assertEqual(config.find_config(d).name, "x")
        finally:
            shutil.rmtree(d)


class ExtractRefsTest(unittest.TestCase):
    def cands(self, path, text):
        return [r.candidates[0] for r in deps.extract_refs(path, text)]

    def test_layout_and_includes(self):
        c = self.cands("navigation/a.md", "---\nlayout: game\ntitle: x\n---\n{% include nav/x.html %}\n{%- include_relative part.md %}")
        self.assertIn("_layouts/game.html", c)
        self.assertIn("_includes/nav/x.html", c)
        self.assertIn("navigation/part.md", c)

    def test_assets(self):
        text = ('<script src="{{site.baseurl}}/assets/js/a.js"></script>\n'
                '<link rel="stylesheet" href="{{ site.baseurl }}/assets/css/s.css">\n'
                '![x]({{site.baseurl}}/images/p.png)\n<img src="/images/q.jpg">\n'
                '<a href="{{site.baseurl}}/navigation/page">page link is not a file</a>\n'
                '<img src="https://cdn.example.com/x.png"> {{site.data.games}}')
        c = self.cands("navigation/a.md", text)
        for p in ("assets/js/a.js", "assets/css/s.css", "images/p.png", "images/q.jpg", "_data/games.yml"):
            self.assertIn(p, c)
        self.assertFalse([x for x in c if "navigation/page" in x or "cdn" in x])
        css = [r for r in deps.extract_refs("a.md", text) if r.candidates[0] == "assets/css/s.css"][0]
        self.assertIn("assets/css/s.scss", css.candidates)

    def test_js_imports(self):
        text = ("import A from './a.js';\nimport { B } from '../lib/b.js';\nimport C from '@assets/js/c/c.js';\n"
                "const D = await import('/assets/js/d.js');\nimport * as THREE from 'three';\nexport { E } from './e';\n")
        c = self.cands("assets/js/game/main.js", text)
        for p in ("assets/js/game/a.js", "assets/js/lib/b.js", "assets/js/c/c.js", "assets/js/d.js", "assets/js/game/e.js"):
            self.assertIn(p, c)
        self.assertNotIn("assets/js/game/three.js", c)

    def test_notebook_cells(self):
        nb = {"cells": [{"cell_type": "raw", "source": ["---\n", "layout: post\n", "---"]},
                        {"cell_type": "code", "source": ["%%js\n", "import G from '@assets/js/GameEngine/G.js';\n"]}]}
        c = self.cands("_notebooks/x.ipynb", json.dumps(nb))
        self.assertIn("_layouts/post.html", c)
        self.assertIn("assets/js/GameEngine/G.js", c)

    def test_scss_import(self):
        c = deps.extract_refs("_sass/main.scss", '@import "minima/base";')[0].candidates
        self.assertIn("_sass/minima/_base.scss", c)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.ctx = self.sb.context()

    def tearDown(self):
        self.sb.cleanup()

    def test_closure(self):
        res = self.ctx.resolve(["navigation/games/pong.md"])
        # transitive: pong.md -> layout game -> include + engine/core.js -> util.js
        for p in ("_layouts/game.html", "_includes/game-nav.html", "assets/js/engine/core.js",
                  "assets/js/engine/util.js", "assets/js/pong/pong.js", "images/pong/bg.png"):
            self.assertIn(p, res.import_paths, p)
        self.assertIn("_layouts/base.html", res.satisfied)  # already in the kit
        self.assertNotIn("_layouts/base.html", res.import_paths)
        self.assertEqual(res.missing, {})

    def test_missing_reported(self):
        res = self.ctx.resolve(["navigation/games/breakout.md"])
        self.assertIn("images/breakout/missing.png", res.missing)
        self.assertEqual(res.missing["images/breakout/missing.png"][0][0], "navigation/games/breakout.md")

    def test_excluded_dependency(self):
        self.ctx.up.files.add("navigation/x.md")
        orig = self.ctx.up.read_text
        self.ctx.up.read_text = lambda p: "<img src=\"/images/about/me.png\">" if p == "navigation/x.md" else orig(p)
        res = self.ctx.resolve(["navigation/x.md"])
        self.assertIn("images/about/me.png", res.excluded)
        self.assertNotIn("images/about/me.png", res.import_paths)

    def test_generated_outputs(self):
        res = self.ctx.resolve(["_projects/games/tiny/index.md", "_projects/games/tiny/tiny.js"])
        self.assertIn("assets/js/projects/tiny/tiny.js", res.generated)
        self.assertEqual(res.missing, {})

    def test_no_follow(self):
        res = self.ctx.resolve(["navigation/games/pong.md"], follow=False)
        self.assertEqual(res.import_paths, {"navigation/games/pong.md"})


if __name__ == "__main__":
    unittest.main()
