import json
import os
import shutil
import tempfile
import unittest

from helpers import CANONICAL, fixture_catalog, write

from kitsync import catalog


def cat(pages=None):
    return catalog.Catalog(fixture_catalog(pages), key="pages")


class RegistrationTest(unittest.TestCase):
    def test_existing_feature(self):
        c = cat({"games": ["navigation/games/pong.md"]})
        r = c.add_paths("games", ["navigation/games/breakout.md", "navigation/games/pong.md"])
        self.assertEqual(r["added"], ["navigation/games/breakout.md"])
        self.assertEqual(r["already"], ["navigation/games/pong.md"])
        self.assertEqual(c.paths("games"), ["navigation/games/pong.md", "navigation/games/breakout.md"])
        # a file under an already-registered directory is "already" listed
        c2 = cat({"games": ["navigation/games"]})
        self.assertEqual(c2.add_paths("games", ["navigation/games/x.md"])["already"], ["navigation/games/x.md"])

    def test_other_features_untouched(self):
        c = cat({"extras": ["features/media"], "course": ["_notebooks/c1"]})
        before = json.dumps({k: v for k, v in c.features.items() if k != "games"}, sort_keys=True)
        c.add_paths("games", ["navigation/games"])
        after = json.dumps({k: v for k, v in c.features.items() if k != "games"}, sort_keys=True)
        self.assertEqual(before, after)

    def test_unknown_feature(self):
        with self.assertRaises(catalog.CatalogError):
            cat().add_paths("nope", ["a"])

    def test_conflict_refused(self):
        c = cat({"extras": ["features/media"]})
        with self.assertRaises(catalog.ConflictError) as cm:
            c.add_paths("games", ["features/media"])
        self.assertIn("extras", str(cm.exception))
        self.assertIn("--move", str(cm.exception))
        with self.assertRaises(catalog.ConflictError):  # inside another feature's directory
            c.add_paths("games", ["features/media/x.md"])
        with self.assertRaises(catalog.ConflictError):  # would swallow another feature's entry
            c.add_paths("games", ["features"])
        self.assertEqual(c.paths("games"), [])
        self.assertEqual(c.paths("extras"), ["features/media"])

    def test_move(self):
        c = cat({"extras": ["features/media", "features/other"]})
        r = c.add_paths("games", ["features/media"], move=True)
        self.assertEqual(r["moved"], [("features/media", "extras")])
        self.assertEqual(c.paths("extras"), ["features/other"])
        self.assertEqual(c.paths("games"), ["features/media"])
        self.assertEqual(c.validate(), [])

    def test_move_cannot_split_a_directory(self):
        c = cat({"extras": ["features/media"]})
        with self.assertRaises(catalog.ConflictError):
            c.add_paths("games", ["features/media/x.md"], move=True)

    def test_glob_entries(self):
        c = cat({"games": ["navigation/games/*.md"]})
        self.assertEqual(c.owner("navigation/games/pong.md"), "games")
        self.assertIsNone(c.owner("navigation/games/sub/pong.md"))
        self.assertEqual(c.owner("navigation/other.md"), None)

    def test_dict_shape(self):
        data = fixture_catalog()
        data["features"]["games"]["pages"] = {"paths": ["a"], "nav": "games"}
        c = catalog.Catalog(data, key="pages")
        c.add_paths("games", ["b"])
        self.assertEqual(data["features"]["games"]["pages"], {"paths": ["a", "b"], "nav": "games"})


class NewFeatureTest(unittest.TestCase):
    def test_kits_from_category(self):
        for category, expect in (("education", ["education"]), ("general", ["business"]),
                                 ("core", ["vanilla", "education", "business"])):
            with self.subTest(category=category):
                c = cat()
                kits = c.create_feature("newthing", "New", "desc", category)
                self.assertEqual(sorted(kits), sorted(expect))
                for k, kd in c.data["kits"].items():
                    self.assertEqual("newthing" in kd["features"], k in expect)

    def test_kits_override_and_validation(self):
        c = cat()
        self.assertEqual(c.create_feature("n1", "N", "d", "general", kits=["education", "business"]), ["education", "business"])
        with self.assertRaises(catalog.CatalogError):
            c.create_feature("n2", "N", "d", "general", kits=["nope"])
        with self.assertRaises(catalog.CatalogError):
            c.create_feature("n3", "N", "d", "fun")
        with self.assertRaises(catalog.CatalogError):
            c.create_feature("n4", "N", "d", "general", labels=["not-a-label"])
        with self.assertRaises(catalog.CatalogError):
            c.create_feature("Bad_Id", "N", "d", "general")
        with self.assertRaises(catalog.CatalogError):
            c.create_feature("games", "N", "d", "general")
        with self.assertRaises(catalog.CatalogError):
            c.create_feature("n5", "", "d", "general")

    def test_setup_scaffold_and_labels(self):
        c = cat()
        c.create_feature("newthing", "New", "desc", "general", labels=["demo"], setup_summary="Just works.")
        f = c.features["newthing"]
        self.assertEqual(f["labels"], ["demo"])
        self.assertEqual(f["pages"], [])
        self.assertEqual(sorted(f["setup"]), ["settings", "steps", "summary", "verify"])
        self.assertEqual(f["setup"]["summary"], "Just works.")
        self.assertTrue(f["setup"]["verify"])
        self.assertEqual(catalog.setup_errors(c.data), [])


class ValidateTest(unittest.TestCase):
    def test_double_claims(self):
        c = cat({"games": ["a/b"], "extras": ["a/b"]})
        self.assertTrue(any("both" in e for e in c.validate()))
        c = cat({"games": ["a"], "extras": ["a/b"]})
        self.assertTrue(any("inside" in e for e in c.validate()))

    def test_paths_exist(self):
        d = tempfile.mkdtemp()
        try:
            write(d, "a/x.md", "x")
            c = cat({"games": ["a", "a/*.md"], "extras": ["missing/dir", "nomatch/*.md"]})
            errs = c.validate(d, universe=["a/x.md"])
            self.assertTrue(any("missing/dir does not exist" in e for e in errs))
            self.assertTrue(any("nomatch/*.md matches no file" in e for e in errs))
            self.assertFalse(any("'games'" in e and "exist" in e for e in errs))
        finally:
            shutil.rmtree(d)

    def test_against_canonical(self):
        canon = fixture_catalog()
        c = cat()
        self.assertEqual(c.validate(canonical=canon), [])
        canon2 = json.loads(json.dumps(canon))
        canon2["features"]["games"]["category"] = "education"
        canon2["features"]["extra2"] = dict(canon["features"]["games"])
        canon2["kits"]["business"]["features"].append("extras")
        canon2["roles"] = {"admin": {}}
        errs = c.validate(canonical=canon2)
        self.assertTrue(any("ids differ" in e for e in errs))
        self.assertTrue(any("category" in e for e in errs))
        self.assertTrue(any("kit feature lists" in e for e in errs))
        self.assertTrue(any("'roles'" in e for e in errs))

    def test_unknown_category_label_kitfeature(self):
        data = fixture_catalog()
        data["features"]["games"]["category"] = "fun"
        data["features"]["games"]["labels"] = ["nope"]
        data["kits"]["vanilla"]["features"].append("ghost")
        errs = catalog.Catalog(data).validate()
        self.assertEqual(len([e for e in errs if "unknown" in e]), 3)

    def test_setup_errors(self):
        data = fixture_catalog()
        data["features"]["games"]["setup"] = {"summary": "", "settings": [{"key": "K", "service": "django"}], "verify": "v"}
        del data["features"]["extras"]["setup"]
        errs = catalog.setup_errors(data)
        self.assertTrue(any("games': setup.summary is empty" in e for e in errs))
        self.assertTrue(any("'django'" in e for e in errs))
        self.assertTrue(any("'extras' has no setup" in e for e in errs))

    def test_save_roundtrip(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "catalog.json")
            with open(p, "w") as f:
                json.dump(fixture_catalog(), f, indent=4)
            c = catalog.Catalog.load(p)
            self.assertEqual(c.indent, 4)
            c.add_paths("games", ["x"])
            c.save()
            self.assertEqual(catalog.Catalog.load(p).paths("games"), ["x"])
        finally:
            shutil.rmtree(d)

    @unittest.skipUnless(os.path.isfile(CANONICAL), "canonical catalog not present")
    def test_new_feature_against_real_canonical(self):
        canon = catalog.load_json(CANONICAL)
        data = json.loads(json.dumps(canon))
        for f in data["features"].values():
            f["pages"] = []
        c = catalog.Catalog(data)
        label = sorted(canon["labels"])[0]
        c.create_feature("zz-new", "Z", "d", "education", labels=[label], canonical=canon)
        self.assertIn("zz-new", c.data["kits"]["education"]["features"])
        errs = c.validate(canonical=canon)
        self.assertTrue(any("zz-new" in e for e in errs))  # canonical must be updated too


if __name__ == "__main__":
    unittest.main()
