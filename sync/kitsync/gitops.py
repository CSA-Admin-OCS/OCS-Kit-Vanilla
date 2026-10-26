"""Small git helpers and the upstream checkout (URL -> cached shallow clone, or a local path)."""

import os
import re
import subprocess


class GitError(Exception):
    pass


def git(repo, *args, **kw):
    check = kw.pop("check", True)
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    p = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if check and p.returncode != 0:
        raise GitError("git %s failed in %s: %s" % (" ".join(args), repo, p.stderr.decode("utf-8", "replace").strip()))
    return p.stdout.decode("utf-8", "replace")


def is_repo(path):
    try:
        return git(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except (GitError, OSError):
        return False


def ls_files(repo):
    out = git(repo, "ls-files", "-z")
    return [p for p in out.split("\0") if p]


def head(repo):
    try:
        return git(repo, "rev-parse", "HEAD").strip()
    except GitError:
        return ""


def current_branch(repo):
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def branch_exists(repo, name):
    return git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + name, check=False).strip() != ""


def unique_branch(repo, name):
    cand, i = name, 2
    while branch_exists(repo, cand):
        cand = "%s-%d" % (name, i)
        i += 1
    return cand


def dirty_paths(repo):
    out = git(repo, "status", "--porcelain", "-z")
    res = []
    for item in out.split("\0"):
        if len(item) > 3:
            res.append(item[3:])
    return res


def default_cache():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ocs-kit-sync")


def _slug(url):
    s = re.sub(r"^[a-z]+://", "", url.strip().rstrip("/"))
    s = re.sub(r"\.git$", "", s)
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", s)


class Upstream(object):
    """Read-only view of the upstream repo's tracked files at its checked-out HEAD."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        if is_repo(self.root):
            self.files = set(ls_files(self.root))
            self.sha = head(self.root)
            self.url = git(self.root, "remote", "get-url", "origin", check=False).strip()
        else:
            self.url = ""
            from .scrub import list_repo_files
            self.files = set(list_repo_files(self.root))
            self.sha = ""
        # only real files (submodules / deleted-in-worktree entries are skipped)
        self.files = set(p for p in self.files if os.path.isfile(os.path.join(self.root, p))
                         and not os.path.islink(os.path.join(self.root, p)))

    def read_bytes(self, path):
        try:
            with open(os.path.join(self.root, path), "rb") as f:
                return f.read()
        except (IOError, OSError):
            return None

    def read_text(self, path):
        b = self.read_bytes(path)
        if b is None or b"\x00" in b[:8192]:
            return None
        return b.decode("utf-8", "replace")


def open_upstream(spec, branch="main", cache=None, fetch=True, log=print):
    """``spec`` is a local directory or a git URL. URLs are shallow-cloned (read-only) into the cache."""
    if spec and os.path.isdir(spec):
        return Upstream(spec)
    if not spec:
        raise GitError("no upstream given (set upstream.url in kit.sync.json or pass --upstream)")
    cache = cache or default_cache()
    dest = os.path.join(cache, _slug(spec))
    if os.path.isdir(os.path.join(dest, ".git")):
        if fetch:
            log("fetching %s (%s) into %s" % (spec, branch, dest))
            git(dest, "fetch", "--depth", "1", "origin", branch)
            git(dest, "reset", "--hard", "-q", "FETCH_HEAD")
    else:
        os.makedirs(cache, exist_ok=True)
        log("cloning %s (%s, shallow) into %s" % (spec, branch, dest))
        p = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", branch, spec, dest],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            raise GitError("clone failed: %s" % p.stderr.decode("utf-8", "replace"))
    return Upstream(dest)
