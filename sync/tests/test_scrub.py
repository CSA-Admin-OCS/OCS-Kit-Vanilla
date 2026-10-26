import json
import os
import shutil
import tempfile
import unittest

from helpers import DENY, FAKE_GOOGLE_KEY, png_with_text, write

from kitsync import media, scrub


def rules(findings):
    return sorted(set(f.rule for f in findings))


class DenylistTest(unittest.TestCase):
    def setUp(self):
        self.gate = scrub.ScrubGate({})

    def test_each_term(self):
        cases = {
            "deny:dnhs": "Welcome to %s CSA" % DENY["dnhs"],
            "deny:del-norte": "%s High School" % DENY["delnorte"],
            "deny:poway": "%susd.net" % DENY["poway"].lower(),
            "deny:nighthawk": "%s coding society" % DENY["nighthawk"],
            "deny:jm1021": "github.com/%s" % DENY["jm"],
            "deny:jmort": "%s1021@gmail" % DENY["jmort"],
            "deny:mortensen": "Mr. %s" % DENY["mort"],
            "deny:ocs-domain": "https://%s/x" % DENY["domain"],
        }
        for rule, text in cases.items():
            with self.subTest(rule=rule):
                fs = self.gate.check_text("a/page.md", "line one\n" + text + "\n")
                self.assertIn(rule, rules(fs))
                f = [x for x in fs if x.rule == rule][0]
                self.assertEqual(f.line, 2)
                self.assertIn("a/page.md:2", f.format())

    def test_bathroom_account(self):
        fs = self.gate.check_text("x.properties", "mail=%sbathroom@gmail.com\n" % DENY["dnhs"].lower())
        self.assertIn("deny:dnhs", rules(fs))

    def test_clean_text(self):
        self.assertEqual(self.gate.check_text("a.md", "Welcome to Your School. randomdnhsword? no: xdnhs\n"), [])

    def test_extra_denylist_from_config(self):
        g = scrub.ScrubGate({"denylist": [{"id": "deny:school", "regex": "springfield high"}]})
        self.assertIn("deny:school", rules(g.check_text("a.md", "Springfield High rules")))

    def test_mask(self):
        text = "x/%s-games/a.md | deny:%s=2 | https://%s/p" % (DENY["nighthawk"].lower(), DENY["dnhs"].lower(), DENY["domain"])
        out, n = self.gate.mask(text)
        self.assertEqual(n, 3)
        self.assertEqual(self.gate.check_text("r.md", out), [])
        self.assertIn("x/[masked]-games/a.md", out)

    def test_binary_metadata(self):
        data = png_with_text("brand=%s High" % DENY["delnorte"])
        fs, _ = self.gate.check_bytes("images/a.png", data)
        self.assertEqual(rules(fs), ["deny:del-norte"])
        self.assertIn("metadata", fs[0].message)
        # stripping the metadata fixes it and keeps a valid PNG
        clean = media.strip_metadata("images/a.png", data)
        self.assertTrue(clean.startswith(b"\x89PNG"))
        self.assertIn(b"IDAT", clean)
        self.assertEqual(self.gate.check_bytes("images/a.png", clean)[0], [])

    def test_short_random_binary_runs_ignored(self):
        data = b"\x00\x01DnHs\x8d\x00" * 10
        self.assertEqual(self.gate.check_bytes("a.png", data)[0], [])


class SecretTest(unittest.TestCase):
    def setUp(self):
        self.gate = scrub.ScrubGate({})

    def test_patterns(self):
        cases = {
            "secret:google-api-key": "const k = '%s';" % FAKE_GOOGLE_KEY,
            "secret:slack-token": "token = " + "xo" + "xp-1234567890-abcdefghij",
            "secret:groq-key": "GROQ=" + "gs" + "k_" + "A" * 30,
            "secret:github-token": "gh" + "p_" + "a" * 36,
            "secret:aws-access-key": "id=" + "AK" + "IA" + "ABCDEFGHIJKLMNOP",
            "secret:openai-style-key": "key: " + "s" + "k-" + "abcdefghijklmnopqrstuvwx",
            "secret:private-key": "-----BEGIN RSA " + "PRIVATE KEY-----\nabc\n",
            "secret:password-literal": "password = \"" + "hunter2xyz" + "\"",
            "secret:api-key-literal": "apiKey: '" + "0123456789abcdef0123" + "'",
        }
        for rule, text in cases.items():
            with self.subTest(rule=rule):
                fs = self.gate.check_text("src/app.js", text + "\n")
                self.assertIn(rule, rules(fs))
                # the excerpt never contains the whole secret
                for f in fs:
                    self.assertLess(len(f.excerpt), 40)

    def test_github_fine_grained_pat(self):
        self.assertIn("secret:github-token", rules(self.gate.check_text("a", "github_" + "pat_" + "A" * 30)))

    def test_placeholders_are_not_passwords(self):
        for text in ('password = "password"', 'password: "{{ site.pw }}"', 'password = "${PASSWORD}"',
                     'password = "your_password_here"', 'password = "changeme"', 'password = "****"'):
            with self.subTest(text=text):
                self.assertEqual(self.gate.check_text("a.py", text), [])

    def test_env_file_passwords(self):
        fs = self.gate.check_text("deploy/.env.prod", "ADMIN_PASSWORD=realSecret99\n")
        self.assertIn("secret:password-literal", rules(fs))
        self.assertEqual(self.gate.check_text(".env.example", "ADMIN_PASSWORD=changeme\n"), [])
        self.assertIn("secret:env-file", rules(self.gate.check_text(".env", "A=1\n")))

    def test_css_class_is_not_openai_key(self):
        self.assertEqual(self.gate.check_text("a.css", ".task-sk-abcdefghijklmnopqrstuvwxyz {}"), [])


class PiiTest(unittest.TestCase):
    def setUp(self):
        self.gate = scrub.ScrubGate({"pii_min_records": 5})

    def test_many_emails_in_json(self):
        data = json.dumps([{"email": "student%d@school.org" % i} for i in range(6)])
        self.assertIn("pii:emails", rules(self.gate.check_text("_data/users.json", data)))

    def test_example_emails_ignored(self):
        data = json.dumps([{"email": "user%d@example.com" % i} for i in range(20)])
        self.assertNotIn("pii:emails", rules(self.gate.check_text("_data/users.json", data)))

    def test_github_handles_in_json(self):
        data = json.dumps({"students": [{"github": "kid%d" % i, "score": i} for i in range(6)]})
        self.assertIn("pii:handles", rules(self.gate.check_text("_data/class.json", data)))

    def test_few_handles_ok(self):
        data = json.dumps([{"github": "kid%d" % i} for i in range(3)])
        self.assertEqual(self.gate.check_text("_data/class.json", data), [])

    def test_roster_csv(self):
        text = "student,github,period\n" + "".join("a%d,b%d,3\n" % (i, i) for i in range(6))
        self.assertIn("pii:roster", rules(self.gate.check_text("data/x.csv", text)))

    def test_pii_paths(self):
        for path, rule in (("instance/backup/data.json", "pii:backup"), ("lists/period3_roster.csv", "pii:roster"),
                           ("instance/volumes/user_management.db", "pii:database"), ("cookies.txt", "pii:cookies")):
            with self.subTest(path=path):
                self.assertIn(rule, rules(self.gate.check_bytes(path, b"{}")[0]))

    def test_emails_in_markdown_not_pii(self):
        text = " ".join("p%d@school.org" % i for i in range(30))
        self.assertNotIn("pii:emails", rules(self.gate.check_text("post.md", text)))


class SizeAndAllowlistTest(unittest.TestCase):
    def test_large_file(self):
        g = scrub.ScrubGate({"max_file_kb": 1})
        fs, _ = g.check_bytes("big.bin", b"\x00" * 2048)
        self.assertEqual(rules(fs), ["size:large"])

    def test_allowlist_config(self):
        g = scrub.ScrubGate({"allow": [{"path": "LICENSE", "rule": "deny:*", "reason": "credit"}]})
        fs, allowed = g.check_bytes("LICENSE", ("Copyright %s" % DENY["mort"]).encode())
        self.assertEqual(fs, [])
        self.assertEqual(len(allowed), 1)
        # the allowlist is per path and per rule
        self.assertTrue(g.check_text("README.md", "Copyright %s" % DENY["mort"]))
        self.assertTrue(g.check_text("LICENSE", "key " + FAKE_GOOGLE_KEY))

    def test_allowlist_match_text(self):
        g = scrub.ScrubGate({"allow": [{"path": "**", "rule": "deny:ocs-domain", "match": "github.io"}]})
        self.assertEqual(g.check_text("a.md", "see https://%s.github.io" % DENY["domain"]), [])
        self.assertTrue(g.check_text("a.md", "see https://%s/" % DENY["domain"]))

    def test_allowlist_file_and_inline(self):
        d = tempfile.mkdtemp()
        try:
            write(d, "kit.scrub-allow", "# reviewed\ndocs/*.md deny:poway  # district name in a citation\n")
            g = scrub.ScrubGate({}, base_dir=d)
            self.assertEqual(g.check_text("docs/a.md", DENY["poway"]), [])
            self.assertTrue(g.check_text("other/a.md", DENY["poway"]))
            self.assertEqual(g.check_text("x.md", "%s  <!-- kit-scrub: allow -->" % DENY["poway"]), [])
        finally:
            shutil.rmtree(d)

    def test_tree_report(self):
        d = tempfile.mkdtemp()
        try:
            write(d, "ok.md", "fine\n")
            write(d, "bad/a.md", "x\n" + DENY["nighthawk"] + "\n")
            write(d, "node_modules/lib.js", DENY["nighthawk"])  # never scanned
            rep = scrub.ScrubGate({}).check_tree(d)
            self.assertFalse(rep.ok)
            self.assertEqual(rep.files_checked, 2)
            self.assertIn("bad/a.md:2: [deny:nighthawk]", rep.format())
            self.assertIn("FAIL", rep.format())
            self.assertEqual(rep.to_dict()["by_category"], {"deny": 1})
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
