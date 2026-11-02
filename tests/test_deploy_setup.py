"""deploy/setup.py: generated config, re-runs that keep secrets, domain changes, profiles."""
import importlib.util
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
spec = importlib.util.spec_from_file_location("deploy_setup", DEPLOY / "setup.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

SECRETS = ["SECRET_KEY", "RESET_TOKEN_SECRET", "ADMIN_PASSWORD", "TEACHER_PASSWORD", "DEFAULT_PASSWORD"]
GENERATED = [".env", "flask.env", "spring.env", "CREDENTIALS.txt"]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        shutil.copytree(DEPLOY / "templates", self.tmp / "templates")
        shutil.copytree(DEPLOY / "profiles", self.tmp / "profiles")
        patcher = mock.patch.object(ds, "HERE", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = {k: v for k, v in os.environ.items() if not k.endswith("_HOST_PORT")}
        envp = mock.patch.dict(os.environ, env, clear=True)
        envp.start()
        self.addCleanup(envp.stop)

    def run_setup(self, *argv):
        with mock.patch("builtins.print"):
            self.assertEqual(ds.main(list(argv)), 0)

    def env(self, name):
        return ds.parse_env((self.tmp / name).read_text())

    def test_fresh_generic(self):
        self.run_setup()
        f, s, c = self.env("flask.env"), self.env("spring.env"), self.env(".env")
        self.assertEqual(f["IS_PRODUCTION"], "true")
        self.assertEqual(f["SITE_DOMAIN"], "example.org")
        self.assertEqual(s["SITE_DOMAIN"], "spring.example.org")
        for e in (f, s):
            self.assertEqual(e["CORS_ORIGINS"], "https://pages.example.org")
            self.assertEqual(e["COOKIE_DOMAIN"], ".example.org")
            self.assertEqual(e["FRONTEND_URL"], "https://pages.example.org")
            self.assertEqual(e["KIT"], "education")
        self.assertEqual(f["RESET_TOKEN_SECRET"], s["RESET_TOKEN_SECRET"])
        self.assertGreaterEqual(len(ds.base64.b64decode(s["JWT_SECRET"])), 32)
        for key in ("ADMIN_PASSWORD", "TEACHER_PASSWORD", "DEFAULT_PASSWORD"):
            self.assertEqual(ds.password_problems(s[key]), [])
            self.assertEqual(f[key], s[key])
        self.assertEqual(f["JWT_TOKEN_NAME"], "jwt_python_flask")
        self.assertEqual(s["JWT_TOKEN_NAME"], "jwt_java_spring")
        # no local-http cookie overrides in production
        self.assertNotIn("jwt.cookie.secure", s)
        self.assertEqual(c["FLASK_HOST_PORT"], "8587")
        for name in GENERATED:
            self.assertEqual(stat.S_IMODE((self.tmp / name).stat().st_mode), 0o600, name)
        self.assertTrue((self.tmp / "data/flask/instance").is_dir())
        self.assertTrue((self.tmp / "data/spring/volumes").is_dir())

    def test_domain_change_keeps_secrets_and_edits(self):
        self.run_setup("--admin-email", "it@first.org", "--domain", "first.org")
        before_f, before_s = self.env("flask.env"), self.env("spring.env")
        p = self.tmp / "flask.env"
        p.write_text(p.read_text().replace("GEMINI_API_KEY=\n", "GEMINI_API_KEY=abc\n"))
        self.run_setup("--domain", "myschool.org")
        f, s = self.env("flask.env"), self.env("spring.env")
        for key in SECRETS:
            self.assertEqual(f[key], before_f[key], key)
        self.assertEqual(s["JWT_SECRET"], before_s["JWT_SECRET"])
        self.assertEqual(f["GEMINI_API_KEY"], "abc")
        self.assertEqual(f["COOKIE_DOMAIN"], ".myschool.org")
        self.assertEqual(s["SITE_DOMAIN"], "spring.myschool.org")
        self.assertEqual(s["ADMIN_EMAIL"], "it@first.org")
        conf = (self.tmp / "out/nginx/ocs-kit-flask.conf").read_text()
        self.assertIn("server_name flask.myschool.org;", conf)
        self.assertNotIn("first.org", conf)
        self.run_setup()      # no options: everything remembered
        self.assertEqual(self.env("flask.env")["SITE_DOMAIN"], "myschool.org")

    def test_nginx_render(self):
        self.run_setup("--domain", "myschool.org", "--flask-port", "1111", "--spring-ws-port", "2222")
        flask = (self.tmp / "out/nginx/ocs-kit-flask.conf").read_text()
        spring = (self.tmp / "out/nginx/ocs-kit-spring.conf").read_text()
        self.assertIn("proxy_pass http://127.0.0.1:1111;", flask)
        self.assertIn("proxy_pass http://127.0.0.1:2222;", spring)
        self.assertIn("server_name spring.myschool.org;", spring)
        self.assertIn("$http_upgrade", spring)      # nginx's own variables survive
        self.assertNotIn("${", flask + spring)

    def test_cockpit_profile(self):
        self.run_setup("--domain", "myschool.org")   # first generic, as a switch must override its ports
        self.run_setup("--profile", "cockpit")
        c, f, s = self.env(".env"), self.env("flask.env"), self.env("spring.env")
        self.assertEqual(c["COMPOSE_PROJECT_NAME"], "ocs-kit")
        self.assertEqual((c["FLASK_HOST_PORT"], c["SPRING_HOST_PORT"], c["SPRING_WS_HOST_PORT"], c["SOCKET_HOST_PORT"]),
                         ("8687", "8685", "8689", "8600"))
        self.assertEqual(f["COOKIE_DOMAIN"], "")
        self.assertEqual(s["COOKIE_DOMAIN"], "")
        self.assertEqual(f["JWT_TOKEN_NAME"], "kit_jwt_python_flask")
        self.assertEqual(f["SESSION_COOKIE_NAME"], "kit_sess_python_flask")
        self.assertEqual(s["JWT_TOKEN_NAME"], "kit_jwt_java_spring")
        self.assertEqual(s["SESSION_COOKIE_NAME"], "kit_sess_java_spring")
        self.assertEqual(f["CORS_ORIGINS"], "https://kit-pages.myschool.org")
        self.assertEqual(s["SITE_DOMAIN"], "kit-spring.myschool.org")
        conf = (self.tmp / "out/nginx/ocs-kit-flask.conf").read_text()
        self.assertIn("server_name kit-flask.myschool.org;", conf)
        self.assertIn("127.0.0.1:8687", conf)
        self.run_setup("--socket-port", "8700")      # a profile port can be overridden, and it sticks
        self.run_setup()
        self.assertEqual(self.env(".env")["SOCKET_HOST_PORT"], "8700")
        self.run_setup("--cookie-domain", ".myschool.org")
        self.assertEqual(self.env("flask.env")["COOKIE_DOMAIN"], ".myschool.org")
        self.assertEqual(self.env("flask.env")["JWT_TOKEN_NAME"], "kit_jwt_python_flask")

    def test_env_var_ports(self):
        with mock.patch.dict(os.environ, {"FLASK_HOST_PORT": "18587"}):
            self.run_setup()
        self.assertEqual(self.env(".env")["FLASK_HOST_PORT"], "18587")

    def test_refuses_root_and_bad_input(self):
        with mock.patch.object(os, "geteuid", return_value=0), self.assertRaises(SystemExit):
            ds.main([])
        for bad in (["--domain", "bad domain"], ["--kit", "nope"], ["--admin-email", "x"],
                    ["--flask-port", "99999"], ["--flask-port", "9", "--socket-port", "9"], ["--profile", "../x"]):
            with self.assertRaises(SystemExit):
                ds.main(bad)
        self.assertFalse((self.tmp / ".env").exists())

    def test_foreign_data_stops_with_the_fix(self):
        self.run_setup()
        before = (self.tmp / "flask.env").read_text()
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1), \
                mock.patch.object(os, "getgid", return_value=os.getgid() + 1):
            with self.assertRaises(SystemExit) as ctx:
                ds.main(["--domain", "other.org"])
            expected = "docker run --rm -v %s:/d alpine chown -R %d:%d /d" % (
                self.tmp / "data/spring/volumes", os.getuid(), os.getgid())
        self.assertIn(expected, str(ctx.exception))
        self.assertEqual((self.tmp / "flask.env").read_text(), before)

    def test_generated_passwords(self):
        for _ in range(200):
            pw = ds.generate_password()
            self.assertEqual(ds.password_problems(pw), [])
            self.assertIsNone(re.search(r"[$#\s'\"]", pw))

    def test_kit_uids_match_catalog(self):
        import json
        catalog = json.loads((DEPLOY.parent / "kits/catalog.json").read_text())
        text = json.dumps(catalog)
        for staff, member in ds.ROLE_UIDS.values():
            self.assertIn('"%s"' % staff, text)
            self.assertIn('"%s"' % member, text)


if __name__ == "__main__":
    sys.exit(unittest.main())
