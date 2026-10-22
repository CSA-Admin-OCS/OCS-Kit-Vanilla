"""Shared state for scan/import/update: the kit repo, its sync config, the upstream view and the catalog."""

import os

from . import catalog as catalog_mod
from . import deps, media, scrub
from .scrub import list_repo_files


class KitContext(object):
    def __init__(self, kit_root, cfg, upstream, canonical_path=None, log=print):
        self.root = os.path.abspath(kit_root)
        self.cfg = cfg
        self.up = upstream
        self.log = log
        self.kit_files = set(list_repo_files(self.root))
        self.catalog_path = os.path.join(self.root, cfg.catalog_file)
        self.catalog = None
        if os.path.isfile(self.catalog_path):
            self.catalog = catalog_mod.Catalog.load(self.catalog_path, cfg.catalog_key)
        self.canonical = None
        if canonical_path and os.path.isfile(canonical_path):
            self.canonical = catalog_mod.load_json(canonical_path)
        self.gate = scrub.ScrubGate(cfg.scrub, base_dir=self.root)
        self._tx_cache = {}

    # ---- content as it would land in the kit ---------------------------------
    def transformed(self, upath):
        """(kit_path, bytes, replace_hits) for upstream file ``upath`` after replace rules / metadata strip."""
        if upath in self._tx_cache:
            return self._tx_cache[upath]
        kpath = self.cfg.to_kit(upath)
        data = self.up.read_bytes(upath)
        hits = []
        if data is None:
            res = (kpath, None, hits)
        elif scrub.is_binary(data):
            if self.cfg.scrub.get("strip_metadata", True):
                data = media.strip_metadata(kpath, data)
            res = (kpath, data, hits)
        else:
            text = data.decode("utf-8", "replace") if not _is_utf8(data) else data.decode("utf-8")
            new, hits = self.cfg.apply_replace(kpath, text)
            res = (kpath, new.encode("utf-8") if hits else data, hits)
        if len(self._tx_cache) < 20000:
            self._tx_cache[upath] = res
        return res

    def kit_bytes(self, kpath):
        p = os.path.join(self.root, kpath)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def upstream_kit_view(self):
        """{upstream path: kit path} for every upstream file that is not excluded."""
        out = {}
        for u in sorted(self.up.files):
            if self.cfg.is_excluded(u):
                continue
            out[u] = self.cfg.to_kit(u)
        return out

    def resolve(self, upaths, follow=True):
        return deps.resolve(upaths, self.up.files, self.kit_files, self.up.read_text, self.cfg, follow_deps=follow)

    def owner(self, kpath):
        if self.catalog is None:
            return None
        return self.catalog.owner(kpath)


def _is_utf8(b):
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
