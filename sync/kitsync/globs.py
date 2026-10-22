"""gitignore-flavoured path globs used by kit.sync.json (exclude, protect, allowlist...).

Rules (paths are repo-relative, '/'-separated):
  * ``*`` matches within one path segment, ``?`` one character, ``**`` any number of segments.
  * A pattern without '/' (other than a trailing one) matches the basename anywhere: ``*.pyc``, ``.env``.
  * A leading '/' anchors the pattern at the repo root: ``/git`` matches only the root file ``git``.
  * A pattern that matches a directory also matches everything below it: ``linuxcyberA`` and
    ``linuxcyberA/`` both cover ``linuxcyberA/x/y.html``.
"""

import re
from functools import lru_cache


@lru_cache(maxsize=4096)
def glob_to_regex(pattern):
    pat = pattern.strip().replace("\\", "/")
    anchored = pat.startswith("/")
    pat = pat.lstrip("/")
    while pat.endswith("/"):
        pat = pat[:-1]
    if not pat:
        return re.compile(r"^$")
    prefix = "" if (anchored or "/" in pat) else "(?:.*/)?"
    out = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[":
            j = pat.find("]", i + 1)
            if j > i:
                body = pat[i + 1:j].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = j + 1
            else:
                out.append(re.escape("["))
                i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + prefix + "".join(out) + "(?:/.*)?$")


def match(pattern, path):
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return bool(glob_to_regex(pattern).match(path.lstrip("/")))


def is_glob(pattern):
    return any(c in pattern for c in "*?[")


class PathSet(object):
    """An ordered set of glob patterns; ``first(path)`` returns the first pattern that matches."""

    def __init__(self, patterns=None):
        self.patterns = [p for p in (patterns or []) if p and not p.strip().startswith("#")]

    def first(self, path):
        for p in self.patterns:
            if match(p, path):
                return p
        return None

    def __contains__(self, path):
        return self.first(path) is not None

    def __bool__(self):
        return bool(self.patterns)

    __nonzero__ = __bool__


def expand(patterns, universe):
    """Return the sorted paths of ``universe`` matched by any of ``patterns``."""
    ps = PathSet(patterns)
    return sorted(p for p in universe if p in ps)
