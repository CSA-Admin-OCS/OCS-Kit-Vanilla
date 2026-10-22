"""Minimal internal link checker for a built Jekyll site (fallback when the Pages repo has none)."""

import os
import re
from urllib.parse import unquote

_LINK = re.compile(r"""(?i)<(?:a|link|script|img|source|iframe)\b[^>]*?\b(?:href|src)\s*=\s*["']([^"'#?]*)[^"']*["']""")


def _exists(site, path):
    p = os.path.join(site, path.lstrip("/"))
    if path.endswith("/"):
        return os.path.isfile(os.path.join(p, "index.html"))
    return os.path.isfile(p) or os.path.isfile(p + ".html") or os.path.isfile(os.path.join(p, "index.html"))


def check_site(site, baseurl=""):
    """Return {broken internal URL: [pages linking to it]} for the HTML files under ``site``."""
    baseurl = "/" + baseurl.strip("/") if baseurl.strip("/") else ""
    broken = {}
    for d, _, files in os.walk(site):
        for fn in files:
            if not fn.endswith(".html"):
                continue
            page = os.path.join(d, fn)
            rel_dir = "/" + os.path.relpath(d, site).replace(os.sep, "/").strip(".")
            with open(page, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in _LINK.finditer(text):
                url = unquote(m.group(1).strip())
                if not url or re.match(r"^[a-z][a-z0-9+.\-]*:", url, re.I) or url.startswith("//"):
                    continue
                if "{{" in url or "${" in url:
                    continue
                if url.startswith("/"):
                    if baseurl and not (url == baseurl or url.startswith(baseurl + "/")):
                        target = url  # absolute link that ignores baseurl: broken when deployed under baseurl
                        ok = False
                    else:
                        target = url[len(baseurl):] or "/"
                        ok = _exists(site, target)
                else:
                    target = os.path.normpath(os.path.join(rel_dir, url)).replace(os.sep, "/")
                    ok = _exists(site, target + ("/" if url.endswith("/") else ""))
                if not ok:
                    broken.setdefault(url, []).append(os.path.relpath(page, site))
    return broken
