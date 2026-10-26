"""Tests for ocs_kit_installer.py (stdlib unittest).

Run:  python3 -m unittest discover -s tests -v

Logic tests use a small fixture catalog with made-up feature ids so they don't break when the
real kits/catalog.json changes; separate tests check the real catalog is consistent.
"""

import base64
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ocs_kit_installer as ki  # noqa: E402

FIXTURE = {
    "version": 1,
    "categories": {"core": {"name": "Core", "description": "Always on."},
                   "general": {"name": "General", "description": "For anyone."},
                   "school": {"name": "School", "description": "Classroom only."}},
    "labels": {"needs-api-key": "Needs a key.", "privacy-sensitive": "Careful.", "safe": "Fine."},
    "roles": {"admin": {"description": "Runs it."}, "staff": {"description": "Helps."},
              "member": {"description": "Uses it."}},
    "feature_management": {"description": "Only admins change features."},
    "features": {
        "base": {"name": "Base", "always_on": True, "category": "core", "labels": ["safe"],
                 "setup": {"summary": "Works.", "settings": [
                     {"key": "MAIL_USERNAME", "service": "spring", "required": False, "secret": False,
                      "help": "SMTP user."}], "steps": ["Sign in."], "verify": "Open profile."}},
        "alpha": {"name": "Alpha", "category": "general", "labels": ["safe"],
                  "setup": {"summary": "Pages key.", "settings": [
                      {"key": "kit.alpha_key", "service": "pages", "required": False, "secret": False,
                       "help": "Public key."}]}},
        "beta": {"name": "Beta", "category": "general", "labels": ["needs-api-key"],
                 "setup": {"summary": "Needs a key.", "settings": [
                     {"key": "BETA_KEY", "service": "flask", "required": True, "secret": True,
                      "help": "Beta key.", "get_it": "https://example.org/key"},
                     {"key": "BETA_KEY", "service": "spring", "required": False, "secret": True,
                      "help": "Same key."}],
                     "steps": ["Add the key."], "verify": "Ask it something."}},
        "gamma": {"name": "Gamma", "category": "school", "labels": [],
                  "setup": {"settings": [
                      {"key": "BETA_KEY", "service": "flask", "required": False, "secret": True, "help": "Shared."},
                      {"key": "ADMIN_EMAIL", "service": "spring", "required": True, "secret": False,
                       "help": "Owner."}]}},
        "delta": {"name": "Delta", "category": "general", "labels": ["privacy-sensitive"]},
    },
    "kits": {
        "small": {"name": "Small Kit", "features": ["base"]},
        "class": {"name": "Class Kit", "features": ["base", "alpha", "gamma"],
                  "roles": {"admin": {"label": "Admin", "seed_uid": "admin"},
                            "staff": {"label": "Teacher", "seed_uid": "teacher"},
                            "member": {"label": "Student", "seed_uid": "student"}}},
        "all": {"name": "All Kit", "features": ["*"]},
    },
}

FLASK_ENV_EXAMPLE = """# Flask
SCHOOL_NAME=
SITE_DOMAIN=
FRONTEND_URL=
CORS_ORIGINS=
COOKIE_DOMAIN=
IS_PRODUCTION=false
SECRET_KEY=
RESET_TOKEN_SECRET=
DEFAULT_PASSWORD=
ADMIN_PASSWORD=
TEACHER_PASSWORD=
GEMINI_API_KEY=
"""

SPRING_ENV_EXAMPLE = """# Spring
SCHOOL_NAME=My School CS
FRONTEND_URL=http://localhost:4500
SITE_DOMAIN=api.example.org
CORS_ORIGINS=http://localhost:4500
COOKIE_DOMAIN=
JWT_SECRET=
RESET_TOKEN_SECRET=
KIT_DEV_MODE=false
ADMIN_EMAIL=admin@example.org
ADMIN_PASSWORD=
TEACHER_PASSWORD=
DEFAULT_PASSWORD=
IS_PRODUCTION=false
# ---------- Local HTTP development ----------
# jwt.cookie.secure=false
# jwt.cookie.same-site=Lax
# server.servlet.session.cookie.secure=false
# server.servlet.session.cookie.same-site=lax
"""

CONFIG_YML = """# Site settings
title: Your School Coding
description: "Your School computer science pages"
owner_name: Your School
github_username: your-github-username
github_repo: "your-repo-name"
baseurl: ""

# OCS-in-a-Kit settings
kit:
  school_name: "Your School"
  schools: []            # optional list
  backend:
    flask: "https://flask.example.com"
    spring: "https://spring.example.com"
    flask_local: "http://localhost:8587"
    spring_local: "http://localhost:8585"
  voicerss_api_key: ""     # text-to-speech
  features: []           # filled by presets later

# Build settings
remote_theme: jekyll/minima
plugins:
  - jekyll-remote-theme
"""


def good_answers(**kw):
    a = ki.normalize_answers({"kit": "class", "school_name": "Test High", "admin_email": "admin@test.org",
                              "install_dir": "/tmp/x"}, env={})
    a.update(kw)
    return a


class CatalogTests(unittest.TestCase):
    cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))

    def test_preset_includes_always_on_and_star(self):
        self.assertEqual(ki.preset_features(self.cat, "small"), ["base"])
        self.assertEqual(ki.preset_features(self.cat, "all"), ["base", "alpha", "beta", "gamma", "delta"])

    def test_resolve_overrides(self):
        self.assertEqual(ki.resolve_features(self.cat, "class", "+beta,-gamma"), ["base", "alpha", "beta"])
        self.assertEqual(ki.resolve_features(self.cat, "class", ["+delta"]), ["base", "alpha", "gamma", "delta"])
        self.assertEqual(ki.resolve_features(self.cat, "class", ""), ["base", "alpha", "gamma"])

    def test_resolve_explicit_and_star(self):
        self.assertEqual(ki.resolve_features(self.cat, "small", "beta, alpha"), ["base", "alpha", "beta"])
        self.assertEqual(ki.resolve_features(self.cat, "small", "*"), ki.feature_ids(self.cat))

    def test_resolve_errors(self):
        with self.assertRaises(ki.CatalogError):
            ki.resolve_features(self.cat, "class", "+alpha,beta")       # mixed styles
        with self.assertRaises(ki.CatalogError):
            ki.resolve_features(self.cat, "class", "+nope")
        with self.assertRaises(ki.CatalogError):
            ki.resolve_features(self.cat, "class", "-base")             # always on
        with self.assertRaises(ki.CatalogError):
            ki.preset_features(self.cat, "missing")

    def test_overrides_roundtrip(self):
        ov = ki.compute_overrides(self.cat, "class", ["alpha", "beta"])
        self.assertEqual(ov, ["+beta", "-gamma"])
        self.assertEqual(ki.resolve_features(self.cat, "class", ov), ["base", "alpha", "beta"])
        self.assertEqual(ki.compute_overrides(self.cat, "class", ki.preset_features(self.cat, "class")), [])
        # explicit list normalizes to overrides
        self.assertEqual(ki.normalize_overrides(self.cat, "small", "alpha"), ["+alpha"])

    def test_describe(self):
        self.assertEqual(ki.describe_selection(self.cat, "class", ["+beta", "-gamma"]),
                         "Class Kit + Beta − Gamma")
        self.assertEqual(ki.features_env_value(["+beta", "-gamma"]), "+beta,-gamma")

    def test_groups_follow_category_order(self):
        groups = ki.feature_groups(self.cat)
        self.assertEqual([g["id"] for g, _ in groups], ["core", "general", "school"])
        self.assertEqual(groups[1][1], ["alpha", "beta", "delta"])
        self.assertEqual(groups[2][0]["description"], "Classroom only.")

    def test_notes_and_api_key(self):
        notes = ki.selection_notes(self.cat, "small", ["+gamma", "+delta"])
        self.assertTrue(any("Gamma is a School feature" in n for n in notes))
        self.assertTrue(any("privacy-sensitive" in n and "Delta" in n for n in notes))
        self.assertEqual(ki.needs_api_key(self.cat, "small", []), [])
        self.assertEqual(ki.needs_api_key(self.cat, "small", ["+beta"]), ["beta"])
        info = ki.selection_info(self.cat, "all", "-beta")
        self.assertEqual(info["api_key_features"], [])
        self.assertEqual(info["overrides"], ["-beta"])

    def test_invalid_catalog(self):
        bad = json.loads(json.dumps(FIXTURE))
        bad["kits"]["small"]["features"].append("unknown")
        with self.assertRaises(ki.CatalogError):
            ki.validate_catalog(bad)

    def test_list_features_output(self):
        text = ki.format_feature_list(self.cat)
        self.assertIn("School features - Classroom only.", text)
        self.assertIn("labels: needs-api-key", text)


class RolesAndSetupTests(unittest.TestCase):
    cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))

    def test_roles(self):
        roles = ki.kit_roles(self.cat, "class")
        self.assertEqual([(r["role"], r["label"], r["seed_uid"]) for r in roles],
                         [("admin", "Admin", "admin"), ("staff", "Teacher", "teacher"), ("member", "Student", "student")])
        self.assertEqual(roles[1]["description"], "Helps.")
        # a kit without roles falls back to defaults
        self.assertEqual(ki.kit_roles_map(self.cat, "small")["staff"]["seed_uid"], "teacher")
        labels = ki.password_labels(self.cat, "class")
        self.assertIn("Student", labels["default_password"])
        self.assertIn("user: teacher", labels["teacher_password"])

    def test_role_env_values(self):
        roles = ki.kit_roles_map(self.cat, "class")
        f = ki.build_env_values("flask", good_answers(), [], {}, {}, roles)
        s = ki.build_env_values("spring", good_answers(), [], {}, {}, roles)
        self.assertEqual((f["ADMIN_UID"], f["TEACHER_UID"], f["USER_UID"]), ("admin", "teacher", "student"))
        self.assertEqual(f["TEACHER_USER"], "Teacher User")
        self.assertEqual(s["TEACHER_NAME"], "Teacher User")
        self.assertEqual(s["USER_NAME"], "Student User")

    def test_setup_plan_merges_shared_settings(self):
        plan = ki.feature_setup_plan(self.cat, "all", [])
        by_feature = {e["feature"]: e for e in plan}
        beta = {st["id"]: st for st in by_feature["beta"]["settings"]}
        self.assertTrue(beta["flask:BETA_KEY"]["required"])
        self.assertEqual(beta["flask:BETA_KEY"]["used_by"], ["Beta", "Gamma"])
        self.assertEqual(by_feature["gamma"]["all_settings"], ["flask:BETA_KEY", "spring:ADMIN_EMAIL"])
        self.assertEqual([st["id"] for st in by_feature["gamma"]["settings"]], ["spring:ADMIN_EMAIL"])

    def test_settings_per_service(self):
        settings = {"flask:BETA_KEY": "k1", "BETA_KEY": "k2", "kit.alpha_key": "pub"}
        self.assertEqual(ki.settings_for_service(self.cat, "all", [], settings, "flask"), {"BETA_KEY": "k1"})
        self.assertEqual(ki.settings_for_service(self.cat, "all", [], settings, "spring"), {"BETA_KEY": "k2"})
        self.assertEqual(ki.settings_for_service(self.cat, "all", [], settings, "pages"), {"kit.alpha_key": "pub"})
        # disabled feature -> its settings are not written
        self.assertEqual(ki.settings_for_service(self.cat, "small", [], settings, "flask"), {})
        edits, bad = ki.pages_setting_edits({"kit.alpha_key": "pub", "title": "T", "kit.backend.flask": "u", "a.b": "x"})
        self.assertEqual(edits, {"top": {"title": "T"}, "kit": {"alpha_key": "pub"}, "backend": {"flask": "u"}})
        self.assertEqual(bad, ["a.b"])

    def test_managed_keys(self):
        self.assertIn("ADMIN_PASSWORD", ki.managed_keys("flask"))
        self.assertIn("ADMIN_EMAIL", ki.managed_keys("spring"))
        self.assertNotIn("BETA_KEY", ki.managed_keys("flask"))

    def test_feature_status(self):
        installed = {"flask", "spring", "pages"}
        st = {f["feature"]: f for f in ki.feature_status(self.cat, "all", [], {}, installed)}
        self.assertEqual(st["beta"]["status"], "needs-setup")
        self.assertEqual([m["key"] for m in st["beta"]["missing"]], ["BETA_KEY"])
        self.assertEqual(st["gamma"]["status"], "ready")          # ADMIN_EMAIL is managed; BETA_KEY optional here
        self.assertEqual(st["alpha"]["status"], "ready")
        ok = {f["feature"]: f for f in ki.feature_status(self.cat, "all", [], {"BETA_KEY": "x"}, installed)}
        self.assertEqual(ok["beta"]["status"], "ready")
        existing = {f["feature"]: f for f in ki.feature_status(self.cat, "all", [], {}, installed,
                                                               existing={"flask": {"BETA_KEY": "old"}})}
        self.assertEqual(existing["beta"]["status"], "ready")
        md = ki.feature_setup_markdown(self.cat, "all", [], list(st.values()), "Test High")
        self.assertIn("# Feature setup guide - Test High", md)
        self.assertIn("| Beta | **Needs setup** |", md)
        self.assertIn("Get it: https://example.org/key", md)
        self.assertIn("Only admins change features.", md)
        self.assertIn("Check it works: Ask it something.", md)

    def test_setting_env_and_saved_answers(self):
        a = ki.normalize_answers({"settings": {"kit.alpha_key": "pub", "flask:BETA_KEY": "secret"}}, env={})
        ki.apply_setting_env(a, self.cat, env={"OCS_SETTING_MAIL_USERNAME": "smtp-user"})
        self.assertEqual(a["settings"]["spring:MAIL_USERNAME"], "smtp-user")
        saved = ki.saved_answers(a, self.cat)
        self.assertEqual(saved["settings"].get("kit.alpha_key"), "pub")
        self.assertNotIn("flask:BETA_KEY", saved["settings"])  # secret settings are never saved
        e = ki.validate_answers(good_answers(settings={"flask:BETA_KEY": "a$b"}), self.cat)
        self.assertIn("setting:flask:BETA_KEY", e)


class RealCatalogTests(unittest.TestCase):
    def test_real_catalog_is_consistent(self):
        cat = ki.load_catalog(str(ROOT / "kits" / "catalog.json"))
        cats = cat.get("categories") or {}
        labels = cat.get("labels") or {}
        for fid, f in cat["features"].items():
            if cats:
                self.assertIn(f.get("category"), cats, fid)
            for label in f.get("labels") or []:
                self.assertIn(label, labels, "%s: unknown label %s" % (fid, label))
        for kit in cat["kits"]:
            sel = ki.preset_features(cat, kit)
            self.assertTrue(set(ki.always_on_features(cat)) <= set(sel))
            self.assertEqual(ki.compute_overrides(cat, kit, sel), [])

    def test_installer_loads_catalog_next_to_script(self):
        self.assertIn("kits", ki.load_catalog())


class PasswordTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(ki.validate_password("Abcdef1!"), [])
        self.assertTrue(ki.validate_password("short1!"))
        self.assertIn("an uppercase letter", ki.validate_password("abcdefg1!"))
        self.assertIn("a symbol from " + ki.SPRING_SYMBOLS, ki.validate_password("Abcdefg12"))
        self.assertTrue(any("not allowed" in p for p in ki.validate_password("Abcdef1!$")))
        self.assertTrue(any("not allowed" in p for p in ki.validate_password("Abc def1!")))

    def test_generation(self):
        seen = set()
        for _ in range(300):
            pw = ki.generate_password()
            self.assertEqual(ki.validate_password(pw), [], pw)
            self.assertNotIn("$", pw)
            seen.add(pw)
        self.assertEqual(len(seen), 300)

    def test_secrets(self):
        jwt = ki.generate_secret("JWT_SECRET")
        self.assertGreaterEqual(len(base64.b64decode(jwt)), 32)
        self.assertEqual(len(ki.generate_secret("SECRET_KEY")), 64)


class EnvTests(unittest.TestCase):
    def test_set_env_values(self):
        text = "A=1\n#B=2\n# C = 3\nD=4\n"
        out = ki.set_env_values(text, {"A": "x", "B": "y", "C": "z", "E": "new"})
        self.assertEqual(out, "A=x\nB=y\nC=z\nD=4\nE=new\n")

    def test_prefers_uncommented_and_keeps_special_chars(self):
        text = "# KEY=doc example\nKEY=old\n"
        out = ki.set_env_values(text, {"KEY": r"a\1&b\g<0>"})
        self.assertEqual(out, "# KEY=doc example\nKEY=a\\1&b\\g<0>\n")

    def test_append_without_trailing_newline(self):
        self.assertEqual(ki.set_env_values("A=1", {"B": "2"}), "A=1\nB=2\n")

    def test_dotted_keys_and_comment(self):
        out = ki.apply_env("spring", SPRING_ENV_EXAMPLE, {}, local=True)
        for k, v in ki.SPRING_LOCAL_COOKIE_LINES:
            self.assertIn("\n%s=%s\n" % (k, v), out)
        prod = ki.apply_env("spring", out, {}, local=False)
        for k, v in ki.SPRING_LOCAL_COOKIE_LINES:
            self.assertIn("\n# %s=%s\n" % (k, v), prod)
            self.assertNotIn(k, ki.parse_env(prod))

    def test_parse_env(self):
        env = ki.parse_env("A=1\n# B=2\nC='x y'\nD=\"q\"\n\nE=a=b\n")
        self.assertEqual(env, {"A": "1", "C": "x y", "D": "q", "E": "a=b"})

    def test_build_env_fresh_local(self):
        cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))
        a = good_answers(preview_port=14500)
        shared = {}
        f = ki.build_env_values("flask", a, ["+beta"], {}, shared)
        s = ki.build_env_values("spring", a, ["+beta"], {}, shared)
        self.assertEqual(f["RESET_TOKEN_SECRET"], s["RESET_TOKEN_SECRET"])
        self.assertEqual(f["ADMIN_PASSWORD"], s["ADMIN_PASSWORD"])
        self.assertEqual(ki.validate_password(f["DEFAULT_PASSWORD"]), [])
        self.assertEqual(f["KIT"], "class")
        self.assertEqual(f["FEATURES"], "+beta")
        self.assertEqual(f["IS_PRODUCTION"], "false")
        self.assertEqual(f["COOKIE_DOMAIN"], "")
        self.assertIn("http://localhost:14500", f["CORS_ORIGINS"])
        self.assertEqual(f["FRONTEND_URL"], "http://localhost:14500")
        self.assertEqual(s["ADMIN_EMAIL"], "admin@test.org")
        self.assertIn("JWT_SECRET", s)
        self.assertNotIn("GEMINI_API_KEY", f)  # blank key never wipes an existing one
        del cat

    def test_build_env_production(self):
        a = good_answers(mode="production", domain="myschool.org", github_owner="MySchool")
        f = ki.build_env_values("flask", a, [], {}, {})
        s = ki.build_env_values("spring", a, [], {}, {})
        self.assertEqual(f["CORS_ORIGINS"], "https://pages.myschool.org,https://myschool.github.io")
        self.assertEqual(f["COOKIE_DOMAIN"], ".myschool.org")
        self.assertEqual(f["SITE_DOMAIN"], "myschool.org")
        self.assertEqual(s["SITE_DOMAIN"], "spring.myschool.org")
        self.assertEqual(f["FRONTEND_URL"], "https://pages.myschool.org")
        self.assertEqual(f["IS_PRODUCTION"], "true")

    def test_preserve_on_rerun(self):
        existing = {"SECRET_KEY": "keep-me", "RESET_TOKEN_SECRET": "rts", "ADMIN_PASSWORD": "Old1!pass",
                    "TEACHER_PASSWORD": "Teach1!x", "DEFAULT_PASSWORD": "Def1!xyz"}
        a = good_answers(admin_password="New1!pass")
        v = ki.build_env_values("flask", a, [], existing, {})
        self.assertEqual(v["SECRET_KEY"], "keep-me")
        self.assertEqual(v["RESET_TOKEN_SECRET"], "rts")
        self.assertEqual(v["ADMIN_PASSWORD"], "New1!pass")     # typed wins
        self.assertEqual(v["TEACHER_PASSWORD"], "Teach1!x")    # blank keeps existing
        self.assertEqual(v["DEFAULT_PASSWORD"], "Def1!xyz")


class InstallerConfigureTests(unittest.TestCase):
    """step_configure_backend on disk: fresh install, then a re-run keeps secrets."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for kind, text in (("flask", FLASK_ENV_EXAMPLE), ("spring", SPRING_ENV_EXAMPLE)):
            (self.root / kind).mkdir()
            (self.root / kind / ".env.example").write_text(text)
        self.cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kw):
        a = good_answers(install_dir=str(self.root), **kw)
        return ki.Installer(a, self.cat, repo_base=str(self.root), ref="main")

    def test_fresh_then_rerun(self):
        self.make(features=["+beta"], settings={"flask:BETA_KEY": "bk", "ADMIN_PASSWORD": "Hack1!xx"}).step_configure_backend()
        f1 = ki.parse_env((self.root / "flask" / ".env").read_text())
        s1 = ki.parse_env((self.root / "spring" / ".env").read_text())
        self.assertEqual(oct((self.root / "flask" / ".env").stat().st_mode & 0o777), "0o600")
        self.assertEqual(f1["RESET_TOKEN_SECRET"], s1["RESET_TOKEN_SECRET"])
        self.assertEqual(s1["jwt.cookie.secure"], "false")
        self.assertEqual(s1["FEATURES"], "+beta")
        self.assertEqual(f1["COMPOSE_PROJECT_NAME"], "ocskit-flask")
        self.assertEqual(f1["BETA_KEY"], "bk")
        self.assertNotIn("BETA_KEY", s1)                       # spring value not typed
        self.assertNotEqual(f1["ADMIN_PASSWORD"], "Hack1!xx")  # settings never override managed keys
        self.assertEqual(f1["USER_UID"], "student")
        # user edits something unrelated; re-run with a new teacher password and another kit
        p = self.root / "flask" / ".env"
        p.write_text(p.read_text() + "MY_CUSTOM=1\n")
        inst = self.make(kit="all", features=["-delta"], teacher_password="Brand1!new")
        inst.step_configure_backend()
        f2 = ki.parse_env(p.read_text())
        s2 = ki.parse_env((self.root / "spring" / ".env").read_text())
        for k in ("SECRET_KEY", "RESET_TOKEN_SECRET", "ADMIN_PASSWORD", "DEFAULT_PASSWORD"):
            self.assertEqual(f1[k], f2[k], k)
        self.assertEqual(s1["JWT_SECRET"], s2["JWT_SECRET"])
        self.assertEqual(f2["TEACHER_PASSWORD"], "Brand1!new")
        self.assertEqual(f2["MY_CUSTOM"], "1")
        self.assertEqual((f2["KIT"], f2["FEATURES"]), ("all", "-delta"))
        self.assertIn("Brand1!new", inst.credentials_text())

    def test_change_features_only_touches_kit(self):
        self.make().step_configure_backend()
        before = (self.root / "flask" / ".env").read_text()
        a = good_answers(install_dir=str(self.root), action="change-features", kit="small",
                         features=["+alpha"])
        inst = ki.Installer(a, self.cat, repo_base=str(self.root), ref="main")
        inst.compose = lambda *x, **k: (0, "")
        inst.wait_http = lambda *x, **k: None
        inst.step_change_features()
        after = (self.root / "flask" / ".env").read_text()
        diff = [l for l in after.splitlines() if l not in before.splitlines()]
        self.assertEqual(sorted(diff), ["FEATURES=+alpha", "KIT=small"])

    def test_save_writes_guides(self):
        inst = self.make(features=["+beta"])
        inst.step_configure_backend()
        inst.step_save()
        guide = (self.root / ki.FEATURE_SETUP_FILE).read_text()
        self.assertIn("Beta", guide)
        creds = self.root / ki.CREDENTIALS_FILE
        self.assertEqual(oct(creds.stat().st_mode & 0o777), "0o600")
        self.assertIn("Student  user student", creds.read_text())
        saved = json.loads((self.root / ki.ANSWERS_FILE).read_text())
        self.assertNotIn("admin_password", saved)
        summary = inst.build_summary(True)
        self.assertEqual({f["feature"]: f["status"] for f in summary["features"]}["beta"], "needs-setup")


class PortOffsetTests(unittest.TestCase):
    def test_host_ports_and_override(self):
        a = good_answers(port_offset=10000)
        self.assertEqual(ki.host_ports(a), {"flask": 18587, "socket": 18500, "spring": 18585, "chat": 18589})
        self.assertEqual(ki.host_ports(good_answers()), {"flask": 8587, "socket": 8500, "spring": 8585, "chat": 8589})
        o = ki.render_compose_override("spring", 10000)
        self.assertIn('ports: !override\n      - "18585:8585"\n      - "18589:8589"', o)
        self.assertIn("image: ocskit_spring_code_runner", o)
        f = ki.render_compose_override("flask", 10000)
        self.assertIn('"18500:8500"', f)
        self.assertIn("http://localhost:18585", ki.cors_origins(a, "spring"))
        s = ki.pages_config_settings(a, [], "preview")
        self.assertEqual(s["backend"]["flask_local"], "http://localhost:18587")
        self.assertIn("preview_port", ki.validate_answers(good_answers(port_offset=10000, preview_port=18587),
                                                          ki.validate_catalog(json.loads(json.dumps(FIXTURE)))))
        self.assertIn("port_offset", ki.validate_answers(good_answers(port_offset=-5),
                                                         ki.validate_catalog(json.loads(json.dumps(FIXTURE)))))

    def test_nginx_with_offset(self):
        out = ki.render_nginx_configs("proxy_pass http://localhost:${FLASK_PORT};",
                                      "proxy_pass http://localhost:8585; proxy_pass http://localhost:8589;",
                                      "x.org", ki.host_ports({"port_offset": 1}))
        self.assertEqual(out["flask.conf"], "proxy_pass http://localhost:8588;")
        self.assertEqual(out["spring.conf"], "proxy_pass http://localhost:8586; proxy_pass http://localhost:8590;")

    def test_override_written_and_removed(self):
        cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for kind in ("flask", "spring"):
                (root / kind).mkdir()
            ki.Installer(good_answers(install_dir=tmp, port_offset=7), cat).prepare_backend_dirs()
            self.assertTrue((root / "flask" / "instance" / "volumes").is_dir())
            self.assertTrue((root / "spring" / "volumes").is_dir())
            self.assertIn('"8594:8587"', (root / "flask" / "docker-compose.override.yml").read_text())
            ki.Installer(good_answers(install_dir=tmp), cat).prepare_backend_dirs()
            override = root / "flask" / "docker-compose.override.yml"
            if ki.container_user_override("flask", os.getuid(), os.getgid()):
                self.assertNotIn("ports", override.read_text())
            else:
                self.assertFalse(override.exists())

    def _prepare(self, tmp, setup=None):
        cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))
        root = Path(tmp)
        for kind in ("flask", "spring"):
            (root / kind).mkdir()
        if setup:
            setup(root)
        ki.Installer(good_answers(install_dir=tmp), cat).prepare_backend_dirs()
        return root

    def test_data_dirs_not_world_accessible(self):
        old_umask = os.umask(0)  # worst case: nothing masked by the user's umask
        try:
            with tempfile.TemporaryDirectory() as tmp:
                def setup(root):
                    vol = root / "flask" / "instance" / "volumes"
                    (vol / "sub").mkdir(parents=True, mode=0o777)
                    (vol / "user_management.db").write_text("x")
                    os.chmod(vol / "user_management.db", 0o666)
                    os.chmod(root / "flask", 0o755)
                root = self._prepare(tmp, setup)
                for rel in ki.DATA_DIRS["flask"]:
                    self.assertTrue((root / "flask" / rel).is_dir())
                tops = [root / "flask" / rel for rel in ki.DATA_DIRS["flask"]] + [root / "spring" / "volumes"]
                for top in tops:
                    self.assertEqual(top.stat().st_mode & 0o777, ki.DATA_DIR_MODE, top)
                    for path in top.rglob("*"):
                        self.assertEqual(path.stat().st_mode & 0o007, 0, "%s open to other users" % path)
                self.assertEqual((root / "flask/instance/volumes/user_management.db").stat().st_mode & 0o777, 0o600)
                # parents and the repo itself are never touched
                self.assertEqual((root / "flask").stat().st_mode & 0o777, 0o755)
        finally:
            os.umask(old_umask)

    def test_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            out = Path(outside)
            target_file = out / "secret.db"
            target_file.write_text("x")
            os.chmod(target_file, 0o644)
            target_dir = out / "dir"
            target_dir.mkdir()
            os.chmod(target_dir, 0o755)
            spring_target = out / "spring-data"
            spring_target.mkdir()
            os.chmod(spring_target, 0o755)

            def setup(root):
                vol = root / "flask" / "instance" / "volumes"
                vol.mkdir(parents=True)
                os.symlink(str(target_file), str(vol / "link.db"))
                os.symlink(str(target_dir), str(vol / "linkdir"))
                os.symlink(str(spring_target), str(root / "spring" / "volumes"))  # the data dir itself
            root = self._prepare(tmp, setup)
            self.assertEqual(target_file.stat().st_mode & 0o777, 0o644)
            self.assertEqual(target_dir.stat().st_mode & 0o777, 0o755)
            self.assertEqual(spring_target.stat().st_mode & 0o777, 0o755)
            self.assertTrue(os.path.islink(str(root / "spring" / "volumes")))
            self.assertFalse(ki.secure_data_dir(root / "spring" / "volumes"))

    def test_container_user_override(self):
        self.assertIsNone(ki.container_user_override("flask", 1000, 1000))   # matches the image's appuser
        self.assertIsNone(ki.container_user_override("flask", 0, 0))         # root: data is chowned instead
        self.assertIsNone(ki.container_user_override("spring", 1001, 1001))  # spring runs as root in its image
        if sys.platform.startswith("linux"):
            self.assertEqual(ki.container_user_override("flask", 1001, 1002), "1001:1002")
            o = ki.render_compose_override("flask", 0, "1001:1002")
            self.assertIn('  web:\n    user: "1001:1002"', o)
            self.assertNotIn("ports", o)
            self.assertNotIn("image:", o)


class NginxTests(unittest.TestCase):
    def test_render(self):
        flask_tpl = "server_name flask.${SITE_DOMAIN};\nproxy_pass http://localhost:${FLASK_PORT};\n" \
                    "location /socket.io/ { proxy_pass http://localhost:${SOCKET_PORT}; }\n" \
                    "proxy_set_header Host $host; ${UNKNOWN}\n"
        spring_tpl = "server_name ${SITE_DOMAIN};\nproxy_set_header Upgrade $http_upgrade;\n"
        out = ki.render_nginx_configs(flask_tpl, spring_tpl, "myschool.org")
        self.assertIn("server_name flask.myschool.org;", out["flask.conf"])
        self.assertIn("localhost:8587;", out["flask.conf"])
        self.assertIn("localhost:8500;", out["flask.conf"])
        self.assertIn("$host; ${UNKNOWN}", out["flask.conf"])
        self.assertIn("server_name spring.myschool.org;", out["spring.conf"])
        self.assertIn("$http_upgrade", out["spring.conf"])

    def test_real_templates_if_present(self):
        base = ROOT.parent
        ft, st = base / "OCS-Kit-Flask/deploy/nginx.conf.template", base / "OCS-Kit-Spring/deploy/nginx.conf.template"
        if not (ft.exists() and st.exists()):
            self.skipTest("backend repos not next to this repo")
        out = ki.render_nginx_configs(ft.read_text(), st.read_text(), "example.edu")
        for text in out.values():
            self.assertNotRegex(text, r"\$\{(SITE_DOMAIN|FLASK_PORT|SOCKET_PORT)\}")
        self.assertIn("server_name flask.example.edu;", out["flask.conf"])
        self.assertIn("server_name spring.example.edu;", out["spring.conf"])

    def test_commands_and_dns(self):
        cmds = ki.nginx_commands("/srv/ocs/nginx", "myschool.org")
        self.assertTrue(any("certbot --nginx -d flask.myschool.org -d spring.myschool.org" in c for c in cmds))
        a = good_answers(mode="production", domain="myschool.org", website_publish=True, github_owner="Org")
        names = [r[1] for r in ki.dns_records(a)]
        self.assertEqual(names, ["flask.myschool.org", "spring.myschool.org", "pages.myschool.org"])


class ConfigYmlTests(unittest.TestCase):
    def test_edit(self):
        out = ki.edit_config_yml(CONFIG_YML, top={"title": "Test High", "github_repo": "site"},
                                 kit={"school_name": "Test High", "name": "class", "features": ["+beta", "-gamma"]},
                                 backend={"flask": "https://flask.t.org", "flask_local": "http://localhost:8587"})
        self.assertIn('title: "Test High"\n', out)
        self.assertIn('github_repo: "site"\n', out)
        self.assertIn('  name: "class"\n', out)
        self.assertIn('  features: ["+beta", "-gamma"]           # filled by presets later\n', out)
        self.assertIn('    flask: "https://flask.t.org"\n', out)
        self.assertIn('    spring: "https://spring.example.com"\n', out)
        self.assertIn('  voicerss_api_key: ""     # text-to-speech\n', out)
        self.assertIn("plugins:\n  - jekyll-remote-theme\n", out)
        self.assertIn("\n# Build settings\nremote_theme", out)
        self.assertEqual(out.count("features:"), 1)
        # idempotent
        again = ki.edit_config_yml(out, top={"title": "Test High", "github_repo": "site"},
                                   kit={"school_name": "Test High", "name": "class", "features": ["+beta", "-gamma"]},
                                   backend={"flask": "https://flask.t.org", "flask_local": "http://localhost:8587"})
        self.assertEqual(out, again)

    def test_multiline_list_and_missing_block(self):
        text = "title: x\nkit:\n  features:\n    - a\n    - b\n  name: old\nother: 1\n"
        out = ki.edit_config_yml(text, kit={"name": "new", "features": []})
        self.assertEqual(out, 'title: x\nkit:\n  features: []\n  name: "new"\nother: 1\n')
        out2 = ki.edit_config_yml("title: x\n", kit={"name": "k"}, backend={"flask": "u"})
        self.assertIn('kit:\n  name: "k"\n  backend:\n    flask: "u"\n', out2)

    def test_real_config_if_present(self):
        p = ROOT.parent / "OCS-Kit-Pages" / "_config.yml"
        if not p.exists():
            self.skipTest("pages repo not next to this repo")
        s = ki.pages_config_settings(good_answers(), ["+beta"], "preview")
        out = ki.edit_config_yml(p.read_text(), s["top"], s["kit"], s["backend"])
        kit_block = out[out.index("\nkit:"):]
        self.assertIn('  name: "class"', kit_block)
        self.assertIn('  features: ["+beta"]', kit_block)
        self.assertEqual(len(out.splitlines()) - len(p.read_text().splitlines()) in (0, 1), True)


class AnswerTests(unittest.TestCase):
    cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))

    def test_valid(self):
        self.assertEqual(ki.validate_answers(good_answers(), self.cat), {})

    def test_errors(self):
        a = good_answers(mode="production", domain="https://bad", admin_email="nope", admin_password="weak",
                         school_name="A $chool", install_website=True, website_preview=False, website_publish=False)
        e = ki.validate_answers(ki.normalize_answers(a, env={}), self.cat)
        for k in ("domain", "admin_email", "admin_password", "school_name", "website_preview"):
            self.assertIn(k, e)
        e2 = ki.validate_answers(good_answers(kit="nope"), self.cat)
        self.assertIn("kit", e2)
        e3 = ki.validate_answers(good_answers(features=["+zzz"]), self.cat)
        self.assertIn("features", e3)
        e4 = ki.validate_answers(good_answers(install_backend=False, install_website=False), self.cat)
        self.assertIn("install_backend", e4)
        e5 = ki.validate_answers(good_answers(preview_port=8587), self.cat)
        self.assertIn("preview_port", e5)

    def test_normalize(self):
        a = ki.normalize_answers({"domain": "https://MySchool.org/", "features": "+a, -b", "install_website": "no"},
                                 env={"OCS_ADMIN_PASSWORD": "Env1!pass"})
        self.assertEqual(a["domain"], "myschool.org")
        self.assertEqual(a["features"], ["+a", "-b"])
        self.assertFalse(a["website_preview"])
        self.assertEqual(a["admin_password"], "Env1!pass")
        saved = ki.saved_answers(a)
        self.assertNotIn("admin_password", saved)
        self.assertNotIn("gemini_api_key", saved)

    def test_warnings(self):
        self.assertTrue(ki.website_warnings(good_answers(website_publish=True)))
        self.assertFalse(ki.website_warnings(good_answers(mode="production", domain="a.org", website_publish=True)))


class DryRunTests(unittest.TestCase):
    def test_publish_dry_run_commands(self):
        cat = ki.validate_catalog(json.loads(json.dumps(FIXTURE)))
        with tempfile.TemporaryDirectory() as tmp:
            a = good_answers(install_dir=tmp, mode="production", domain="myschool.org", install_backend=True,
                             website_preview=False, website_publish=True, github_owner="my-org",
                             github_repo="cs-site")
            inst = ki.Installer(a, cat, dry_run=True)
            self.assertTrue(inst.run())
            log = "\n".join(inst.lines)
            self.assertEqual(os.listdir(tmp), [])  # nothing written
            for expected in ["gh repo fork CSA-Admin-OCS/OCS-Kit-Pages --clone=false --default-branch-only "
                             "--fork-name cs-site --org my-org",
                             "gh api -X PUT repos/my-org/cs-site/actions/permissions -F enabled=true",
                             "gh api -X POST repos/my-org/cs-site/pages -f build_type=workflow",
                             "cname=pages.myschool.org",
                             "gh workflow run jekyll-gh-pages.yml -R my-org/cs-site --ref main",
                             "git push --force origin HEAD:main",
                             "docker compose up -d --build"]:
                self.assertIn(expected, log)
            self.assertIsNotNone(inst.summary["production"])
            self.assertIn("pages.myschool.org", [r["name"] for r in inst.summary["production"]["dns"]])


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cat = ki.load_catalog()
        args = SimpleNamespace(ref="main", dry_run=True, repo_base=str(ROOT.parent), port=0, echo_log=False)
        answers = ki.normalize_answers({"install_dir": cls.tmp.name}, env={})
        cls.state = ki.GuiState(cls.cat, answers, args)
        cls.srv = ki.make_gui_server(cls.state, 0)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def req(self, path, body=None, token=True, host=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        if token:
            url += ("&" if "?" in path else "?") + "t=" + self.state.token
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                   headers={"Content-Type": "application/json"})
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_token_enforced(self):
        self.assertEqual(self.req("/", token=False)[0], 403)
        self.assertEqual(self.req("/api/state", token=False)[0], 403)
        self.assertEqual(self.req("/api/validate", {}, token=False)[0], 403)
        self.assertEqual(self.req("/?t=wrong", token=False)[0], 403)
        self.assertEqual(self.req("/", host="evil.example:%d" % self.port)[0], 403)  # DNS rebinding

    def test_page_and_state(self):
        code, html = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn("OCS-in-a-Kit installer", html)
        self.assertIn(self.state.token, html)
        self.assertNotIn("https://cdn", html)
        code, body = self.req("/api/state")
        st = json.loads(body)
        self.assertEqual(set(st["catalog"]["kits"]), set(self.cat["kits"]))
        self.assertEqual([g["id"] for g in st["groups"]], [g["id"] for g, _ in ki.feature_groups(self.cat)])
        self.assertTrue(st["dry_run"])

    def test_validation_errors(self):
        code, body = self.req("/api/validate", {"kit": "nope", "admin_email": "x", "admin_password": "weak$",
                                                 "mode": "production", "domain": ""})
        e = json.loads(body)["errors"]
        for k in ("kit", "admin_email", "admin_password", "domain", "school_name"):
            self.assertIn(k, e)
        code, body = self.req("/api/install", {"kit": "nope"})
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(self.req("/api/nothing")[0], 404)

    def test_selection(self):
        kit = next(iter(self.cat["kits"]))
        extra = [f for f in ki.feature_ids(self.cat) if f not in ki.preset_features(self.cat, kit)][:1]
        code, body = self.req("/api/selection", {"kit": kit, "features": ["+" + f for f in extra]})
        r = json.loads(body)
        self.assertEqual(r["overrides"], ["+" + f for f in extra])
        self.assertTrue(r["summary"].startswith(self.cat["kits"][kit]["name"]))
        self.assertEqual(self.req("/api/selection", {"kit": "nope"})[0], 400)

    def test_dry_run_install_via_gui(self):
        kit = next(iter(self.cat["kits"]))
        a = {"kit": kit, "school_name": "GUI High", "admin_email": "a@b.org", "install_dir": self.tmp.name,
             "install_backend": True, "install_website": True, "website_preview": True, "preview_port": 14501}
        code, body = self.req("/api/install", a)
        self.assertTrue(json.loads(body)["ok"], body)
        deadline = time.time() + 30
        while time.time() < deadline:
            p = json.loads(self.req("/api/progress?since=0")[1])
            if p["done"]:
                break
            time.sleep(0.2)
        self.assertTrue(p["done"])
        self.assertTrue(p["ok"], "\n".join(p["lines"]))
        self.assertTrue(all(s["status"] == "done" for s in p["steps"]))
        self.assertIn("[dry-run] $ docker compose up -d --build", "\n".join(p["lines"]))
        self.assertEqual(os.listdir(self.tmp.name), [])


if __name__ == "__main__":
    unittest.main()
