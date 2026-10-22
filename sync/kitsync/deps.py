"""Find what a set of upstream files needs (layouts, includes, scripts, styles, images, data) so an
import brings it along or reports it as missing.

References understood:
  front matter ``layout: x``                  -> _layouts/x.html (and that layout's own layout, ...)
  ``{% include x.html %}``                    -> _includes/x.html
  ``{% include_relative x %}``                -> relative to the file
  ``site.data.x``                             -> _data/x.(yml|yaml|json|csv)
  ``<script src>``, ``<link href>``, ``<img src>``, ``<source src>``, markdown images, CSS ``url()``,
  JS ``import ... from '...'`` / ``import('...')``, ``@import`` in SCSS,
  ``{{site.baseurl}}/...`` and ``@assets/...`` (the Pages build alias for ``assets/``)
Only references to static files (by extension) are followed; page permalinks are not.
"""

import json
import posixpath
import re

from . import globs

STATIC_EXT = {
    "js", "mjs", "css", "scss", "sass", "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "avif",
    "mp3", "wav", "ogg", "m4a", "mp4", "webm", "mov", "json", "csv", "txt", "pdf", "ttf", "otf", "woff",
    "woff2", "eot", "glb", "gltf", "obj", "fbx", "wasm", "xml", "yml", "yaml", "tmx", "tsx", "atlas", "html",
}
TEXT_EXT = {"md", "markdown", "html", "htm", "ipynb", "js", "mjs", "css", "scss", "sass", "liquid", "json",
            "yml", "yaml", "xml", "svg", "txt"}

_FM = re.compile(r"\A\s*---\s*\n(.*?)\n---", re.S)
_LAYOUT = re.compile(r"(?m)^\s*layout:\s*[\"']?([A-Za-z0-9_\-./]+)[\"']?\s*$")
_INCLUDE = re.compile(r"\{%-?\s*include\s+([A-Za-z0-9_\-./]+)")
_INCLUDE_REL = re.compile(r"\{%-?\s*include_relative\s+([A-Za-z0-9_\-./]+)")
_DATA = re.compile(r"site\.data\.([A-Za-z0-9_\-]+)")
_ATTR = re.compile(r"""(?i)<(?:script|img|source|audio|video|link|iframe|embed|image)\b[^>]*?\b(?:src|href|data-src|xlink:href)\s*=\s*["']([^"']+)["']""")
_MD_IMG = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
_CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")
_JS_IMPORT = re.compile(r"""(?:\bimport\s+(?:[\w*{}\s,$]+?\s+from\s+)?|\bimport\s*\(\s*|\bexport\s+[\w*{}\s,$]+?\s+from\s+)["'`]([^"'`]+)["'`]""")
_SCSS_IMPORT = re.compile(r"""@(?:import|use|forward)\s+["']([^"']+)["']""")
_BASEURL = re.compile(r"""\{\{-?\s*(?:site\.url\s*\}\}\s*\{\{-?\s*)?site\.baseurl\s*-?\}\}(/[^\s"'`)<>{}\\]+)""")
_GENERIC_ASSET = re.compile(r"""["'`](/?(?:assets|images|media|_includes|_layouts)/[^"'`\s{}]+)["'`]""")
_ALIAS = re.compile(r"""["'`]@assets/([^"'`\s]+)["'`]""")


class Ref(object):
    __slots__ = ("kind", "raw", "line", "candidates")

    def __init__(self, kind, raw, line, candidates):
        self.kind, self.raw, self.line, self.candidates = kind, raw, line, candidates

    def __repr__(self):
        return "Ref(%s %r -> %r)" % (self.kind, self.raw, self.candidates)


def _ext(p):
    base = p.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def is_text_path(p):
    return _ext(p) in TEXT_EXT


def notebook_text(text):
    """Join the cell sources of a notebook so references inside cells are visible."""
    try:
        nb = json.loads(text)
    except ValueError:
        return text
    parts = []
    for c in nb.get("cells", []):
        src = c.get("source", "")
        parts.append("".join(src) if isinstance(src, list) else str(src))
    return "\n".join(parts)


def _norm(url, src_path):
    """Map a URL found in ``src_path`` to a repo path, or None if it is not a local static file."""
    u = url.strip()
    if not u or u.startswith(("#", "mailto:", "data:", "javascript:", "tel:", "blob:")):
        return None
    if re.match(r"^[a-z][a-z0-9+.\-]*://", u, re.I) or u.startswith("//"):
        return None
    if "{{" in u or "{%" in u or "${" in u or "<%" in u:
        return None
    u = u.split("?", 1)[0].split("#", 1)[0]
    if u.startswith("@assets/"):
        u = "/assets/" + u[len("@assets/"):]
    if u.startswith("/"):
        p = posixpath.normpath(u.lstrip("/"))
    else:
        if not (u.startswith(".") or "/" in u or "." in u):
            return None  # bare module specifier ("three", "react")
        base = posixpath.dirname(src_path)
        p = posixpath.normpath(posixpath.join(base, u))
    if p.startswith("..") or p in (".", ""):
        return None
    return p


def _candidates_static(p):
    if p is None:
        return []
    ext = _ext(p)
    if ext not in STATIC_EXT:
        return []
    c = [p]
    if ext == "css":  # Jekyll compiles assets/css/x.scss -> x.css
        c.append(p[:-4] + ".scss")
        c.append(p[:-4] + ".sass")
    return c


def extract_refs(path, text):
    """Return the references in file ``path`` (repo-relative) with content ``text``."""
    if path.endswith(".ipynb"):
        text = notebook_text(text)
    refs = []

    def add(kind, raw, pos, cands):
        if cands:
            refs.append(Ref(kind, raw, text.count("\n", 0, pos) + 1, cands))

    fm = _FM.match(text)
    if fm:
        for m in _LAYOUT.finditer(fm.group(1)):
            name = m.group(1)
            if name not in ("none", "null", "nil"):
                cands = ["_layouts/%s" % name] if "." in name else ["_layouts/%s.html" % name, "_layouts/%s.md" % name]
                add("layout", name, fm.start(1) + m.start(), cands)
    for m in _INCLUDE.finditer(text):
        add("include", m.group(1), m.start(), ["_includes/" + m.group(1)])
    for m in _INCLUDE_REL.finditer(text):
        add("include_relative", m.group(1), m.start(), [posixpath.normpath(posixpath.join(posixpath.dirname(path), m.group(1)))])
    for m in _DATA.finditer(text):
        n = m.group(1)
        add("data", n, m.start(), ["_data/%s.%s" % (n, e) for e in ("yml", "yaml", "json", "csv")] + ["_data/" + n])
    ext = _ext(path)
    for rx, kind in ((_ATTR, "src"), (_MD_IMG, "image"), (_CSS_URL, "url"), (_BASEURL, "baseurl"),
                     (_GENERIC_ASSET, "asset"), (_ALIAS, "alias")):
        for m in rx.finditer(text):
            raw = m.group(1)
            if kind == "alias":
                raw = "@assets/" + raw
            add(kind, raw, m.start(), _candidates_static(_norm(raw, path)))
    if ext in ("js", "mjs", "html", "md", "ipynb", "markdown"):
        for m in _JS_IMPORT.finditer(text):
            raw = m.group(1)
            p = _norm(raw, path)
            if p is not None and not _ext(p):
                p = p + ".js"
            add("import", raw, m.start(), _candidates_static(p))
    if ext in ("scss", "sass"):
        for m in _SCSS_IMPORT.finditer(text):
            raw = m.group(1)
            if raw.startswith(("http", "sass:")):
                continue
            d, b = posixpath.split(raw)
            rel_bases = [posixpath.dirname(path), "_sass"]
            cands = []
            for base in rel_bases:
                for name in (b, "_" + b):
                    for e in ("", ".scss", ".sass", ".css"):
                        if e == "" and "." not in name:
                            continue
                        cands.append(posixpath.normpath(posixpath.join(base, d, name + e)))
            add("scss-import", raw, m.start(), cands)
    # dedupe by candidate list
    seen, out = set(), []
    for r in refs:
        key = (r.kind, tuple(r.candidates))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


class Resolution(object):
    """Result of resolving the dependency closure of a set of upstream files."""

    def __init__(self):
        self.import_paths = set()       # upstream paths to copy (requested + new deps)
        self.requested = set()
        self.new_deps = {}              # upstream dep path -> [referencing paths]
        self.satisfied = {}             # dep path -> "kit" | "kit (differs upstream)" | "import"
        self.missing = {}               # (ref.raw or candidate) -> [(referencing path, line)]
        self.excluded = {}              # dep upstream path excluded by config -> [referencing paths]
        self.generated = {}             # generated output path -> source it comes from

    def to_dict(self):
        return {
            "requested": sorted(self.requested),
            "new_dependencies": {k: sorted(v) for k, v in sorted(self.new_deps.items())},
            "missing": {k: ["%s:%s" % t for t in v] for k, v in sorted(self.missing.items())},
            "excluded_dependencies": {k: sorted(v) for k, v in sorted(self.excluded.items())},
            "generated": dict(sorted(self.generated.items())),
            "satisfied_in_kit": sorted(k for k, v in self.satisfied.items() if v.startswith("kit")),
        }


def resolve(requested, up_files, kit_files, read_upstream, cfg, follow_deps=True, max_files=5000):
    """Compute the files an import needs.

    requested     iterable of upstream paths the user asked for (already expanded)
    up_files      set of all upstream paths
    kit_files     set of all kit paths
    read_upstream callable(path) -> str|None, the text of an upstream file
    cfg           SyncConfig (for path mapping, exclude and generated rules)
    """
    res = Resolution()
    res.requested = set(requested)
    res.import_paths = set(requested)
    queue = sorted(requested)
    visited = set()
    gen_rules = []
    for g in cfg.generated:
        gen_rules.append((g["prefix"], g["source"]))

    kit_gen_cache = {}

    def generated_source(p):
        for prefix, source in gen_rules:
            if p.startswith(prefix):
                name = p[len(prefix):].split("/", 1)[0]
                pat = source.replace("{name}", name)
                if pat not in kit_gen_cache:
                    kit_gen_cache[pat] = any(globs.match(pat, f) for f in kit_files)
                ok = kit_gen_cache[pat] or any(globs.match(pat, f) for f in res.import_paths)
                return pat, ok
        return None, None

    while queue and len(visited) < max_files:
        src = queue.pop(0)
        if src in visited:
            continue
        visited.add(src)
        if not follow_deps or not is_text_path(src):
            continue
        text = read_upstream(src)
        if text is None:
            continue
        for ref in extract_refs(src, text):
            hit = None
            for c in ref.candidates:
                if c in res.import_paths:
                    hit = ("import", c)
                    break
                kc = cfg.to_kit(c)
                if kc in kit_files or c in kit_files:
                    hit = ("kit", c)
                    break
                if c in up_files:
                    hit = ("upstream", c)
                    break
            if hit is None:
                pat, srcs = generated_source(ref.candidates[0])
                if pat is not None:
                    res.generated[ref.candidates[0]] = pat
                    if not srcs:
                        res.missing.setdefault(ref.candidates[0], []).append((src, ref.line))
                    continue
                res.missing.setdefault(ref.candidates[0], []).append((src, ref.line))
                continue
            where, path = hit
            if where == "kit":
                res.satisfied[path] = "kit"
            elif where == "import":
                res.satisfied.setdefault(path, "import")
            else:
                if cfg.is_excluded(path):
                    res.excluded.setdefault(path, set()).add(src)
                    continue
                res.import_paths.add(path)
                res.new_deps.setdefault(path, set()).add(src)
                queue.append(path)
    return res
