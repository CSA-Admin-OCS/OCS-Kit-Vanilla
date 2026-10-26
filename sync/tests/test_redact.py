import json
import os
import shutil
import tempfile
import unittest

from helpers import Sandbox, git

from kitsync import gitops, transfer
from kitsync.urls import has_credentials, redact_text, redact_url

TOKEN = "ghs_" + "S3cr3tT0k3nValue1234567890"


class RedactUrlTest(unittest.TestCase):
    def test_user_and_password(self):
        self.assertEqual(redact_url("https://alice:pa55word@github.com/o/r.git"), "https://github.com/o/r.git")

    def test_token_only(self):
        self.assertEqual(redact_url("https://%s@github.com/o/r.git" % TOKEN), "https://github.com/o/r.git")
        self.assertEqual(redact_url("https://x-access-token:%s@github.com/o/r" % TOKEN), "https://github.com/o/r")

    def test_port_kept(self):
        self.assertEqual(redact_url("https://u:p@git.example.org:8443/o/r.git"), "https://git.example.org:8443/o/r.git")
        self.assertEqual(redact_url("http://u:p@[::1]:3000/r"), "http://[::1]:3000/r")

    def test_password_containing_at(self):
        self.assertEqual(redact_url("https://u:p@ss@github.com/o/r"), "https://github.com/o/r")

    def test_no_userinfo_unchanged(self):
        for u in ("https://github.com/Open-Coding-Society/pages.git", "https://github.com/o/r?ref=main#x",
                  "/home/me/pages", "../pages", "", None):
            with self.subTest(u=u):
                self.assertEqual(redact_url(u), u)
                self.assertFalse(has_credentials(u))

    def test_ssh_urls(self):
        self.assertEqual(redact_url("git@github.com:Open-Coding-Society/pages.git"),
                         "github.com:Open-Coding-Society/pages.git")
        self.assertEqual(redact_url("ssh://git@github.com:22/o/r.git"), "ssh://github.com:22/o/r.git")
        self.assertTrue(has_credentials("https://%s@github.com/o/r" % TOKEN))

    def test_secret_query_params(self):
        r = redact_url("https://example.com/r.git?access_token=%s&ref=main" % TOKEN)
        self.assertNotIn(TOKEN, r)
        self.assertIn("ref=main", r)
        self.assertIn("access_token=REDACTED", r)

    def test_redact_text(self):
        msg = ("fatal: unable to access 'https://x-access-token:%s@github.com/o/r.git/': 403\n"
               "remote: see https://github.com/o/r and mail me@example.com" % TOKEN)
        out = redact_text(msg)
        self.assertNotIn(TOKEN, out)
        self.assertIn("'https://github.com/o/r.git/'", out)
        self.assertIn("me@example.com", out)  # plain emails are not URLs


class RedactTextTest(unittest.TestCase):
    """redact_text must agree with redact_url on every URL it finds."""

    def test_at_in_password(self):
        for text in ("clone https://user:p@ss@github.com/o/r.git now",
                     "clone https://user:p@ss@w0rd@github.com/o/r.git",
                     "https://u:a?b#c@github.com/o/r"):
            with self.subTest(text=text):
                out = redact_text(text)
                self.assertNotIn("p@ss", out)
                self.assertNotIn("u:a", out)
                self.assertIn("https://github.com/o/r", out)
        self.assertEqual(redact_url("https://user:p@ss@w0rd@github.com/o/r.git"), "https://github.com/o/r.git")

    def test_query_param_tokens(self):
        for name in ("token", "access_token", "private_token", "key", "api_key", "password", "secret", "sig",
                     "X-Amz-Signature", "client_secret"):
            with self.subTest(name=name):
                url = "https://gitlab.example.com/o/r.git?%s=%s&ref=main" % (name, TOKEN)
                for out in (redact_url(url), redact_text("fatal: could not read %s: 401" % url)):
                    self.assertNotIn(TOKEN, out)
                    self.assertIn("ref=main", out)
                    self.assertIn("=REDACTED", out)
        # ordinary parameters are left alone
        self.assertEqual(redact_url("https://h/x?ref=main&page=2"), "https://h/x?ref=main&page=2")
        self.assertEqual(redact_url("https://h/x?monkey=1"), "https://h/x?monkey=1")

    def test_multiple_urls_one_line(self):
        line = ("from https://a:%s@github.com/o/a.git and https://github.com/o/b.git,"
                "https://b:%s@gitlab.com/o/c.git;git@github.com:o/d.git" % (TOKEN, TOKEN))
        out = redact_text(line)
        self.assertNotIn(TOKEN, out)
        self.assertEqual(out, "from https://github.com/o/a.git and https://github.com/o/b.git,"
                              "https://gitlab.com/o/c.git;github.com:o/d.git")

    def test_trailing_punctuation(self):
        cases = {
            "see https://u:%s@h.com/r." % TOKEN: "see https://h.com/r.",
            "(https://u:%s@h.com/r)" % TOKEN: "(https://h.com/r)",
            "'https://u:%s@h.com/r/': 403" % TOKEN: "'https://h.com/r/': 403",
            '"https://u:%s@h.com/r?token=%s",' % (TOKEN, TOKEN): '"https://h.com/r?token=REDACTED",',
            "<https://u:%s@h.com/r>!" % TOKEN: "<https://h.com/r>!",
            "`git clone https://%s@h.com/r.git`;" % TOKEN: "`git clone https://h.com/r.git`;",
        }
        for text, expect in cases.items():
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), expect)

    def test_text_without_urls_unchanged(self):
        for text in ("mail me@example.com: hi", "a@b:12 is a time", "no urls here", "path/to/file.md:12",
                     "https://github.com/o/r and /local/path"):
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), text)

    def test_one_parser(self):
        urls = ["https://u:p@ss@h.com:8443/x?token=%s#frag" % TOKEN, "ssh://git@h.com/x.git",
                "git@github.com:o/r.git", "https://h.com/x?api_key=%s" % TOKEN, "http://[::1]:3000/r"]
        for u in urls:
            with self.subTest(u=u):
                self.assertEqual(redact_text("x %s y" % u), "x %s y" % redact_url(u))


class RedactionInUseTest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.secret_url = "https://x-access-token:%s@github.com/o/pages.git" % TOKEN

    def tearDown(self):
        self.sb.cleanup()

    def test_upstream_url_redacted_in_scan(self):
        git(self.sb.up, "remote", "add", "origin", self.secret_url)
        self.assertEqual(gitops.Upstream(self.sb.up).url, "https://github.com/o/pages.git")
        for fmt in ([], ["--json"]):
            rc, out, err = self.sb.cli("scan", "--no-deps", *fmt)
            self.assertEqual(rc, 0, err)
            self.assertNotIn(TOKEN, out + err)
        rc, out, _ = self.sb.cli("scan", "--json", "--no-deps")
        self.assertEqual(json.loads(out)["upstream"]["url"], "https://github.com/o/pages.git")

    def test_commit_message_and_pr_body_redacted(self):
        ctx = self.sb.context()
        ctx.cfg.upstream_url = self.secret_url
        calls = []
        plan = transfer.do_import(ctx, "games", ["navigation/games/pong.md"], pr=True, date="20260101",
                                  pr_runner=lambda cmd: calls.append(cmd) or 0)
        msg = git(self.sb.kit, "log", "-1", "--format=%B")
        self.assertIn("Upstream: https://github.com/o/pages.git @ ", msg)
        self.assertNotIn(TOKEN, msg)
        self.assertNotIn(TOKEN, json.dumps(calls))
        self.assertTrue(plan.commit)

    def test_clone_failure_and_cache_dir_redacted(self):
        cache = tempfile.mkdtemp(prefix="ocs-kit-sync-cache-")
        try:
            logs = []
            bad = "https://x-access-token:%s@127.0.0.1:9/o/r.git" % TOKEN
            with self.assertRaises(gitops.GitError) as cm:
                gitops.open_upstream(bad, cache=cache, log=logs.append)
            self.assertNotIn(TOKEN, str(cm.exception))
            self.assertNotIn(TOKEN, " ".join(logs))
            self.assertNotIn(TOKEN, " ".join(os.listdir(cache)))
            self.assertNotIn(TOKEN, gitops._slug(bad))
        finally:
            shutil.rmtree(cache, ignore_errors=True)

    def test_cached_clone_stores_no_credentials(self):
        # clone a local repo through a file:// URL, then check what the cached clone stores as origin
        cache = tempfile.mkdtemp(prefix="ocs-kit-sync-cache-")
        try:
            spec = "file://" + self.sb.up
            up = gitops.open_upstream(spec, cache=cache, log=lambda m: None)
            self.assertEqual(up.url, spec)
            # simulate a clone made from a credentialed URL by an older version, then a re-fetch
            git(up.root, "remote", "set-url", "origin", "https://u:%s@example.invalid/r.git" % TOKEN)
            self.assertNotIn(TOKEN, gitops.Upstream(up.root).url)
            # fetching uses the given spec, not the stored remote
            up2 = gitops.open_upstream(spec, cache=cache, log=lambda m: None)
            self.assertEqual(up2.sha, git(self.sb.up, "rev-parse", "HEAD").strip())
        finally:
            shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
