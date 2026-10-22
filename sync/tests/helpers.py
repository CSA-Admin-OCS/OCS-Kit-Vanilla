"""Test helpers: fake upstream and kit repos in temp dirs."""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

SYNC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SYNC_DIR not in sys.path:
    sys.path.insert(0, SYNC_DIR)

OCS_KIT_DIR = os.path.dirname(SYNC_DIR)
CANONICAL = os.path.join(OCS_KIT_DIR, "kits", "catalog.json")
CLI = os.path.join(SYNC_DIR, "ocs_kit_sync.py")

# Assembled at runtime so this file never contains the literal secrets/terms it tests for.
FAKE_GOOGLE_KEY = "AI" + "za" + "SyD" + "x" * 32
DENY = {"dnhs": "DN" + "HS", "delnorte": "Del " + "Norte", "poway": "Po" + "way", "nighthawk": "Night" + "hawk",
        "jm": "jm" + "1021", "jmort": "jm" + "ort", "mort": "Morten" + "sen", "domain": "pages.opencoding" + "society.com"}


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", "-C", repo] + list(args), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env).stdout.decode()


def write(root, rel, content):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(p, mode) as f:
        f.write(content)
    return p


def read(root, rel, binary=False):
    with open(os.path.join(root, rel), "rb" if binary else "r") as f:
        return f.read()


def init_repo(root, files, branch="main"):
    os.makedirs(root, exist_ok=True)
    git(root, "init", "-q", "-b", branch)
    for rel, content in files.items():
        write(root, rel, content)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def png_with_text(text):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"Comment\x00" + text.encode())
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def fixture_catalog(pages=None):
    """Minimal kit-repo catalog. Feature ids here are local to the fixture."""
    pages = pages or {}
    feats = {}
    for fid, cat in (("core", "core"), ("games", "general"), ("extras", "general"), ("course", "education")):
        feats[fid] = {"name": fid.title(), "category": cat, "labels": [], "description": "fixture " + fid,
                      "setup": {"summary": "s", "settings": [], "steps": [], "verify": "v"},
                      "pages": list(pages.get(fid, []))}
    return {
        "version": 1,
        "categories": {"core": {}, "general": {}, "education": {}},
        "labels": {"student-safe": "x", "demo": "y"},
        "features": feats,
        "kits": {"vanilla": {"features": ["core"]}, "education": {"features": ["core", "course"]},
                 "business": {"features": ["core", "games"]}},
    }


SYNC_CONFIG = {
    "name": "pages",
    "upstream": {"url": "", "branch": "main"},
    "catalog": {"file": "kits/catalog.json", "key": "pages"},
    "map": [{"upstream": "media/", "kit": "features/media/"}],
    "exclude": ["/navigation/about.md", "/images/about", "/junk", "*.log"],
    "protect": ["_config.yml", "kits/**", "README.md", "scripts/apply_kit.py", "_projects/.makeprojects"],
    "replace": [
        {"id": "js-domain", "regex": True, "files": ["*.js"],
         "pattern": "([\"'`])https?://pages\\.opencodingsociety\\.com/", "replacement": "(window.baseurl || \"\") + \\1/"},
        {"id": "liquid-domain", "regex": True, "files": ["*.md", "*.html"],
         "pattern": "https?://pages\\.opencodingsociety\\.com", "replacement": "{{site.baseurl}}"},
    ],
    "generated": [{"prefix": "assets/js/projects/", "source": "_projects/*/{name}"}],
    "scrub": {"max_file_kb": 64},
    "suggest": {"games": ["game"], "course": ["lesson"]},
    "validators": [["python3", "scripts/apply_kit.py", "--check"]],
    "tests": [],
    "post_import": {"makeprojects": True},
}

APPLY_KIT_STUB = '''import json, os, sys
# stub of the real scripts/apply_kit.py --check: catalog parses and every listed path exists
cat = json.load(open("kits/catalog.json"))
bad = [p for f in cat["features"].values() for p in f.get("pages", []) if not os.path.exists(p)]
if bad:
    print("missing:", bad); sys.exit(1)
print("ok"); sys.exit(0)
'''


def upstream_files():
    return {
        "_config.yml": "title: OCS upstream\n",
        "README.md": "upstream readme\n",
        "index.md": "---\nlayout: base\n---\nhome\n",
        "_layouts/base.html": "<html>{{ content }}</html>\n",
        "_layouts/game.html": "---\nlayout: base\n---\n{% include game-nav.html %}\n<script src=\"{{site.baseurl}}/assets/js/engine/core.js\"></script>\n{{ content }}\n",
        "_includes/game-nav.html": "<nav>games</nav>\n",
        "assets/js/engine/core.js": "import { util } from './util.js';\nexport const x = util;\n",
        "assets/js/engine/util.js": "export const util = 1;\n",
        "navigation/games/pong.md": "---\nlayout: game\ntitle: Pong\n---\n<script type=\"module\">import P from '{{site.baseurl}}/assets/js/pong/pong.js';</script>\n![bg]({{site.baseurl}}/images/pong/bg.png)\n<a href=\"https://" + DENY["domain"] + "/navigation/games\">all games</a>\n",
        "assets/js/pong/pong.js": "import { x } from '../engine/core.js';\nconst home = \"https://" + DENY["domain"] + "/navigation/games\";\nexport default x;\n",
        "images/pong/bg.png": png_with_text("brand=" + DENY["delnorte"] + " High"),
        "navigation/games/breakout.md": "---\nlayout: game\n---\n<img src=\"/images/breakout/missing.png\">\n",
        "navigation/about.md": "about " + DENY["mort"] + "\n",
        "images/about/me.png": png_with_text("me"),
        "navigation/bad/leak.md": "---\nlayout: base\n---\nkey = '" + FAKE_GOOGLE_KEY + "'\n",
        "media/media.md": "---\nlayout: base\n---\nmedia lesson\n",
        "_projects/games/tiny/index.md": "---\nlayout: base\n---\n<script type=\"module\">import T from '@assets/js/projects/tiny/tiny.js';</script>\n",
        "_projects/games/tiny/tiny.js": "export default 1;\n",
        "_projects/.makeprojects": "# registry\ngames/tiny:dev\n",
    }


def kit_files(catalog=None):
    return {
        "_config.yml": "title: My School\nkit:\n  features: []\n",
        "README.md": "kit readme\n",
        "index.md": "---\nlayout: base\n---\nhome\n",
        "_layouts/base.html": "<html>{{ content }}</html>\n",
        "features/media/media.md": "---\nlayout: base\n---\nmedia lesson (old)\n",
        "scripts/apply_kit.py": APPLY_KIT_STUB,
        "_projects/.makeprojects": "# registry\n",
        "kits/catalog.json": json.dumps(catalog or fixture_catalog({"extras": ["features/media"]}), indent=2) + "\n",
    }


class Sandbox(object):
    """A temp dir with an upstream git repo, a kit git repo and a kit.sync.json."""

    def __init__(self, up_files=None, kit=None, cfg=None):
        self.tmp = tempfile.mkdtemp(prefix="ocs-kit-sync-test-")
        self.up = init_repo(os.path.join(self.tmp, "upstream"), up_files or upstream_files())
        self.kit = init_repo(os.path.join(self.tmp, "kit"), kit or kit_files())
        self.cfg_path = os.path.join(self.tmp, "kit.sync.json")
        c = json.loads(json.dumps(cfg or SYNC_CONFIG))
        c["upstream"]["url"] = self.up
        with open(self.cfg_path, "w") as f:
            json.dump(c, f)

    def context(self):
        from kitsync import config, gitops
        from kitsync.core import KitContext
        cfg = config.load(self.cfg_path)
        return KitContext(self.kit, cfg, gitops.Upstream(self.up), log=lambda m: None)

    def cli(self, *args):
        cmd = [sys.executable, CLI] + list(args[:1]) + ["--kit", self.kit, "--config", self.cfg_path] + list(args[1:])
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, p.stdout.decode(), p.stderr.decode()

    def snapshot(self, root=None):
        root = root or self.kit
        out = {}
        for d, dirs, files in os.walk(root):
            if ".git" in dirs:
                dirs.remove(".git")
            for fn in files:
                p = os.path.join(d, fn)
                with open(p, "rb") as f:
                    out[os.path.relpath(p, root)] = f.read()
        return out

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
