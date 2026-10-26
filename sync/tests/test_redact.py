import json
import os
import random
import shutil
import tempfile
import time
import unittest

from helpers import Sandbox, git

from kitsync import gitops, transfer
from kitsync.urls import MAX_TOKEN, has_credentials, redact_text, redact_url

TOKEN = "ghs_" + "S3cr3tT0k3nValue1234567890"
SECRET_NAMES = ["token", "access_token", "private_token", "accessToken", "githubToken", "X-Api-Key", "api_key",
                "key", "authorization", "Authorization", "client_secret_v2", "sessionid", "PRIVATE-TOKEN",
                "bearer", "cookie", "sig", "X-Amz-Signature", "password", "passwd", "pass", "db_pass", "pwd",
                "credentials", "tok%65n", "api%5Fkey"]


class RedactUrlTest(unittest.TestCase):
    def test_userinfo_forms(self):
        cases = {
            "https://alice:pa55word@github.com/o/r.git": "https://***@github.com/o/r.git",
            "https://%s@github.com/o/r.git" % TOKEN: "https://***@github.com/o/r.git",
            "https://x-access-token:%s@github.com/o/r" % TOKEN: "https://***@github.com/o/r",
            "https://u:p@git.example.org:8443/o/r.git": "https://***@git.example.org:8443/o/r.git",
            "http://u:p@[::1]:3000/r": "http://***@[::1]:3000/r",
            "https://u:p@ss@github.com/o/r": "https://***@github.com/o/r",
            "https://u:p/a?b#c@github.com/o/r": "https://***@github.com/o/r",
            "ssh://git@github.com:22/o/r.git": "ssh://***@github.com:22/o/r.git",
            "git+ssh://u:p@h.com/r": "git+ssh://***@h.com/r",
            "1x://u:p@h/r": "1x://***@h/r",
            "git@github.com:Open-Coding-Society/pages.git": "github.com:Open-Coding-Society/pages.git",
        }
        for url, expect in cases.items():
            with self.subTest(url=url):
                self.assertEqual(redact_url(url), expect)
                self.assertTrue(has_credentials(url))

    def test_no_credentials_unchanged(self):
        for u in ("https://github.com/Open-Coding-Society/pages.git", "https://github.com/o/r?ref=main#x",
                  "/home/me/pages", "../pages", "", None, "_posts/task-management-and-planning-notes.md"):
            with self.subTest(u=u):
                self.assertEqual(redact_url(u), u)
                self.assertFalse(has_credentials(u))

    def test_secret_query_params(self):
        for name in SECRET_NAMES:
            with self.subTest(name=name):
                url = "https://gitlab.example.com/o/r.git?%s=%s&page=2" % (name, TOKEN[4:])
                for out in (redact_url(url), redact_text("fatal: could not read %s: 401" % url)):
                    self.assertNotIn(TOKEN[4:], out)
                    self.assertIn("page=2", out)
                    self.assertIn("=REDACTED", out)
        self.assertEqual(redact_url("https://h/x?ref=main&page=2"), "https://h/x?ref=main&page=2")

    def test_param_value_with_question_mark_and_at(self):
        out = redact_text("https://h/x?token=ab?c@SEC1&x=1 db_pass=a=b?SEC2;y=2 url=https://h/y?key=SEC3")
        for m in ("SEC1", "SEC2", "SEC3"):
            self.assertNotIn(m, out)
        self.assertIn("y=2", out)

    def test_secret_shaped_values_anywhere(self):
        for v in ("AIza" + "B" * 35, "ghp_" + "a" * 36, "github_pat_" + "a" * 30, "gsk_" + "a" * 30,
                  "xoxb-" + "1" * 20, "AKIA" + "ABCDEFGHIJKLMNOP", "sk-" + "a" * 30):
            with self.subTest(v=v[:6]):
                for text in ("https://h/x?ref=%s" % v, "https://h/%s/r" % v, "value %s here" % v, "x=%s" % v):
                    self.assertNotIn(v, redact_text(text))


class RedactTextTest(unittest.TestCase):
    def test_git_error_message(self):
        msg = ("fatal: unable to access 'https://x-access-token:%s@github.com/o/r.git/': 403\n"
               "remote: see https://github.com/o/r and mail me@example.com" % TOKEN)
        out = redact_text(msg)
        self.assertNotIn(TOKEN, out)
        self.assertIn("'https://***@github.com/o/r.git/'", out)
        self.assertIn("me@example.com", out)  # plain emails are not credentials

    def test_multiple_urls(self):
        out = redact_text("from https://a:%s@github.com/o/a.git and https://github.com/o/b.git "
                          "https://b:%s@gitlab.com/o/c.git;git@github.com:o/d.git" % (TOKEN, TOKEN))
        self.assertNotIn(TOKEN, out)
        self.assertIn("from https://***@github.com/o/a.git and https://github.com/o/b.git ", out)
        glued = redact_text("https://a:S1@h/x,https://b:S2@g/y")
        self.assertNotIn("S1", glued)
        self.assertNotIn("S2", glued)

    def test_surrounding_punctuation(self):
        cases = {
            "see https://u:%s@h.com/r." % TOKEN: "see https://***@h.com/r.",
            "(https://u:%s@h.com/r)" % TOKEN: "(https://***@h.com/r)",
            "'https://u:%s@h.com/r/': 403" % TOKEN: "'https://***@h.com/r/': 403",
            '"https://u:%s@h.com/r?token=%s",' % (TOKEN, TOKEN): '"https://***@h.com/r?token=REDACTED',
            "<https://u:%s@h.com/r>!" % TOKEN: "<https://***@h.com/r>!",
            "`git clone https://%s@h.com/r.git`;" % TOKEN: "`git clone https://***@h.com/r.git`;",
            "(git@github.com:o/r.git)": "(github.com:o/r.git)",
        }
        for text, expect in cases.items():
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), expect)

    def test_text_without_credentials_unchanged(self):
        for text in ("mail me@example.com hi", "no urls here", "path/to/file.md:12",
                     "https://github.com/o/r and /local/path", "scrub gate: PASS -- 4 file(s) checked",
                     "_posts/task-management-and-planning-notes.md: [deny:x]"):
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), text)

    def test_long_tokens_redacted_before_truncation(self):
        url = "https://u:" + "p" * 20000 + "SECRET_LONG@h.com/" + "a" * 20000 + "?token=SECRET_Q"
        out = redact_text("x " + url)
        self.assertNotIn("SECRET_", out)
        self.assertTrue(out.endswith("...[truncated]"))
        self.assertLessEqual(len(out), MAX_TOKEN + 40)

    def test_linear_time_on_adversarial_input(self):
        for text in ("a://" * 50000, "x" * 200000, "https://" + "a" * 200000, "a@" * 100000, "@a:" * 70000,
                     ("https://u:p@h/" + "?" * 10) * 10000, "a:" * 100000 + "//", "." * 200000,
                     "=" * 200000, "a=" * 100000, "?token=" * 30000, "sk-" * 70000, "AIza" * 50000):
            with self.subTest(text=text[:12]):
                t = time.time()
                redact_text(text)
                redact_url(text)
                self.assertLess(time.time() - t, 0.5)


def _rand(rng, alphabet, lo, hi):
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(lo, hi)))


class RedactPropertyTest(unittest.TestCase):
    """Thousands of generated URLs/lines, each hiding a unique SECRET_xxxx marker in a credential."""

    SCHEMES = ["https", "http", "ssh", "git+ssh", "1x", "a.b", "git", "s3", "x-y+z", "HTTPS", "svn+ssh"]
    # credential characters: everything except whitespace (tokens are whitespace-separated)
    CRED = list("abcXYZ0129-_.~!$'()*+,;=:/@?#[]%\"\\`<>{}|^&") + ["%40", "%3A", "%2F", "é", "ü", "中", "ß", "😀"]
    # param value characters: no raw '&', '#' or ';' (those end a value by definition)
    VALUE = list("abcXYZ0129-_.~!$'()*+,=:/@?[]%") + ["%26", "é", "中"]
    HOST = ["github.com", "gitlab.example.org", "h", "127.0.0.1:9", "[::1]:3000", "a-b.c-d.example"]
    PATH = list("abc019-_./~@:+=!$,")
    PRE = ["", "(", "'", '"', "<", "[", "`", "see ", "url=", "x,", "{"]
    POST = ["", ".", ",", ")", "'", '"', ">", "]", ";", ":", "!", "`", "}", "...", " and more"]

    def _case(self, rng, marker):
        kind = rng.randrange(5)
        scheme = rng.choice(self.SCHEMES)
        host = rng.choice(self.HOST)
        path = "/" + _rand(rng, self.PATH, 0, 30)
        if rng.random() < 0.05:
            path += "a" * rng.randint(9000, 20000)  # very long token
        if kind == 0:  # marker in the password
            cred = _rand(rng, self.CRED, 0, 12) + marker + _rand(rng, self.CRED, 0, 12)
            if rng.random() < 0.05:
                cred = "p" * rng.randint(9000, 12000) + cred
            core = "%s://%s:%s@%s%s" % (scheme, _rand(rng, self.CRED, 1, 8), cred, host, path)
        elif kind == 1:  # marker in the user
            core = "%s://%s@%s%s" % (scheme, _rand(rng, self.CRED, 0, 8) + marker + _rand(rng, self.CRED, 0, 8),
                                    host, path)
        elif kind == 2:  # marker in a secret-ish query or fragment param
            name = rng.choice(SECRET_NAMES)
            val = _rand(rng, self.VALUE, 0, 10) + marker + _rand(rng, self.VALUE, 0, 10)
            sep = rng.choice(["?", "&", "#", ";"])
            core = "%s://%s%s?a=1%s%s=%s&b=2" % (scheme, host, path, sep, name, val)
        elif kind == 3:  # scp-like user@host:path
            core = "%s%s@%s:o/r.git" % (_rand(rng, self.VALUE, 0, 6), marker, rng.choice(["github.com", "h", "a.b-c"]))
        else:  # two URLs glued together, the secret one second
            core = "%s://h/x,%s://u:%s@%s%s" % (scheme, rng.choice(self.SCHEMES), marker, host, path)
        return rng.choice(self.PRE) + core + rng.choice(self.POST)

    def test_no_marker_survives(self):
        rng = random.Random(20261026)
        t0 = time.time()
        for i in range(4000):
            marker = "SECRET_%04d" % i
            line = self._case(rng, marker)
            for out in (redact_text(line), redact_text("log: " + line + " end"), redact_url(line)):
                if marker in out:
                    self.fail("marker leaked\n in:  %r\n out: %r" % (line[:400], out[:400]))
        self.assertLess(time.time() - t0, 10.0)

    def test_many_markers_in_one_text(self):
        rng = random.Random(7)
        lines = [self._case(rng, "SECRET_M%04d" % i) for i in range(500)]
        out = redact_text("\n".join(lines))
        self.assertNotIn("SECRET_M", out)


class RedactionInUseTest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.secret_url = "https://x-access-token:%s@github.com/o/pages.git" % TOKEN

    def tearDown(self):
        self.sb.cleanup()

    def test_upstream_url_redacted_in_scan(self):
        git(self.sb.up, "remote", "add", "origin", self.secret_url)
        self.assertEqual(gitops.Upstream(self.sb.up).url, "https://***@github.com/o/pages.git")
        for fmt in ([], ["--json"]):
            rc, out, err = self.sb.cli("scan", "--no-deps", *fmt)
            self.assertEqual(rc, 0, err)
            self.assertNotIn(TOKEN, out + err)
        rc, out, _ = self.sb.cli("scan", "--json", "--no-deps")
        self.assertEqual(json.loads(out)["upstream"]["url"], "https://***@github.com/o/pages.git")

    def test_commit_message_and_pr_body_redacted(self):
        ctx = self.sb.context()
        ctx.cfg.upstream_url = self.secret_url
        calls = []
        plan = transfer.do_import(ctx, "games", ["navigation/games/pong.md"], pr=True, date="20260101",
                                  pr_runner=lambda cmd: calls.append(cmd) or 0)
        msg = git(self.sb.kit, "log", "-1", "--format=%B")
        self.assertIn("Upstream: https://***@github.com/o/pages.git @ ", msg)
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
