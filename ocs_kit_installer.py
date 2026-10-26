#!/usr/bin/env python3
"""OCS-in-a-Kit installer.

One file, Python 3.8+ standard library only. It installs the OCS-in-a-Kit backends
(Flask + Spring, in Docker) and/or the website (OCS-Kit-Pages: published to GitHub Pages
and/or previewed locally) on this computer.

    python3 ocs_kit_installer.py              # web-page wizard (terminal wizard when there is no display)
    python3 ocs_kit_installer.py --cli        # terminal wizard
    python3 ocs_kit_installer.py --answers answers.json    # unattended
    python3 ocs_kit_installer.py --list-features
    python3 ocs_kit_installer.py --change-features --kit education --features +finance-sim,-hallpass

Kits and features are read only from kits/catalog.json (next to this script, or downloaded from
the OCS-Kit repository), so a feature added to the catalog shows up here automatically.
"""

import argparse
import base64
import getpass
import http.server
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KIT_VERSION = "v0.2.0"
DEFAULT_REPO_BASE = "https://github.com/CSA-Admin-OCS"
UPSTREAM_OWNER = "CSA-Admin-OCS"
CATALOG_URL = "https://raw.githubusercontent.com/CSA-Admin-OCS/OCS-Kit/{ref}/kits/catalog.json"

REPOS = {
    "flask": {"name": "OCS-Kit-Flask", "default_branch": "main"},
    "spring": {"name": "OCS-Kit-Spring", "default_branch": "master"},
    "pages": {"name": "OCS-Kit-Pages", "default_branch": "main"},
}
PAGES_WORKFLOW = "jekyll-gh-pages.yml"

FLASK_PORT = 8587
SOCKET_PORT = 8500
SPRING_PORT = 8585
SPRING_CHAT_PORT = 8589
DEFAULT_PREVIEW_PORT = 4500
# Container ports per compose service (the backends' docker-compose.yml contract). With
# --port-offset N the host ports become port+N (for running next to another copy).
COMPOSE_SERVICES = {
    "flask": {"web": [FLASK_PORT], "socketio": [SOCKET_PORT], "code_runner": []},
    "spring": {"web": [SPRING_PORT, SPRING_CHAT_PORT], "code_runner": []},
}

ANSWERS_FILE = "ocs-kit-installer.json"
CREDENTIALS_FILE = "CREDENTIALS.txt"
FEATURE_SETUP_FILE = "FEATURE-SETUP.md"
LOG_FILE = "install.log"
PREVIEW_CONTAINER = "ocskit-pages-preview"
PREVIEW_IMAGE = "ocskit-pages-preview:1"
COMPOSE_PROJECTS = {"flask": "ocskit-flask", "spring": "ocskit-spring"}

# Spring requires one of these symbols in seeded passwords. '$' is never allowed:
# docker compose expands it inside env files.
SPRING_SYMBOLS = "~!@#%^&*()"
PASSWORD_ALLOWED = string.ascii_letters + string.digits + SPRING_SYMBOLS + "-_.+=?"
GENERATED_SYMBOLS = "~!@%^&*"

# Lines Spring needs uncommented on plain-http localhost (and commented in production)
SPRING_LOCAL_COOKIE_LINES = [
    ("jwt.cookie.secure", "false"),
    ("jwt.cookie.same-site", "Lax"),
    ("server.servlet.session.cookie.secure", "false"),
    ("server.servlet.session.cookie.same-site", "lax"),
]

# Secrets kept across re-runs (never regenerated when already present in .env)
SECRET_KEYS = {"flask": ["SECRET_KEY", "RESET_TOKEN_SECRET"],
               "spring": ["JWT_SECRET", "RESET_TOKEN_SECRET"]}

# (answers field, env var, label)
PASSWORD_FIELDS = [
    ("admin_password", "ADMIN_PASSWORD", "Admin account (uid: admin)"),
    ("teacher_password", "TEACHER_PASSWORD", "Teacher account (uid: teacher)"),
    ("default_password", "DEFAULT_PASSWORD", "Default password (sample students, password resets)"),
]
# Environment variables that can supply secrets in unattended mode
SECRET_ENV = {"admin_password": "OCS_ADMIN_PASSWORD", "teacher_password": "OCS_TEACHER_PASSWORD",
              "default_password": "OCS_DEFAULT_PASSWORD"}
# Feature settings (from the catalog) can come from OCS_SETTING_<KEY> in unattended mode,
# e.g. OCS_SETTING_GEMINI_API_KEY or OCS_SETTING_KIT_VOICERSS_API_KEY.
SETTING_ENV_PREFIX = "OCS_SETTING_"
SECRET_FIELDS = set(SECRET_ENV)

DEFAULT_ANSWERS = {
    "action": "install",            # install | change-features
    "kit": "education",
    "features": [],                 # overrides relative to the kit preset, e.g. ["+finance-sim", "-hallpass"]
    "install_backend": True,
    "install_website": True,
    "mode": "local",                # local | production
    "school_name": "",
    "admin_email": "",
    "install_dir": str(Path.home() / "ocs-kit"),
    "domain": "",
    "settings": {},                 # feature settings from the catalog: {"service:KEY" or "KEY": value}
    "admin_password": "",
    "teacher_password": "",
    "default_password": "",
    "website_preview": True,
    "website_publish": False,
    "github_owner": "",
    "github_repo": "ocs-kit-pages",
    "preview_port": DEFAULT_PREVIEW_PORT,
    "port_offset": 0,               # host ports = default + offset (side-by-side/testing installs)
}

UNSAFE_TEXT = re.compile(r"[$`\\\"'\r\n]")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
GH_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
GH_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


# ---------------------------------------------------------------------------
# Kit catalog (the only source of kit and feature ids)
# ---------------------------------------------------------------------------

class CatalogError(Exception):
    pass


def validate_catalog(catalog):
    """Check the catalog's shape and that every kit only names known features."""
    if not isinstance(catalog, dict) or not isinstance(catalog.get("features"), dict) \
            or not isinstance(catalog.get("kits"), dict) or not catalog["kits"]:
        raise CatalogError("catalog.json must have 'features' and 'kits' objects")
    ids = set(catalog["features"])
    for kit_id, kit in catalog["kits"].items():
        feats = kit.get("features")
        if not isinstance(feats, list):
            raise CatalogError("kit %r has no 'features' list" % kit_id)
        unknown = [f for f in feats if f != "*" and f not in ids]
        if unknown:
            raise CatalogError("kit %r names unknown features: %s" % (kit_id, ", ".join(unknown)))
    return catalog


def load_catalog(path=None, ref=KIT_VERSION, repo_base=None):
    """Load kits/catalog.json: explicit path, next to this script, a local repo base, or GitHub."""
    candidates = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(Path(__file__).resolve().parent / "kits" / "catalog.json")
        if repo_base and os.path.isdir(repo_base):
            candidates.append(Path(repo_base) / "OCS-Kit" / "kits" / "catalog.json")
    for cand in candidates:
        if cand.is_file():
            with open(cand, encoding="utf-8") as fh:
                return validate_catalog(json.load(fh))
    if path:
        raise CatalogError("catalog not found: %s" % path)
    errors = []
    for r in dict.fromkeys([ref, "main"]):
        url = CATALOG_URL.format(ref=r)
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return validate_catalog(json.loads(resp.read().decode("utf-8")))
        except Exception as exc:  # noqa: BLE001 - report and try the next ref
            errors.append("%s: %s" % (url, exc))
    raise CatalogError("could not load kits/catalog.json (" + "; ".join(errors) + ")")


def feature_ids(catalog):
    return list(catalog["features"])


def always_on_features(catalog):
    return [f for f, v in catalog["features"].items() if v.get("always_on")]


def preset_features(catalog, kit):
    """Feature ids a kit enables by default ('*' = all), always-on features included."""
    if kit not in catalog["kits"]:
        raise CatalogError("unknown kit %r (choose from: %s)" % (kit, ", ".join(catalog["kits"])))
    feats = catalog["kits"][kit]["features"]
    chosen = set(feature_ids(catalog)) if "*" in feats else set(feats)
    chosen |= set(always_on_features(catalog))
    return [f for f in feature_ids(catalog) if f in chosen]


def parse_feature_spec(spec):
    """'+a, -b' / ['+a','-b'] / '*' -> list of tokens."""
    if spec is None:
        return []
    if isinstance(spec, str):
        parts = spec.split(",")
    else:
        parts = list(spec)
    return [str(p).strip().replace(" ", "") for p in parts if str(p).strip()]


def resolve_features(catalog, kit, spec):
    """Turn a FEATURES spec into the final set of enabled features.

    Accepts overrides relative to the kit preset ("+x,-y"), an explicit list ("a,b") or "*".
    """
    base = preset_features(catalog, kit)
    tokens = parse_feature_spec(spec)
    if not tokens:
        return base
    ids = feature_ids(catalog)
    locked = set(always_on_features(catalog))
    if tokens == ["*"]:
        return ids
    signed = [t[0] in "+-" for t in tokens]
    if any(signed) and not all(signed):
        raise CatalogError("mix of +/- overrides and plain feature ids: use one style")
    names = [t[1:] if t[0] in "+-" else t for t in tokens]
    unknown = [n for n in names if n not in ids]
    if unknown:
        raise CatalogError("unknown feature(s): %s (valid: %s)" % (", ".join(unknown), ", ".join(ids)))
    if all(signed):
        chosen = set(base)
        for tok in tokens:
            if tok[0] == "+":
                chosen.add(tok[1:])
            else:
                if tok[1:] in locked:
                    raise CatalogError("feature %r is always on and cannot be removed" % tok[1:])
                chosen.discard(tok[1:])
    else:
        chosen = set(names)
    chosen |= locked
    return [f for f in ids if f in chosen]


def compute_overrides(catalog, kit, selected):
    """Express a selected feature set as ["+x", "-y"] relative to the kit preset."""
    base = set(preset_features(catalog, kit))
    sel = set(selected) | set(always_on_features(catalog))
    ids = feature_ids(catalog)
    plus = ["+" + f for f in ids if f in sel and f not in base]
    minus = ["-" + f for f in ids if f in base and f not in sel]
    return plus + minus


def normalize_overrides(catalog, kit, spec):
    """Any accepted spec -> canonical override list."""
    return compute_overrides(catalog, kit, resolve_features(catalog, kit, spec))


def describe_selection(catalog, kit, overrides):
    """'OCS Education + Finance simulation − Hall pass'."""
    text = catalog["kits"][kit].get("name", kit)
    for tok in overrides:
        name = catalog["features"].get(tok[1:], {}).get("name", tok[1:])
        text += (" + " if tok[0] == "+" else " \u2212 ") + name
    return text


def features_env_value(overrides):
    return ",".join(overrides)


# Labels (from the catalog's "labels" map) that deserve a note when such a feature is switched on.
NOTE_LABELS = ("needs-api-key", "privacy-sensitive", "needs-external-service", "third-party-data")
API_KEY_LABEL = "needs-api-key"


def feature_category(catalog, fid):
    return catalog["features"][fid].get("category") or "other"


def category_info(catalog, cid):
    info = (catalog.get("categories") or {}).get(cid) or {}
    return {"id": cid, "name": info.get("name") or cid.replace("-", " ").capitalize(),
            "description": info.get("description", "")}


def label_info(catalog, label):
    return {"id": label, "description": (catalog.get("labels") or {}).get(label, "")}


def feature_groups(catalog):
    """[(category_info, [feature ids])] in the catalog's category order, then any others."""
    order = list(catalog.get("categories") or {})
    groups = {}
    for fid in feature_ids(catalog):
        cid = feature_category(catalog, fid)
        if cid not in order:
            order.append(cid)
        groups.setdefault(cid, []).append(fid)
    return [(category_info(catalog, c), groups[c]) for c in order if c in groups]


def features_with_label(catalog, selected, label):
    return [f for f in selected if label in (catalog["features"][f].get("labels") or [])]


def needs_api_key(catalog, kit, overrides):
    """Enabled features labelled needs-api-key (the wizard only asks for a key when non-empty)."""
    return features_with_label(catalog, resolve_features(catalog, kit, overrides), API_KEY_LABEL)


def selection_notes(catalog, kit, overrides):
    """Short notes about the selection: added features from a category the kit doesn't cover,
    and enabled features carrying a label from NOTE_LABELS."""
    notes = []
    preset_cats = set(feature_category(catalog, f) for f in preset_features(catalog, kit)
                      if not catalog["features"][f].get("always_on"))
    added = [t[1:] for t in overrides if t[0] == "+"]
    for fid in added:
        cid = feature_category(catalog, fid)
        if cid not in preset_cats:
            cat = category_info(catalog, cid)
            notes.append("%s is a %s feature, not normally part of %s. %s" % (
                catalog["features"][fid].get("name", fid), cat["name"],
                catalog["kits"][kit].get("name", kit), cat["description"]))
    selected = resolve_features(catalog, kit, overrides)
    for label in NOTE_LABELS:
        feats = features_with_label(catalog, selected, label)
        if feats:
            names = ", ".join(catalog["features"][f].get("name", f) for f in feats)
            notes.append("%s (%s): %s" % (names, label, label_info(catalog, label)["description"]))
    return notes


def selection_info(catalog, kit, spec):
    overrides = normalize_overrides(catalog, kit, spec)
    return {"overrides": overrides, "selected": resolve_features(catalog, kit, overrides),
            "summary": describe_selection(catalog, kit, overrides),
            "notes": selection_notes(catalog, kit, overrides),
            "api_key_features": [catalog["features"][f].get("name", f)
                                 for f in needs_api_key(catalog, kit, overrides)],
            "roles": kit_roles(catalog, kit), "password_labels": password_labels(catalog, kit),
            "setup": [dict(e, settings=[dict(st, managed=st["key"] in managed_keys(st["service"]))
                                        for st in e["settings"]]) for e in feature_setup_plan(catalog, kit, overrides)]}


# ---------------------------------------------------------------------------
# Roles and per-feature setup (all from the catalog)
# ---------------------------------------------------------------------------

# Backend .env keys for each catalog role (the backends' seeding contract).
ROLE_ENV = {
    "admin": {"uid": "ADMIN_UID", "name": {"flask": "ADMIN_USER", "spring": "ADMIN_NAME"}},
    "staff": {"uid": "TEACHER_UID", "name": {"flask": "TEACHER_USER", "spring": "TEACHER_NAME"}},
    "member": {"uid": "USER_UID", "name": {"flask": "USER_NAME", "spring": "USER_NAME"}},
}
# Which password question belongs to which role.
ROLE_PASSWORD = {"admin": "admin_password", "staff": "teacher_password", "member": "default_password"}
DEFAULT_ROLES = {"admin": {"label": "Admin", "seed_uid": "admin"},
                 "staff": {"label": "Teacher", "seed_uid": "teacher"},
                 "member": {"label": "User", "seed_uid": "user"}}
SERVICES = ("flask", "spring", "pages")


def kit_roles(catalog, kit):
    """[{role, label, seed_uid, description}] for the kit, in the catalog's role order."""
    kit_def = catalog["kits"].get(kit, {})
    per_kit = kit_def.get("roles") or {}
    order = list(catalog.get("roles") or {}) or list(DEFAULT_ROLES)
    for r in per_kit:
        if r not in order:
            order.append(r)
    out = []
    for role in order:
        r = dict(DEFAULT_ROLES.get(role, {}), **(per_kit.get(role) or {}))
        if not r.get("seed_uid"):
            continue
        out.append({"role": role, "label": r.get("label") or role.capitalize(), "seed_uid": r["seed_uid"],
                    "description": ((catalog.get("roles") or {}).get(role) or {}).get("description", "")})
    return out


def kit_roles_map(catalog, kit):
    return {r["role"]: r for r in kit_roles(catalog, kit)}


def password_labels(catalog, kit):
    """{answers field: label} for the three password questions, using the kit's role names."""
    roles = kit_roles_map(catalog, kit)
    labels = {}
    for role, field in ROLE_PASSWORD.items():
        r = roles.get(role) or dict(DEFAULT_ROLES[role], role=role)
        if role == "member":
            labels[field] = "%s and default password (user: %s; also used for password resets)" % (
                r["label"], r["seed_uid"])
        else:
            labels[field] = "%s password (user: %s)" % (r["label"], r["seed_uid"])
    return labels


def setting_id(setting):
    return "%s:%s" % (setting["service"], setting["key"])


def feature_setup_plan(catalog, kit, overrides):
    """One entry per enabled feature: summary/steps/verify plus its settings.
    A setting used by several features (same service+key) is listed once, under the first
    feature, merged: required if any feature requires it, secret if any marks it secret."""
    plan = []
    seen = {}
    for fid in resolve_features(catalog, kit, overrides):
        f = catalog["features"][fid]
        setup = f.get("setup") or {}
        entry = {"feature": fid, "name": f.get("name", fid), "summary": setup.get("summary", ""),
                 "steps": list(setup.get("steps") or []), "verify": setup.get("verify", ""), "settings": [],
                 "all_settings": [], "required": []}
        for raw in setup.get("settings") or []:
            if raw.get("service") not in SERVICES or not raw.get("key"):
                continue
            sid = setting_id(raw)
            entry["all_settings"].append(sid)
            if raw.get("required"):
                entry["required"].append(sid)
            if sid in seen:
                m = seen[sid]
                m["required"] = m["required"] or bool(raw.get("required"))
                m["secret"] = m["secret"] or bool(raw.get("secret"))
                m["used_by"].append(entry["name"])
                if raw.get("help") and raw["help"] not in m["help"]:
                    m["help"] += " " + raw["help"]
                continue
            m = {"id": sid, "key": raw["key"], "service": raw["service"], "required": bool(raw.get("required")),
                 "secret": bool(raw.get("secret")), "help": raw.get("help", ""), "get_it": raw.get("get_it", ""),
                 "example": raw.get("example", ""), "used_by": [entry["name"]]}
            seen[sid] = m
            entry["settings"].append(m)
        plan.append(entry)
    return plan


def all_catalog_settings(catalog):
    out = {}
    for f in catalog["features"].values():
        for s_ in (f.get("setup") or {}).get("settings") or []:
            if s_.get("key") and s_.get("service"):
                out.setdefault(setting_id(s_), s_)
    return out


def setting_env_name(key):
    return SETTING_ENV_PREFIX + re.sub(r"[^A-Za-z0-9]", "_", key).upper()


def setting_value(settings, setting):
    """Value typed for a setting: by 'service:KEY' first, then bare 'KEY'."""
    v = settings.get(setting["id"] if "id" in setting else setting_id(setting))
    if not v:
        v = settings.get(setting["key"])
    return (v or "").strip()


def managed_keys(kind):
    """.env keys the installer itself writes (so feature settings don't ask for them again)."""
    if kind == "pages":
        return {"kit.school_name", "kit.name", "kit.features"}
    dummy = normalize_answers({"school_name": "x", "admin_email": "a@b.cd", "kit": "x"}, env={})
    keys = set(build_env_values(kind, dummy, [], {}, {"RESET_TOKEN_SECRET": "x"}, DEFAULT_ROLES))
    return keys | {"ADMIN_EMAIL"} if kind == "spring" else keys


def settings_for_service(catalog, kit, overrides, settings, service):
    """{KEY: value} of non-empty typed settings for one service (enabled features only)."""
    out = {}
    for entry in feature_setup_plan(catalog, kit, overrides):
        for st in entry["settings"]:
            if st["service"] == service:
                v = setting_value(settings, st)
                if v:
                    out[st["key"]] = v
    return out


def pages_setting_edits(values):
    """{'kit.voicerss_api_key': v, 'title': v, 'kit.backend.x': v} -> edit_config_yml kwargs."""
    top, kit, backend, unsupported = {}, {}, {}, []
    for key, v in values.items():
        parts = key.split(".")
        if len(parts) == 1:
            top[parts[0]] = v
        elif len(parts) == 2 and parts[0] == "kit":
            kit[parts[1]] = v
        elif len(parts) == 3 and parts[:2] == ["kit", "backend"]:
            backend[parts[2]] = v
        else:
            unsupported.append(key)
    return {"top": top, "kit": kit, "backend": backend}, unsupported


def feature_status(catalog, kit, overrides, settings, installed, existing=None, managed=None):
    """Per enabled feature: ready / needs-setup, with the required settings still missing.
    installed: set of services set up on this computer; existing: {service: {KEY: value}}."""
    existing = existing or {}
    managed = managed if managed is not None else {svc: managed_keys(svc) for svc in SERVICES}
    plan = feature_setup_plan(catalog, kit, overrides)
    by_id = {}
    for entry in plan:
        for st in entry["settings"]:
            by_id[st["id"]] = st
    out = []
    for entry in plan:
        missing = []
        for sid in entry["required"]:
            st = by_id[sid]
            have = (setting_value(settings, st) or (existing.get(st["service"]) or {}).get(st["key"])
                    or st["key"] in managed.get(st["service"], set()))
            if not have:
                missing.append({"key": st["key"], "service": st["service"], "help": st["help"],
                                "get_it": st["get_it"], "here": st["service"] in installed})
        out.append({"feature": entry["feature"], "name": entry["name"], "summary": entry["summary"],
                    "status": "needs-setup" if missing else "ready", "missing": missing,
                    "steps": entry["steps"], "verify": entry["verify"]})
    return out


SERVICE_WHERE = {"flask": "flask/.env (Flask backend)", "spring": "spring/.env (Spring backend)",
                 "pages": "_config.yml of the website (public!)"}


def feature_setup_markdown(catalog, kit, overrides, status, school=""):
    """A readable FEATURE-SETUP.md for the enabled features."""
    plan = {e["feature"]: e for e in feature_setup_plan(catalog, kit, overrides)}
    allset = all_catalog_settings(catalog)
    lines = ["# Feature setup guide" + (" - " + school if school else ""), "",
             "Kit: **%s**" % describe_selection(catalog, kit, overrides), ""]
    mgmt = (catalog.get("feature_management") or {}).get("description")
    lines += ["Feature changes after install are made by **Admins** in the website's admin dashboard, "
              "or by re-running the installer (`--change-features`)." + ((" " + mgmt) if mgmt else ""), ""]
    lines += ["| Feature | Status |", "|---|---|"]
    for st in status:
        lines.append("| %s | %s |" % (st["name"], "Ready" if st["status"] == "ready" else "**Needs setup**"))
    lines.append("")
    for st in status:
        e = plan[st["feature"]]
        lines += ["## %s (`%s`)" % (st["name"], st["feature"]), ""]
        if e["summary"]:
            lines += [e["summary"], ""]
        lines += ["Status: %s" % ("ready" if st["status"] == "ready" else "**needs setup** - missing " +
                                  ", ".join("`%s` in %s" % (m["key"], SERVICE_WHERE[m["service"]])
                                            for m in st["missing"])), ""]
        sids = e["all_settings"]
        if sids:
            lines += ["| Setting | Where | Required | What it is |", "|---|---|---|---|"]
            for sid in sids:
                raw = allset.get(sid, {})
                help_ = (raw.get("help") or "").replace("|", "\\|")
                if raw.get("get_it"):
                    help_ += " Get it: %s" % raw["get_it"]
                if raw.get("example"):
                    help_ += " Example: `%s`" % raw["example"]
                lines.append("| `%s` | %s | %s | %s |" % (
                    raw.get("key", sid), SERVICE_WHERE.get(raw.get("service"), raw.get("service")),
                    "yes" if sid in e["required"] else "no", help_))
            lines.append("")
            lines += ["After editing a backend .env, restart it: `docker compose up -d` in that folder.", ""]
        if e["steps"]:
            lines += ["Steps:", ""] + ["%d. %s" % (i, s_) for i, s_ in enumerate(e["steps"], 1)] + [""]
        if e["verify"]:
            lines += ["Check it works: %s" % e["verify"], ""]
    return "\n".join(lines)


def format_feature_list(catalog):
    lines = ["Kits:"]
    for kid, kit in catalog["kits"].items():
        lines.append("  %-10s %s - %s" % (kid, kit.get("name", kid), kit.get("description", "")))
        lines.append("             features: %s" % ", ".join(preset_features(catalog, kid)))
    for cat, fids in feature_groups(catalog):
        lines.append("")
        lines.append("%s features%s" % (cat["name"], (" - " + cat["description"]) if cat["description"] else ""))
        for fid in fids:
            f = catalog["features"][fid]
            tag = " (always on)" if f.get("always_on") else ""
            in_kits = [k for k in catalog["kits"] if fid in preset_features(catalog, k)]
            lines.append("  %-17s %s%s - %s" % (fid, f.get("name", fid), tag, f.get("description", "")))
            if f.get("labels"):
                lines.append("  %-17s labels: %s" % ("", ", ".join(f["labels"])))
            lines.append("  %-17s in kits: %s" % ("", ", ".join(in_kits) or "(none: add with +%s)" % fid))
    labels = catalog.get("labels") or {}
    if labels:
        lines.append("")
        lines.append("Labels:")
        for lid, desc in labels.items():
            lines.append("  %-22s %s" % (lid, desc))
    lines.append("")
    lines.append("Customize with --kit <kit> --features +id,-id   (or an explicit list, or '*')")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Passwords and secrets
# ---------------------------------------------------------------------------

def validate_password(pw):
    """Return a list of problems (empty = OK). Rules match the Spring backend's policy."""
    problems = []
    if len(pw) < 8:
        problems.append("at least 8 characters")
    if not re.search(r"[A-Z]", pw):
        problems.append("an uppercase letter")
    if not re.search(r"[a-z]", pw):
        problems.append("a lowercase letter")
    if not re.search(r"[0-9]", pw):
        problems.append("a digit")
    if not any(c in SPRING_SYMBOLS for c in pw):
        problems.append("a symbol from " + SPRING_SYMBOLS)
    bad = sorted(set(c for c in pw if c not in PASSWORD_ALLOWED))
    if bad:
        problems.append("no " + " ".join(repr(c) for c in bad) + " (not allowed)")
    return problems


def generate_password(length=16):
    rng = secrets.SystemRandom()
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, GENERATED_SYMBOLS]
    chars = [rng.choice(p) for p in pools]
    everything = string.ascii_letters + string.digits + GENERATED_SYMBOLS
    chars += [rng.choice(everything) for _ in range(max(length, 8) - len(chars))]
    rng.shuffle(chars)
    return "".join(chars)


def generate_secret(key):
    if key == "JWT_SECRET":
        return base64.b64encode(secrets.token_bytes(48)).decode("ascii")
    return secrets.token_hex(32)


def choose_value(key, typed, existing, shared, generator):
    """typed > this backend's existing .env > value shared with the other backend > new."""
    if typed:
        return typed
    val = (existing.get(key) or "").strip()
    if val:
        return val
    if key in shared and shared[key]:
        return shared[key]
    shared[key] = generator()
    return shared[key]


# ---------------------------------------------------------------------------
# .env editing
# ---------------------------------------------------------------------------

def _env_line_re(key):
    return re.compile(r"^[ \t]*(#[ \t]*)?" + re.escape(key) + r"[ \t]*=.*$", re.M)


def parse_env(text):
    """KEY=VALUE pairs from uncommented lines (last one wins, surrounding quotes stripped)."""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def set_env_values(text, values):
    """Set KEY=value for each item. Replaces the first uncommented line for the key, otherwise
    the first commented one ('#KEY=' / '# KEY='), otherwise appends."""
    for key, value in values.items():
        rx = _env_line_re(key)
        matches = list(rx.finditer(text))
        new_line = "%s=%s" % (key, value)
        if not matches:
            if text and not text.endswith("\n"):
                text += "\n"
            text += new_line + "\n"
            continue
        target = next((m for m in matches if m.group(1) is None), matches[0])
        text = text[:target.start()] + new_line + text[target.end():]
    return text


def comment_env_keys(text, keys):
    """Comment out uncommented KEY= lines."""
    for key in keys:
        rx = re.compile(r"^[ \t]*(" + re.escape(key) + r"[ \t]*=.*)$", re.M)
        text = rx.sub(lambda m: "# " + m.group(1), text)
    return text


def is_local(answers):
    return answers.get("mode") != "production"


def host_ports(answers):
    """Host ports of the backends for this install (default ports + port_offset)."""
    o = int(answers.get("port_offset") or 0)
    return {"flask": FLASK_PORT + o, "socket": SOCKET_PORT + o, "spring": SPRING_PORT + o,
            "chat": SPRING_CHAT_PORT + o}


def render_compose_override(kind, offset):
    """docker-compose.override.yml remapping host ports (+offset) and using separate image names,
    so a second copy can run next to an existing one. Needs Docker Compose 2.24+ (!override)."""
    lines = ["# Written by the OCS-in-a-Kit installer (--port-offset %d). Delete it and run" % offset,
             "# 'docker compose up -d --build' to go back to the default ports.", "services:"]
    for svc, ports in COMPOSE_SERVICES[kind].items():
        lines.append("  %s:" % svc)
        lines.append("    image: ocskit_%s_%s" % (kind, svc))
        if ports:
            lines.append("    ports: !override")
            lines += ['      - "%d:%d"' % (p + offset, p) for p in ports]
    return "\n".join(lines) + "\n"


def frontend_url(answers):
    if is_local(answers):
        return "http://localhost:%d" % int(answers.get("preview_port") or DEFAULT_PREVIEW_PORT)
    return "https://pages.%s" % answers["domain"]


def github_pages_url(owner, repo):
    owner_l = owner.lower()
    if repo.lower() == owner_l + ".github.io":
        return "https://%s.github.io/" % owner_l
    return "https://%s.github.io/%s/" % (owner_l, repo)


def cors_origins(answers, kind):
    if is_local(answers):
        port = int(answers.get("preview_port") or DEFAULT_PREVIEW_PORT)
        origins = ["http://localhost:%d" % port, "http://127.0.0.1:%d" % port,
                   "http://localhost:4500", "http://127.0.0.1:4500",
                   "http://localhost:4000", "http://127.0.0.1:4000"]
        if kind == "spring":
            origins.append("http://localhost:%d" % host_ports(answers)["spring"])
    else:
        origins = ["https://pages.%s" % answers["domain"]]
        if answers.get("github_owner"):
            origins.append("https://%s.github.io" % answers["github_owner"].lower())
    return ",".join(dict.fromkeys(origins))


def build_env_values(kind, answers, overrides, existing, shared, roles=None):
    """All values the installer manages in a backend's .env.

    `existing` = this backend's current .env (empty on a fresh install); `shared` carries values
    that must match across backends (RESET_TOKEN_SECRET, generated passwords)."""
    local = is_local(answers)
    domain = answers.get("domain", "")
    values = {
        "COMPOSE_PROJECT_NAME": COMPOSE_PROJECTS[kind],
        "SCHOOL_NAME": answers["school_name"],
        "FRONTEND_URL": frontend_url(answers),
        "CORS_ORIGINS": cors_origins(answers, kind),
        "COOKIE_DOMAIN": "" if local else "." + domain,
        "IS_PRODUCTION": "false" if local else "true",
        "KIT": answers["kit"],
        "FEATURES": features_env_value(overrides),
    }
    for key in SECRET_KEYS[kind]:
        values[key] = choose_value(key, None, existing, shared, lambda k=key: generate_secret(k))
    for field, key, _label in PASSWORD_FIELDS:
        values[key] = choose_value(key, answers.get(field), existing, shared, generate_password)
    for role, r in (roles or {}).items():
        envs = ROLE_ENV.get(role)
        if envs:
            values[envs["uid"]] = r["seed_uid"]
            values[envs["name"][kind]] = r["label"] + " User"
    if kind == "flask":
        values["SITE_DOMAIN"] = "" if local else domain
    else:
        values["SITE_DOMAIN"] = "localhost" if local else "spring." + domain
        values["ADMIN_EMAIL"] = answers["admin_email"]
        values["KIT_DEV_MODE"] = "false"
    return values


def apply_env(kind, text, values, local):
    text = set_env_values(text, values)
    if kind == "spring":
        if local:
            text = set_env_values(text, dict(SPRING_LOCAL_COOKIE_LINES))
        else:
            text = comment_env_keys(text, [k for k, _ in SPRING_LOCAL_COOKIE_LINES])
    return text


# ---------------------------------------------------------------------------
# nginx templates
# ---------------------------------------------------------------------------

def render_template(text, variables):
    """envsubst with an explicit variable list: only ${NAME} for NAME in variables is replaced,
    nginx's own $variables are left alone."""
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                  lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0), text)


def render_nginx_configs(flask_template, spring_template, domain, ports=None):
    ports = ports or host_ports({})
    spring = render_template(spring_template, {"SITE_DOMAIN": "spring." + domain})
    if ports["spring"] != SPRING_PORT:  # the Spring template hard-codes its ports
        spring = spring.replace("localhost:%d" % SPRING_PORT, "localhost:%d" % ports["spring"]) \
                       .replace("localhost:%d" % SPRING_CHAT_PORT, "localhost:%d" % ports["chat"])
    return {
        "flask.conf": render_template(flask_template, {"SITE_DOMAIN": domain, "FLASK_PORT": ports["flask"],
                                                       "SOCKET_PORT": ports["socket"]}),
        "spring.conf": spring,
    }


def nginx_commands(nginx_dir, domain):
    q = shlex.quote
    cmds = []
    for name in ("flask", "spring"):
        src = os.path.join(str(nginx_dir), name + ".conf")
        cmds.append("sudo cp %s /etc/nginx/sites-available/ocs-%s.conf" % (q(src), name))
        cmds.append("sudo ln -sf /etc/nginx/sites-available/ocs-%s.conf /etc/nginx/sites-enabled/ocs-%s.conf"
                    % (name, name))
    cmds.append("sudo nginx -t && sudo systemctl reload nginx")
    cmds.append("sudo certbot --nginx -d flask.%s -d spring.%s" % (domain, domain))
    return cmds


def dns_records(answers):
    d = answers["domain"]
    rows = []
    if answers.get("install_backend"):
        rows.append(("A", "flask." + d, "this server's public IP address"))
        rows.append(("A", "spring." + d, "this server's public IP address"))
    if answers.get("install_website") and answers.get("website_publish"):
        owner = (answers.get("github_owner") or "<github-owner>").lower()
        rows.append(("CNAME", "pages." + d, owner + ".github.io"))
    elif answers.get("install_website") or not answers.get("install_backend"):
        rows.append(("CNAME", "pages." + d, "wherever you host the website (e.g. <owner>.github.io)"))
    return rows


# ---------------------------------------------------------------------------
# _config.yml editing (Pages)
# ---------------------------------------------------------------------------

def yaml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(v)) for v in value) + "]"
    return json.dumps(str(value))


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _is_content(line):
    s = line.strip()
    return bool(s) and not s.startswith("#")


def _key_match(line, key, indent):
    return re.match(r"^" + " " * indent + re.escape(key) + r":(\s|$)", line) is not None


def _set_child(lines, start, end, key, indent, value_text):
    """Within lines[start:end] (children of a block at `indent`), set `key: value`.
    Nested content under the key is replaced. Returns the new end index."""
    i = start
    while i < end:
        if _key_match(lines[i], key, indent):
            j = i + 1
            while j < end and (not _is_content(lines[j]) or _indent(lines[j]) > indent):
                if _is_content(lines[j]) and _indent(lines[j]) > indent:
                    j += 1
                    continue
                break
            # keep a trailing comment on simple one-line values
            m = re.match(r"^\s*" + re.escape(key) + r":\s*(\[[^\]]*\]|\"[^\"]*\"|[^#\"\[]*?)(\s+#.*)?$",
                         lines[i].rstrip("\n"))
            comment = m.group(2) if (m and m.group(2) and j == i + 1) else ""
            lines[i:j] = [" " * indent + "%s: %s%s\n" % (key, value_text, comment)]
            return end - (j - i) + 1
        i += 1
    lines.insert(start, " " * indent + "%s: %s\n" % (key, value_text))
    return end + 1


def _block_end(lines, i, indent):
    j = i + 1
    while j < len(lines):
        if _is_content(lines[j]) and _indent(lines[j]) <= indent:
            break
        j += 1
    # don't swallow trailing blank/comment lines that belong to the next block
    while j > i + 1 and not _is_content(lines[j - 1]):
        j -= 1
    return j


def edit_config_yml(text, top=None, kit=None, backend=None):
    """Edit Pages' _config.yml: top-level keys, the `kit:` block's direct children and
    `kit: backend:` children. Everything else (comments, other keys) is preserved."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for key, value in (top or {}).items():
        idx = next((i for i, l in enumerate(lines) if _key_match(l, key, 0)), None)
        if idx is None:
            lines.append("%s: %s\n" % (key, yaml_value(value)))
        else:
            _set_child(lines, idx, idx + 1, key, 0, yaml_value(value))
    if kit or backend:
        k = next((i for i, l in enumerate(lines) if _key_match(l, "kit", 0)), None)
        if k is None:
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.append("kit:\n")
            k = len(lines) - 1
        end = _block_end(lines, k, 0)
        # insert new keys right after 'kit:' (in reverse so order is kept)
        for key, value in reversed(list((kit or {}).items())):
            end = _set_child(lines, k + 1, end, key, 2, yaml_value(value))
        if backend:
            b = next((i for i in range(k + 1, end) if _key_match(lines[i], "backend", 2)), None)
            if b is None:
                lines.insert(end, "  backend:\n")
                b = end
                end += 1
            bend = _block_end(lines, b, 2)
            for key, value in reversed(list(backend.items())):
                bend = _set_child(lines, b + 1, bend, key, 4, yaml_value(value))
    return "".join(lines)


def pages_config_settings(answers, overrides, target, owner="", repo=""):
    school = answers["school_name"]
    top = {"title": school, "description": "%s computer science pages" % school, "owner_name": school}
    if target == "github" and owner:
        top["github_username"] = owner
        top["github_repo"] = repo
    kit = {"school_name": school, "name": answers["kit"], "features": list(overrides)}
    hp = host_ports(answers)
    backend = {"flask_local": "http://localhost:%d" % hp["flask"],
               "spring_local": "http://localhost:%d" % hp["spring"]}
    if not is_local(answers):
        backend = {"flask": "https://flask.%s" % answers["domain"],
                   "spring": "https://spring.%s" % answers["domain"], **backend}
    return {"top": top, "kit": kit, "backend": backend}


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def _as_bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(v)


def normalize_answers(raw, defaults=None, env=None):
    env = os.environ if env is None else env
    a = dict(DEFAULT_ANSWERS)
    a.update(defaults or {})
    for k, v in (raw or {}).items():
        if k in DEFAULT_ANSWERS:
            a[k] = v
    for field, var in SECRET_ENV.items():
        if not a.get(field) and env.get(var):
            a[field] = env[var]
    for k in ("install_backend", "install_website", "website_preview", "website_publish"):
        a[k] = _as_bool(a[k])
    for k in ("school_name", "admin_email", "install_dir", "domain", "github_owner",
              "github_repo", "kit", "mode", "action"):
        a[k] = str(a.get(k) or "").strip()
    a["domain"] = a["domain"].lower().rstrip(".")
    a["domain"] = re.sub(r"^https?://", "", a["domain"]).split("/")[0]
    a["features"] = parse_feature_spec(a.get("features"))
    try:
        a["preview_port"] = int(a.get("preview_port") or DEFAULT_PREVIEW_PORT)
    except (TypeError, ValueError):
        a["preview_port"] = -1
    try:
        a["port_offset"] = int(a.get("port_offset") or 0)
    except (TypeError, ValueError):
        a["port_offset"] = -1
    if not a["install_website"]:
        a["website_preview"] = a["website_publish"] = False
    raw_settings = a.get("settings") if isinstance(a.get("settings"), dict) else {}
    a["settings"] = {str(k).strip(): str(v if v is not None else "").strip() for k, v in raw_settings.items()
                     if str(k).strip()}
    return a


def apply_setting_env(a, catalog, env=None):
    """Fill empty feature settings from OCS_SETTING_<KEY> environment variables."""
    env = os.environ if env is None else env
    for sid, st in all_catalog_settings(catalog).items():
        if not setting_value(a["settings"], dict(st, id=sid)):
            v = env.get(setting_env_name(st["key"]))
            if v:
                a["settings"][sid] = v.strip()
    return a


def validate_answers(a, catalog):
    """Return {field: message}. Empty dict = valid."""
    err = {}
    if a.get("action") not in ("install", "change-features"):
        err["action"] = "Unknown action."
    if a.get("kit") not in catalog["kits"]:
        err["kit"] = "Choose a kit: " + ", ".join(catalog["kits"])
    else:
        try:
            resolve_features(catalog, a["kit"], a.get("features"))
        except CatalogError as exc:
            err["features"] = str(exc)
    if not a.get("install_dir"):
        err["install_dir"] = "Choose a folder to install into."
    off = a.get("port_offset", 0)
    if not isinstance(off, int) or not 0 <= off <= 50000:
        err["port_offset"] = "Port offset must be a number between 0 and 50000."
    if a.get("action") == "change-features":
        return err
    if not (a.get("install_backend") or a.get("install_website")):
        err["install_backend"] = "Choose the backend server, the website, or both."
    if a.get("mode") not in ("local", "production"):
        err["mode"] = "Choose 'local' or 'production'."
    if not a.get("school_name"):
        err["school_name"] = "Enter your school's name."
    elif UNSAFE_TEXT.search(a["school_name"]):
        err["school_name"] = "Please don't use $, quotes, backslashes or backticks."
    if not EMAIL_RE.match(a.get("admin_email", "")) or UNSAFE_TEXT.search(a.get("admin_email", "")):
        err["admin_email"] = "Enter a valid email address."
    if a.get("mode") == "production":
        if not DOMAIN_RE.match(a.get("domain", "")):
            err["domain"] = "Enter your domain, like myschool.org (no http://)."
    for key, value in (a.get("settings") or {}).items():
        is_pages = key.startswith("pages:") or key.startswith("kit.")
        if value and (UNSAFE_TEXT.search(value) or (" " in value and not is_pages)):
            err["setting:" + key] = "Please don't use spaces, $, quotes or backslashes here."
    for field, _key, _label in PASSWORD_FIELDS:
        pw = a.get(field) or ""
        if pw:
            problems = validate_password(pw)
            if problems:
                err[field] = "Needs " + ", ".join(problems) + "."
    if a.get("install_website"):
        if not (a.get("website_preview") or a.get("website_publish")):
            err["website_preview"] = "Choose GitHub Pages, a local preview, or both."
        if a.get("website_publish"):
            if not GH_REPO_RE.match(a.get("github_repo", "")):
                err["github_repo"] = "Repository name: letters, digits, '.', '-', '_'."
            if a.get("github_owner") and not GH_NAME_RE.match(a["github_owner"]):
                err["github_owner"] = "Not a valid GitHub user or organization name."
        if a.get("website_preview"):
            p = a.get("preview_port")
            if not isinstance(p, int) or not 1024 <= p <= 65535:
                err["preview_port"] = "Port must be a number between 1024 and 65535."
            elif p in host_ports(a).values():
                err["preview_port"] = "That port is used by the backends."
    return err


def website_warnings(a):
    warns = []
    if a.get("website_publish") and is_local(a):
        warns.append("A site published on GitHub Pages can only talk to production backends (https). "
                     "With 'Try it on this computer', only the local preview can log in to your "
                     "localhost backends.")
    if a.get("install_website") and not a.get("install_backend") and is_local(a):
        warns.append("The website needs the backends running on this computer (localhost:8587 and "
                     ":8585) to log in.")
    return warns


def saved_answers(a, catalog=None):
    """Answers safe to keep in ocs-kit-installer.json: no passwords, no secret feature settings."""
    out = {k: v for k, v in a.items() if k in DEFAULT_ANSWERS and k not in SECRET_FIELDS and k != "action"}
    secret = set()
    if catalog:
        for sid, st in all_catalog_settings(catalog).items():
            if st.get("secret"):
                secret |= {sid, st["key"]}
    out["settings"] = {k: v for k, v in (a.get("settings") or {}).items()
                       if v and catalog is not None and k not in secret}
    return out


def load_saved_answers(install_dir):
    p = Path(os.path.expanduser(install_dir)) / ANSWERS_FILE
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if k in DEFAULT_ANSWERS and k not in SECRET_FIELDS}
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def _probe(cmd, timeout=20):
    if not shutil.which(cmd[0]):
        return None, ""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                           universal_newlines=True, errors="replace")
        return p.returncode == 0, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def os_name():
    s = platform.system()
    if s == "Darwin":
        return "macos"
    if s == "Windows":
        return "windows"
    return "linux"


FIX_HINTS = {
    "git": {"macos": "Run: xcode-select --install   (or brew install git)",
            "linux": "Run: sudo apt install git   (Fedora: sudo dnf install git)",
            "windows": "Install Git for Windows, then reopen this window.",
            "link": "https://git-scm.com/downloads"},
    "docker": {"macos": "Install Docker Desktop for Mac and start it.",
               "linux": "Run: curl -fsSL https://get.docker.com | sh   then: sudo usermod -aG docker $USER "
                        "(log out and back in)",
               "windows": "Install Docker Desktop (WSL 2 backend) and start it.",
               "link": "https://docs.docker.com/get-docker/"},
    "compose": {"macos": "Docker Desktop includes Compose: update Docker Desktop.",
                "linux": "Run: sudo apt install docker-compose-plugin",
                "windows": "Docker Desktop includes Compose: update Docker Desktop.",
                "link": "https://docs.docker.com/compose/install/"},
    "daemon": {"macos": "Open Docker Desktop and wait until it says 'running'.",
               "linux": "Run: sudo systemctl start docker   (and check you are in the 'docker' group)",
               "windows": "Open Docker Desktop and wait until it says 'running'.",
               "link": "https://docs.docker.com/config/daemon/start/"},
    "gh": {"macos": "Run: brew install gh", "linux": "See the install page for apt/dnf instructions.",
           "windows": "Run: winget install --id GitHub.cli", "link": "https://cli.github.com/"},
    "ghauth": {"macos": "Run: gh auth login", "linux": "Run: gh auth login", "windows": "Run: gh auth login",
               "link": "https://cli.github.com/manual/gh_auth_login"},
    "preview": {"macos": "Install Docker Desktop (recommended), or Ruby + Bundler + make + Python 3.",
                "linux": "Install Docker (recommended), or: sudo apt install ruby-full ruby-bundler make python3-venv",
                "windows": "Install Docker Desktop (recommended).",
                "link": "https://docs.docker.com/get-docker/"},
    "port": {"macos": "Stop the program using this port (an older install? run 'docker compose stop' in it).",
             "linux": "Stop the program using this port (an older install? run 'docker compose stop' in it).",
             "windows": "Stop the program using this port.", "link": ""},
}


def port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def check_prerequisites(a, probe=_probe, port_check=port_in_use):
    """List of {id, name, status: ok|fail|warn, detail, fix, link}."""
    osn = os_name()
    items = []

    def add(iid, name, status, detail, hint_key=None):
        h = FIX_HINTS.get(hint_key or iid, {})
        items.append({"id": iid, "name": name, "status": status, "detail": detail,
                      "fix": "" if status == "ok" else h.get(osn, ""),
                      "link": "" if status == "ok" else h.get("link", "")})

    need_git = a.get("install_backend") or a.get("website_preview") or a.get("website_publish")
    need_docker = a.get("install_backend")
    if need_git:
        ok, out = probe(["git", "--version"])
        add("git", "Git", "ok" if ok else "fail", out if ok else "git was not found")
    docker_ok = False
    if need_docker or a.get("website_preview"):
        ok, out = probe(["docker", "--version"])
        status = "ok" if ok else ("fail" if need_docker else "warn")
        add("docker", "Docker", status, out if ok else "docker was not found")
        if ok:
            cok, cout = probe(["docker", "compose", "version"])
            add("compose", "Docker Compose", "ok" if cok else ("fail" if need_docker else "warn"),
                cout if cok else "'docker compose' is not available")
            dok, dout = probe(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30)
            add("daemon", "Docker is running", "ok" if dok else ("fail" if need_docker else "warn"),
                "engine " + dout if dok else "cannot talk to the Docker engine: " + (dout.splitlines() or [""])[-1])
            docker_ok = bool(cok and dok)
    if need_docker and a.get("action") != "change-features":
        hp = host_ports(a)
        for port, what in ((hp["flask"], "Flask"), (hp["socket"], "Flask socket"), (hp["spring"], "Spring"),
                           (hp["chat"], "Spring chat")):
            if port_check(port):
                add("port-%d" % port, "Port %d (%s)" % (port, what), "warn",
                    "already in use: fine if it is an earlier OCS Kit install, otherwise stop that program",
                    "port")
    if a.get("website_preview"):
        if docker_ok:
            add("preview", "Website preview", "ok", "will run in Docker (no Ruby needed)")
        else:
            tools = {t: bool(shutil.which(t)) for t in ("ruby", "bundle", "make", "python3")}
            missing = [t for t, v in tools.items() if not v]
            if missing:
                add("preview", "Website preview", "fail", "needs Docker, or local " + ", ".join(missing))
            else:
                add("preview", "Website preview", "ok", "will use your local Ruby/Bundler/make/Python")
        if a.get("action") != "change-features" and port_check(int(a.get("preview_port") or 0)):
            add("port-preview", "Port %s (website preview)" % a.get("preview_port"), "warn",
                "already in use: choose another preview port or stop that program", "port")
    if a.get("website_publish"):
        ok, out = probe(["gh", "--version"])
        add("gh", "GitHub CLI (gh)", "ok" if ok else "fail", out.splitlines()[0] if ok and out else "gh was not found")
        if ok:
            aok, aout = probe(["gh", "auth", "status"])
            add("ghauth", "Signed in to GitHub", "ok" if aok else "fail",
                "signed in" if aok else "not signed in: run 'gh auth login'")
    return items


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

class InstallError(Exception):
    pass


PREVIEW_DOCKERFILE = """FROM ruby:3.2-bookworm
RUN apt-get update \\
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip make lsof procps \\
 && rm -rf /var/lib/apt/lists/*
WORKDIR /site
"""

PREVIEW_SCRIPT = r"""set -e
cd /site
if [ ! -x venv/bin/python3 ] || ! venv/bin/python3 -c 'import sys' 2>/dev/null; then
  rm -rf venv && python3 -m venv venv
fi
if [ ! -f venv/.ocskit-reqs ] || [ requirements.txt -nt venv/.ocskit-reqs ]; then
  echo '[preview] installing Python packages (first run takes a few minutes)'
  venv/bin/pip install --disable-pip-version-check -q -r requirements.txt
  touch venv/.ocskit-reqs
fi
export PATH=/site/venv/bin:$PATH
echo '[preview] installing Ruby gems'
bundle install --quiet
echo '[preview] building and serving the site'
make HOST=0.0.0.0 PORT="$PORT"
echo "[preview] ready on port $PORT"
exec tail -n +1 -F "/tmp/jekyll$PORT.log"
"""


class Installer:
    """Runs the install (or change-features) plan. Thread-safe progress for the GUI."""

    def __init__(self, answers, catalog, repo_base=DEFAULT_REPO_BASE, ref=KIT_VERSION, dry_run=False,
                 echo=False, script_dir=None):
        self.a = answers
        self.catalog = catalog
        self.repo_base = repo_base
        self.ref = ref
        self.dry = dry_run
        self.echo = echo
        self.overrides = normalize_overrides(catalog, answers["kit"], answers.get("features"))
        self.roles = kit_roles_map(catalog, answers["kit"])
        self.ports = host_ports(answers)
        self.root = Path(os.path.expanduser(answers["install_dir"])).resolve()
        self.dirs = {"flask": self.root / "flask", "spring": self.root / "spring",
                     "pages": self.root / "pages", "site": self.root / "github-site",
                     "nginx": self.root / "nginx", "preview": self.root / "preview"}
        self.lock = threading.Lock()
        self.lines = []
        self.steps = []
        self.done = False
        self.ok = None
        self.error = None
        self.summary = None
        self.passwords = {}       # kind -> {ENV: value}
        self.results = {"urls": [], "warnings": list(website_warnings(answers)), "notes": []}
        self.gh_owner = answers.get("github_owner") or ""
        self.preview_method = None
        self._logfh = None

    # -- logging ----------------------------------------------------------
    def log(self, msg):
        for line in str(msg).splitlines() or [""]:
            with self.lock:
                self.lines.append(line)
                if len(self.lines) > 20000:
                    del self.lines[:5000]
            if self._logfh:
                try:
                    self._logfh.write(line + "\n")
                    self._logfh.flush()
                except OSError:
                    pass
            if self.echo:
                try:
                    print(line, flush=True)
                except UnicodeEncodeError:
                    print(line.encode("ascii", "replace").decode("ascii"), flush=True)

    def progress(self, since=0):
        with self.lock:
            return {"lines": self.lines[since:], "next": len(self.lines),
                    "steps": [dict(s) for s in self.steps], "done": self.done, "ok": self.ok,
                    "error": self.error, "summary": self.summary, "dry_run": self.dry}

    # -- primitives -------------------------------------------------------
    def sh(self, cmd, cwd=None, check=True, env=None, quiet=False, timeout=None, dry_output=""):
        shown = " ".join(shlex.quote(str(c)) for c in cmd)
        where = " (in %s)" % cwd if cwd else ""
        if self.dry:
            self.log("[dry-run] $ %s%s" % (shown, where))
            return 0, dry_output
        if not quiet:
            self.log("$ %s%s" % (shown, where))
        full_env = dict(os.environ)
        full_env.update(env or {})
        try:
            proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=full_env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    universal_newlines=True, errors="replace", bufsize=1)
        except OSError as exc:
            if check:
                raise InstallError("could not run %s: %s" % (cmd[0], exc))
            return 127, str(exc)
        out = []
        start = time.time()
        for line in proc.stdout:
            line = line.rstrip("\n")
            out.append(line)
            if not quiet:
                self.log("  " + line)
            if timeout and time.time() - start > timeout:
                proc.kill()
                break
        rc = proc.wait()
        text = "\n".join(out)
        if check and rc != 0:
            tail = "\n".join(out[-15:])
            raise InstallError("command failed (exit %d): %s\n%s" % (rc, shown, tail))
        return rc, text

    def write(self, path, content, mode=None, describe=None):
        path = Path(path)
        if self.dry:
            self.log("[dry-run] write %s (%d bytes)%s" % (path, len(content.encode("utf-8")),
                                                           (": " + describe) if describe else ""))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
        self.log("wrote %s" % path)

    def read(self, path, default=None):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            if default is not None:
                return default
            raise

    # -- repos ------------------------------------------------------------
    def repo_url(self, key):
        name = REPOS[key]["name"]
        if os.path.isdir(self.repo_base):
            return str(Path(self.repo_base).resolve() / name)
        return self.repo_base.rstrip("/") + "/" + name

    def ref_for(self, key):
        if self.ref in ("default", "main", "master", "HEAD", ""):
            return REPOS[key]["default_branch"]
        return self.ref

    def upstream_slug(self):
        m = re.match(r"^(?:https://github\.com/|git@github\.com:)([^/]+)/?$", self.repo_base.rstrip("/"))
        return "%s/%s" % (m.group(1) if m else UPSTREAM_OWNER, REPOS["pages"]["name"])

    def checkout(self, key, dest, managed_files=()):
        dest = Path(dest)
        ref = self.ref_for(key)
        if (dest / ".git").exists() or (self.dry and dest.exists()):
            self.log("Updating %s to %s" % (dest, ref))
            self.sh(["git", "fetch", "--tags", "--force", "origin"], cwd=dest)
            for f in managed_files:
                self.sh(["git", "checkout", "--", f], cwd=dest, check=False, quiet=True)
        else:
            if dest.exists() and any(dest.iterdir()):
                raise InstallError("%s exists and is not a git checkout; move it away or pick another "
                                   "install folder" % dest)
            self.sh(["git", "clone", self.repo_url(key), str(dest)])
        rc, _ = self.sh(["git", "rev-parse", "--verify", "-q", "refs/remotes/origin/%s" % ref], cwd=dest,
                        check=False, quiet=True)
        try:
            if rc == 0:
                self.sh(["git", "checkout", "-B", ref, "origin/%s" % ref], cwd=dest)
            else:
                self.sh(["git", "-c", "advice.detachedHead=false", "checkout", "--detach", ref], cwd=dest)
        except InstallError as exc:
            raise InstallError("%s\nCould not check out %r in %s. Try --ref main (the latest code) or "
                               "commit/stash local changes there." % (exc, ref, dest))

    # -- plan -------------------------------------------------------------
    def plan(self):
        a = self.a
        steps = []
        if a["action"] == "change-features":
            steps.append(("features", "Update kit and features", self.step_change_features))
            steps.append(("save", "Save settings", self.step_save))
            return steps
        if a["install_backend"]:
            steps += [("download-backend", "Download backend code", self.step_download_backend),
                      ("configure-backend", "Configure backends (.env)", self.step_configure_backend),
                      ("start-flask", "Build and start Flask (Docker)", lambda: self.step_start("flask")),
                      ("start-spring", "Build and start Spring (Docker)", lambda: self.step_start("spring")),
                      ("wait-backend", "Wait for backends to answer", self.step_wait_backend),
                      ("verify-backend", "Check admin login and kit", self.step_verify_backend)]
        if a["install_website"] and a["website_preview"]:
            steps += [("download-website", "Download website code", self.step_download_website),
                      ("configure-website", "Configure website (_config.yml)", self.step_configure_website),
                      ("preview", "Start local website preview", self.step_preview)]
        if a["install_website"] and a["website_publish"]:
            steps.append(("publish", "Publish website to GitHub Pages", self.step_publish))
        if a["install_backend"] and not is_local(a):
            steps.append(("nginx", "Write nginx configs", self.step_nginx))
        steps.append(("save", "Save credentials and settings", self.step_save))
        return steps

    def run(self):
        plan = self.plan()
        with self.lock:
            self.steps = [{"id": i, "label": l, "status": "pending"} for i, l, _ in plan]
        if not self.dry:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                self._logfh = open(self.root / LOG_FILE, "a", encoding="utf-8")
            except OSError as exc:
                self.log("Cannot create %s: %s" % (self.root, exc))
        self.log("OCS-in-a-Kit installer %s - %s%s" % (KIT_VERSION, time.strftime("%Y-%m-%d %H:%M:%S"),
                                                      " (DRY RUN: nothing is executed)" if self.dry else ""))
        self.log("Kit: %s" % describe_selection(self.catalog, self.a["kit"], self.overrides))
        ok = True
        for idx, (sid, label, fn) in enumerate(plan):
            with self.lock:
                self.steps[idx]["status"] = "running"
            self.log("")
            self.log("== %s ==" % label)
            try:
                fn()
                status = "done"
            except Exception as exc:  # noqa: BLE001 - report every failure to the user
                ok = False
                status = "failed"
                msg = str(exc) if isinstance(exc, InstallError) else traceback.format_exc()
                self.log("ERROR: " + msg)
                with self.lock:
                    self.error = "%s: %s" % (label, msg)
            with self.lock:
                self.steps[idx]["status"] = status
            if not ok:
                with self.lock:
                    for s in self.steps[idx + 1:]:
                        s["status"] = "skipped"
                break
        summary = self.build_summary(ok)
        with self.lock:
            self.summary = summary
            self.ok = ok
            self.done = True
        self.log("")
        self.log("Finished: %s" % ("success" if ok else "FAILED (see the error above)"))
        if self._logfh:
            self._logfh.close()
            self._logfh = None
        return ok

    # -- backend steps ----------------------------------------------------
    def step_download_backend(self):
        for key in ("flask", "spring"):
            self.checkout(key, self.dirs[key])

    def _existing_envs(self):
        envs = {}
        for kind in ("flask", "spring"):
            envs[kind] = parse_env(self.read(self.dirs[kind] / ".env", ""))
        return envs

    def prepare_backend_dirs(self):
        """Data folders the containers bind-mount, plus the port-offset compose override."""
        offset = int(self.a.get("port_offset") or 0)
        for kind, sub in (("flask", "instance/volumes"), ("spring", "volumes")):
            d = self.dirs[kind]
            data = d / sub
            if self.dry:
                self.log("[dry-run] mkdir -p %s" % data)
            else:
                # Create them as this user: if Docker creates a missing bind-mount source it is
                # owned by root and the non-root app user in the container cannot write to it.
                data.mkdir(parents=True, exist_ok=True)
                if hasattr(os, "getuid") and os.getuid() != 1000 and sys.platform.startswith("linux"):
                    # The Flask image runs as uid 1000; let it write the data folder.
                    top = d / sub.split("/")[0]
                    for root, dirs, _files in os.walk(top):
                        for x in [root] + [os.path.join(root, n) for n in dirs]:
                            try:
                                os.chmod(x, 0o777)
                            except OSError:
                                pass
            override = d / "docker-compose.override.yml"
            if offset:
                self.write(override, render_compose_override(kind, offset),
                           describe="host ports +%d, separate image names" % offset)
            elif override.exists() and "OCS-in-a-Kit installer" in self.read(override, ""):
                if self.dry:
                    self.log("[dry-run] remove %s" % override)
                else:
                    override.unlink()
                    self.log("removed %s (back to default ports)" % override)

    def step_configure_backend(self):
        self.prepare_backend_dirs()
        envs = self._existing_envs()
        shared = {}
        for key in ["RESET_TOKEN_SECRET"] + [k for _, k, _ in PASSWORD_FIELDS]:
            for kind in ("flask", "spring"):
                if envs[kind].get(key):
                    shared[key] = envs[kind][key]
                    break
        for kind in ("flask", "spring"):
            d = self.dirs[kind]
            env_path = d / ".env"
            existed = env_path.exists()
            base = self.read(env_path) if existed else self.read(d / ".env.example", "")
            values = build_env_values(kind, self.a, self.overrides, envs[kind], shared, self.roles)
            extra = self.feature_settings(kind, exclude=values)
            values.update(extra)
            kept = [k for k in SECRET_KEYS[kind] if envs[kind].get(k)]
            if kept:
                self.log("%s: keeping existing %s" % (kind, ", ".join(kept)))
            for field, key, _ in PASSWORD_FIELDS:
                if not self.a.get(field) and envs[kind].get(key):
                    self.log("%s: keeping existing %s" % (kind, key))
            text = apply_env(kind, base, values, is_local(self.a))
            self.write(env_path, text, 0o600,
                       describe="%s .env with %s" % ("updated" if existed else "new", ", ".join(sorted(values))))
            self.passwords[kind] = {k: values[k] for _, k, _ in PASSWORD_FIELDS}
            self.log("%s: KIT=%s FEATURES=%s" % (kind, values["KIT"], values["FEATURES"] or "(preset)"))
            if extra:
                self.log("%s: feature settings written: %s" % (kind, ", ".join(sorted(extra))))

    def feature_settings(self, service, exclude=()):
        """Typed feature settings for a service, never overriding values the installer manages."""
        vals = settings_for_service(self.catalog, self.a["kit"], self.overrides, self.a.get("settings") or {},
                                    service)
        skip = set(exclude) | (managed_keys(service) if service != "pages" else set())
        return {k: v for k, v in vals.items() if k not in skip}

    def compose(self, kind, *args, check=True, quiet=False):
        return self.sh(["docker", "compose"] + list(args), cwd=self.dirs[kind], check=check, quiet=quiet,
                       env={"COMPOSE_PROJECT_NAME": COMPOSE_PROJECTS[kind]})

    def step_start(self, kind):
        self.log("Building can take several minutes the first time.")
        self.compose(kind, "up", "-d", "--build")

    def wait_http(self, url, timeout, what, on_timeout=None, ok_codes=(200,)):
        if self.dry:
            self.log("[dry-run] wait for %s" % url)
            return
        self.log("Waiting for %s (up to %d min)..." % (url, timeout // 60))
        deadline = time.time() + timeout
        last_note = time.time()
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    if r.status in ok_codes:
                        self.log("%s is up (%s)" % (what, url))
                        return
            except urllib.error.HTTPError as e:
                if e.code in ok_codes:
                    self.log("%s is up (%s)" % (what, url))
                    return
            except Exception:  # noqa: BLE001 - not up yet
                pass
            if time.time() - last_note > 30:
                last_note = time.time()
                self.log("  still waiting for %s..." % what)
                if on_timeout:
                    on_timeout(tail=3)
            time.sleep(3)
        extra = on_timeout(tail=50) if on_timeout else ""
        raise InstallError("%s did not answer at %s within %d minutes.%s" % (what, url, timeout // 60,
                                                                           ("\nLast log lines:\n" + extra) if extra else ""))

    def _compose_logs(self, kind):
        def logs(tail=50):
            _, out = self.compose(kind, "logs", "--tail", str(tail), "web", check=False, quiet=True)
            if tail <= 5:
                for line in out.splitlines()[-tail:]:
                    self.log("  | " + line)
            return out
        return logs

    def step_wait_backend(self):
        self.wait_http("http://localhost:%d/login" % self.ports["flask"], 600, "Flask", self._compose_logs("flask"))
        self.wait_http("http://localhost:%d/login" % self.ports["spring"], 900, "Spring", self._compose_logs("spring"))

    def http_json(self, method, url, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            return 0, str(exc)

    def step_verify_backend(self):
        checks = [("flask", "http://localhost:%d/api/authenticate" % self.ports["flask"], {"X-Origin": "client"}),
                  ("spring", "http://localhost:%d/authenticate" % self.ports["spring"], {})]
        for kind, url, headers in checks:
            pw = (self.passwords.get(kind) or {}).get("ADMIN_PASSWORD") or ""
            uid = self.roles.get("admin", DEFAULT_ROLES["admin"])["seed_uid"]
            if self.dry:
                self.log("[dry-run] POST %s as %s" % (url, uid))
                continue
            code, body = self.http_json("POST", url, {"uid": uid, "password": pw}, headers)
            if code == 200:
                self.log("%s: %s login OK" % (kind, uid))
            else:
                msg = ("%s: admin login returned HTTP %s. If this backend already had a database, its "
                       "accounts keep their old passwords (seed passwords only apply to a new database)."
                       % (kind, code))
                self.log("WARNING: " + msg)
                self.results["warnings"].append(msg)
        for kind, port in (("flask", self.ports["flask"]), ("spring", self.ports["spring"])):
            if self.dry:
                self.log("[dry-run] GET http://localhost:%d/api/kit" % port)
                continue
            code, body = self.http_json("GET", "http://localhost:%d/api/kit" % port)
            if code == 200:
                self.log("%s /api/kit: %s" % (kind, body.strip()[:400]))
            else:
                self.log("%s /api/kit: not available in this version (HTTP %s)" % (kind, code))

    # -- website steps ----------------------------------------------------
    def step_download_website(self):
        self.checkout("pages", self.dirs["pages"], managed_files=("_config.yml",))

    def configure_pages_dir(self, d, target, owner="", repo=""):
        cfg = d / "_config.yml"
        s = pages_config_settings(self.a, self.overrides, target, owner, repo)
        text = self.read(cfg, "" if self.dry else None)
        if self.dry and not text:
            self.log("[dry-run] edit %s: kit=%s features=%s" % (cfg, self.a["kit"], self.overrides))
            return
        edits, unsupported = pages_setting_edits(self.feature_settings("pages"))
        for key in unsupported:
            self.results["warnings"].append("Website setting %s must be set by hand in _config.yml." % key)
        text = edit_config_yml(text, s["top"], s["kit"], s["backend"])
        if any(edits.values()):
            text = edit_config_yml(text, edits["top"], edits["kit"], edits["backend"])
            self.log("website settings written: %s" % ", ".join(
                sorted(list(edits["top"]) + ["kit." + k for k in edits["kit"]] + ["kit.backend." + k for k in edits["backend"]])))
        self.write(cfg, text, describe="kit %s, features %s" % (self.a["kit"], self.overrides))

    def step_configure_website(self):
        self.configure_pages_dir(self.dirs["pages"], "preview")

    def _docker_usable(self):
        if self.dry:
            return bool(shutil.which("docker"))
        ok, _ = _probe(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30)
        return bool(ok)

    def step_preview(self):
        port = int(self.a["preview_port"])
        url = "http://localhost:%d/" % port
        if self._docker_usable():
            self.preview_method = "docker"
            self.write(self.dirs["preview"] / "Dockerfile", PREVIEW_DOCKERFILE)
            self.sh(["docker", "build", "-t", PREVIEW_IMAGE, str(self.dirs["preview"])])
            self.sh(["docker", "rm", "-f", PREVIEW_CONTAINER], check=False, quiet=True)
            cmd = ["docker", "run", "-d", "--name", PREVIEW_CONTAINER, "--restart", "unless-stopped",
                   "-p", "%d:%d" % (port, port), "-v", "%s:/site" % self.dirs["pages"], "-w", "/site",
                   "-e", "HOME=/tmp", "-e", "BUNDLE_PATH=/site/vendor/bundle", "-e", "PORT=%d" % port]
            if hasattr(os, "getuid"):
                cmd += ["--user", "%d:%d" % (os.getuid(), os.getgid())]
            cmd += [PREVIEW_IMAGE, "bash", "-c", PREVIEW_SCRIPT]
            self.sh(cmd)

            def logs(tail=50):
                _, out = self.sh(["docker", "logs", "--tail", str(tail), PREVIEW_CONTAINER], check=False,
                                 quiet=True)
                if tail <= 5:
                    for line in out.splitlines()[-tail:]:
                        self.log("  | " + line)
                return out
            self.log("The first build installs Python packages and Ruby gems; it can take 10+ minutes.")
            self.wait_http(url, 2400, "Website preview", logs)
        else:
            self.preview_method = "local"
            d = self.dirs["pages"]
            py = d / "venv" / ("Scripts" if os.name == "nt" else "bin") / "python3"
            self.log("Docker is not available: using the local Ruby/Bundler/make toolchain.")
            if not py.exists():
                self.sh([sys.executable if not shutil.which("python3") else "python3", "-m", "venv", "venv"], cwd=d)
            self.sh([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                     "requirements.txt"], cwd=d)
            self.sh(["bundle", "config", "set", "--local", "path", "vendor/bundle"], cwd=d)
            self.sh(["bundle", "install"], cwd=d)
            env_path = str(py.parent) + os.pathsep + os.environ.get("PATH", "")
            self.log("$ make HOST=localhost PORT=%d (in %s; output in %s)" % (port, d, self.root / "preview.log"))
            if not self.dry:
                with open(self.root / "preview.log", "w") as logfh:
                    proc = subprocess.Popen(["make", "HOST=localhost", "PORT=%d" % port], cwd=str(d),
                                            env=dict(os.environ, PATH=env_path), stdout=logfh,
                                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                            start_new_session=(os.name != "nt"))
                    rc = proc.wait()
                if rc != 0:
                    raise InstallError("make failed; see %s" % (self.root / "preview.log"))
            self.wait_http(url, 1200, "Website preview")
        self.results["urls"].append(("Website (local preview)", url))

    def _gh_login(self):
        _, out = self.sh(["gh", "api", "user", "-q", ".login"], quiet=True, dry_output="<your-github-login>")
        return out.strip().splitlines()[-1] if out.strip() else ""

    def step_publish(self):
        a = self.a
        login = self._gh_login()
        owner = a.get("github_owner") or login
        if not owner:
            raise InstallError("could not find your GitHub login; run 'gh auth login'")
        self.gh_owner = owner
        repo = a["github_repo"]
        full = "%s/%s" % (owner, repo)
        upstream = self.upstream_slug()
        rc, _ = self.sh(["gh", "repo", "view", full, "--json", "name"], check=False, quiet=True)
        created = False
        if self.dry or rc != 0:
            cmd = ["gh", "repo", "fork", upstream, "--clone=false", "--default-branch-only", "--fork-name", repo]
            if owner.lower() != login.lower():
                cmd += ["--org", owner]
            if self.dry:
                self.log("(only if %s does not exist yet)" % full)
            self.sh(cmd)
            created = True
            if not self.dry:
                for _ in range(30):
                    if self.sh(["gh", "repo", "view", full, "--json", "name"], check=False, quiet=True)[0] == 0:
                        break
                    time.sleep(2)
        else:
            self.log("%s already exists: updating it" % full)
        self.sh(["gh", "auth", "setup-git"], check=False)
        site = self.dirs["site"]
        if (site / ".git").exists():
            self.sh(["git", "fetch", "origin"], cwd=site)
            self.sh(["git", "checkout", "-B", "main", "origin/main"], cwd=site)
        else:
            self.sh(["gh", "repo", "clone", full, str(site)])
        force = False
        if created:
            # Start the new site from the kit release, not whatever upstream's main is today.
            self.sh(["git", "fetch", self.repo_url("pages") if not os.path.isdir(self.repo_base)
                     else self.repo_url("pages"), self.ref_for("pages")], cwd=site)
            self.sh(["git", "checkout", "-B", "main", "FETCH_HEAD"], cwd=site)
            force = True
        self.configure_pages_dir(site, "github", owner, repo)
        files = ["_config.yml"]
        if not is_local(a):
            self.write(site / "CNAME", "pages.%s\n" % a["domain"])
            files.append("CNAME")
        self.git_commit_push(site, files, "Configure OCS-in-a-Kit site (%s)" % a["kit"], force)
        self.sh(["gh", "api", "-X", "PUT", "repos/%s/actions/permissions" % full, "-F", "enabled=true",
                 "-f", "allowed_actions=all"])
        self.sh(["gh", "workflow", "enable", PAGES_WORKFLOW, "-R", full], check=False)
        rc, _ = self.sh(["gh", "api", "repos/%s/pages" % full], check=False, quiet=True)
        if rc == 0 and not self.dry:
            self.sh(["gh", "api", "-X", "PUT", "repos/%s/pages" % full, "-f", "build_type=workflow"], check=False)
        else:
            self.sh(["gh", "api", "-X", "POST", "repos/%s/pages" % full, "-f", "build_type=workflow"])
        if not is_local(a):
            rc, _ = self.sh(["gh", "api", "-X", "PUT", "repos/%s/pages" % full, "-f", "cname=pages.%s" % a["domain"]],
                            check=False)
            if rc != 0:
                self.results["warnings"].append("Could not set the custom domain on GitHub Pages yet; set "
                                                "pages.%s under Settings > Pages once DNS is ready." % a["domain"])
        self.sh(["gh", "workflow", "run", PAGES_WORKFLOW, "-R", full, "--ref", "main"], check=False)
        url = ("https://pages.%s/" % a["domain"]) if not is_local(a) else github_pages_url(owner, repo)
        self.results["urls"].append(("Website (GitHub Pages; first build takes a few minutes)", url))
        self.results["urls"].append(("Website repository", "https://github.com/%s" % full))
        self.results["urls"].append(("Website build status", "https://github.com/%s/actions" % full))

    def git_commit_push(self, d, files, message, force=False):
        ident = []
        if not self.dry:
            _, name = self.sh(["git", "config", "user.name"], cwd=d, check=False, quiet=True)
            if not name.strip():
                ident = ["-c", "user.name=OCS Kit Installer", "-c",
                         "user.email=%s" % (self.a.get("admin_email") or "ocs-kit@localhost")]
        self.sh(["git", "add"] + files, cwd=d)
        rc, _ = self.sh(["git"] + ident + ["commit", "-m", message], cwd=d, check=False)
        if rc != 0:
            self.log("(no changes to commit)")
        self.sh(["git", "push"] + (["--force"] if force else []) + ["origin", "HEAD:main"], cwd=d)

    # -- production -------------------------------------------------------
    def step_nginx(self):
        domain = self.a["domain"]
        ft = self.read(self.dirs["flask"] / "deploy" / "nginx.conf.template", "" if self.dry else None)
        st = self.read(self.dirs["spring"] / "deploy" / "nginx.conf.template", "" if self.dry else None)
        for name, text in render_nginx_configs(ft, st, domain, self.ports).items():
            self.write(self.dirs["nginx"] / name, text)

    # -- change features only ---------------------------------------------
    def step_change_features(self):
        a = self.a
        values = {"KIT": a["kit"], "FEATURES": features_env_value(self.overrides)}
        touched = False
        for kind in ("flask", "spring"):
            env_path = self.dirs[kind] / ".env"
            if not env_path.exists():
                continue
            touched = True
            extra = self.feature_settings(kind, exclude=values)
            self.write(env_path, set_env_values(self.read(env_path), dict(values, **extra)), 0o600,
                       describe="KIT=%s FEATURES=%s%s" % (values["KIT"], values["FEATURES"],
                                                        (" + " + ", ".join(sorted(extra))) if extra else ""))
            self.compose(kind, "up", "-d", "--force-recreate")
        if (self.dirs["flask"] / ".env").exists():
            self.wait_http("http://localhost:%d/login" % self.ports["flask"], 300, "Flask", self._compose_logs("flask"))
        if (self.dirs["spring"] / ".env").exists():
            self.wait_http("http://localhost:%d/login" % self.ports["spring"], 600, "Spring", self._compose_logs("spring"))
        kit_vals = {"name": a["kit"], "features": list(self.overrides)}
        pedits, _ = pages_setting_edits(self.feature_settings("pages"))
        kit_vals.update(pedits["kit"])
        pages = self.dirs["pages"]
        if (pages / "_config.yml").exists():
            touched = True
            self.write(pages / "_config.yml", edit_config_yml(self.read(pages / "_config.yml"), kit=kit_vals))
            rc, _ = self.sh(["docker", "restart", PREVIEW_CONTAINER], check=False)
            if rc == 0:
                self.wait_http("http://localhost:%d/" % int(a["preview_port"]), 1200, "Website preview")
            else:
                self.log("Restart your local preview to apply: cd %s && make" % pages)
        site = self.dirs["site"]
        if (site / "_config.yml").exists():
            touched = True
            self.sh(["git", "pull", "--ff-only", "origin", "main"], cwd=site, check=False)
            self.write(site / "_config.yml", edit_config_yml(self.read(site / "_config.yml"), kit=kit_vals))
            self.git_commit_push(site, ["_config.yml"], "Change kit to %s" % describe_selection(
                self.catalog, a["kit"], self.overrides))
            _, remote = self.sh(["git", "remote", "get-url", "origin"], cwd=site, quiet=True)
            m = re.search(r"github\.com[/:]([^/]+/[^/.\s]+)", remote)
            if m:
                self.sh(["gh", "workflow", "run", PAGES_WORKFLOW, "-R", m.group(1), "--ref", "main"], check=False)
        if not touched and not self.dry:
            raise InstallError("nothing to change: no OCS Kit install found in %s" % self.root)

    # -- finish -----------------------------------------------------------
    def step_save(self):
        data = saved_answers(self.a, self.catalog)
        data["action"] = "install"
        if self.gh_owner:
            data["github_owner"] = self.gh_owner
        data["features"] = list(self.overrides)
        data["installer_version"] = KIT_VERSION
        self.write(self.root / ANSWERS_FILE, json.dumps(data, indent=2) + "\n")
        self.feature_report = self.compute_feature_status()
        self.write(self.root / FEATURE_SETUP_FILE, feature_setup_markdown(
            self.catalog, self.a["kit"], self.overrides, self.feature_report, self.a.get("school_name", "")))
        if self.a["action"] == "change-features":
            return
        self.write(self.root / CREDENTIALS_FILE, self.credentials_text(), 0o600,
                   describe="accounts and URLs (chmod 600)")

    def compute_feature_status(self):
        installed, existing = set(), {}
        for kind in ("flask", "spring"):
            if self.a["install_backend"] or (self.dirs[kind] / ".env").exists():
                installed.add(kind)
            existing[kind] = parse_env(self.read(self.dirs[kind] / ".env", ""))
        if self.a.get("install_website") or (self.dirs["pages"] / "_config.yml").exists():
            installed.add("pages")
        return feature_status(self.catalog, self.a["kit"], self.overrides, self.a.get("settings") or {},
                              installed, existing)

    def backend_urls(self):
        if is_local(self.a):
            return [("Flask admin", "http://localhost:%d/login" % self.ports["flask"]),
                    ("Spring admin", "http://localhost:%d/login" % self.ports["spring"])]
        d = self.a["domain"]
        return [("Flask admin (after DNS + nginx)", "https://flask.%s/login" % d),
                ("Spring admin (after DNS + nginx)", "https://spring.%s/login" % d),
                ("Flask on this server", "http://localhost:%d/login" % self.ports["flask"]),
                ("Spring on this server", "http://localhost:%d/login" % self.ports["spring"])]

    def credentials_text(self):
        out = ["OCS-in-a-Kit credentials - keep this file private.",
               "Generated %s by the OCS-in-a-Kit installer." % time.strftime("%Y-%m-%d %H:%M"), "",
               "Kit: " + describe_selection(self.catalog, self.a["kit"], self.overrides), ""]
        urls = (self.backend_urls() if self.a["install_backend"] else []) + self.results["urls"]
        for label, url in urls:
            out.append("%-40s %s" % (label + ":", url))
        out.append("")
        if not self.passwords:
            envs = self._existing_envs()
            self.passwords = {k: {key: v.get(key, "") for _, key, _ in PASSWORD_FIELDS}
                              for k, v in envs.items() if v}
        if self.passwords:
            kinds = list(self.passwords)
            same = all(self.passwords[k] == self.passwords[kinds[0]] for k in kinds)
            groups = [("Flask and Spring", self.passwords[kinds[0]])] if same else \
                [(k.capitalize(), self.passwords[k]) for k in kinds]
            for title, pw in groups:
                out.append("Accounts (%s):" % title)
                for r in kit_roles(self.catalog, self.a["kit"]):
                    field = ROLE_PASSWORD.get(r["role"])
                    key = next((k for f, k, _ in PASSWORD_FIELDS if f == field), None)
                    if key:
                        out.append("  %-8s user %-10s password %s%s" % (
                            r["label"], r["seed_uid"], pw.get(key, ""),
                            "   (also the default password for sample accounts and resets)"
                            if r["role"] == "member" else ""))
                out.append("")
            out.append("Seed passwords only apply when a backend creates a new, empty database.")
            out.append("They are stored in flask/.env and spring/.env.")
        return "\n".join(out) + "\n"

    def build_summary(self, ok):
        a = self.a
        root = self.root
        q = lambda p: shlex.quote(str(p))  # noqa: E731
        urls = []
        if a["install_backend"] and a["action"] == "install":
            urls += self.backend_urls()
        urls += self.results["urls"]
        manage = []
        backends = [k for k in ("flask", "spring") if a["install_backend"] or (self.dirs[k] / ".env").exists()]
        for k in backends:
            manage.append({"title": "%s logs" % k.capitalize(), "cmd": "cd %s && docker compose logs -f web" % q(self.dirs[k])})
        if backends:
            manage.append({"title": "Stop the backends",
                           "cmd": " ; ".join("(cd %s && docker compose stop)" % q(self.dirs[k]) for k in backends)})
            manage.append({"title": "Start the backends",
                           "cmd": " ; ".join("(cd %s && docker compose up -d)" % q(self.dirs[k]) for k in backends)})
        if a.get("website_preview") or self.preview_method:
            if self.preview_method == "local":
                manage.append({"title": "Stop / start the website preview",
                               "cmd": "cd %s && make stop   # start again: make PORT=%s" % (q(self.dirs["pages"]), a["preview_port"])})
            else:
                manage.append({"title": "Stop / start the website preview",
                               "cmd": "docker stop %s   # start again: docker start %s" % (PREVIEW_CONTAINER, PREVIEW_CONTAINER)})
        me = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0].endswith(".py") else "ocs_kit_installer.py"
        manage.append({"title": "Update to a new version (keeps data and secrets)",
                       "cmd": "python3 %s --install-dir %s --ref <new-version>" % (q(me), q(root))})
        manage.append({"title": "Change kit or features only",
                       "cmd": "python3 %s --install-dir %s --change-features --kit <kit> --features +id,-id"
                              % (q(me), q(root))})
        prod = None
        if not is_local(a) and a["action"] == "install":
            prod = {"dns": [{"type": t, "name": n, "value": v} for t, n, v in dns_records(a)],
                    "nginx_files": [str(self.dirs["nginx"] / "flask.conf"), str(self.dirs["nginx"] / "spring.conf")]
                    if a["install_backend"] else [],
                    "nginx_commands": nginx_commands(self.dirs["nginx"], a["domain"]) if a["install_backend"] else []}
        report = getattr(self, "feature_report", None) or self.compute_feature_status()
        mgmt = ("Feature changes after install are made by Admins in the website's admin dashboard, or by "
                "re-running this installer (Change kit / features).")
        extra = (self.catalog.get("feature_management") or {}).get("description")
        return {"ok": ok, "kit": describe_selection(self.catalog, a["kit"], self.overrides),
                "features": report, "feature_management": mgmt + ((" " + extra) if extra else ""),
                "setup_guide": str(root / FEATURE_SETUP_FILE),
                "urls": [{"label": l, "url": u} for l, u in urls],
                "credentials_file": str(root / CREDENTIALS_FILE) if a["action"] == "install" else "",
                "accounts": [{"label": r["label"], "uid": r["seed_uid"]} for r in kit_roles(self.catalog, a["kit"])]
                if a["install_backend"] else [],
                "warnings": self.results["warnings"], "manage": manage, "production": prod,
                "log_file": str(root / LOG_FILE), "install_dir": str(root), "dry_run": self.dry}


def format_summary(s):
    out = ["", "=" * 70, "OCS-in-a-Kit: %s" % ("installed" if s["ok"] else "install FAILED"), "=" * 70,
           "Kit: " + s["kit"]]
    if s["urls"]:
        out.append("")
        out += ["  %-45s %s" % (u["label"], u["url"]) for u in s["urls"]]
    if s["credentials_file"]:
        accts = ", ".join("%s (%s)" % (x["label"], x["uid"]) for x in s.get("accounts") or [])
        out += ["", "Accounts%s - passwords in: %s" % ((" " + accts) if accts else "", s["credentials_file"])]
    if s.get("features"):
        out += ["", "Features:"]
        for f in s["features"]:
            out.append("  [%s] %s" % ("ready" if f["status"] == "ready" else "NEEDS SETUP", f["name"]))
            for m in f["missing"]:
                out.append("        set %s in %s%s" % (m["key"], SERVICE_WHERE[m["service"]],
                                                     (" - get it: " + m["get_it"]) if m["get_it"] else ""))
        out += ["  Setup guide for every feature: " + s["setup_guide"], "  " + s["feature_management"]]
    for w in s["warnings"]:
        out += ["", "NOTE: " + w]
    p = s.get("production")
    if p:
        out += ["", "Production next steps", "1. DNS records (at your domain registrar):"]
        out += ["     %-6s %-30s -> %s" % (r["type"], r["name"], r["value"]) for r in p["dns"]]
        if p["nginx_commands"]:
            out += ["2. nginx + HTTPS (run on this server once DNS points here):"]
            out += ["     " + c for c in p["nginx_commands"]]
    out += ["", "Manage:"]
    for m in s["manage"]:
        out += ["  # " + m["title"], "  " + m["cmd"]]
    out += ["", "Full log: " + s["log_file"]]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Terminal wizard
# ---------------------------------------------------------------------------

def _ask(prompt, default="", validate=None, secret=False, inp=input):
    while True:
        suffix = " [%s]" % default if (default != "" and not secret) else ""
        try:
            val = getpass.getpass(prompt + suffix + ": ") if secret else inp(prompt + suffix + ": ")
        except EOFError:
            val = ""
        val = val.strip()
        if not val:
            val = default
        msg = validate(val) if validate else None
        if not msg:
            return val
        print("  -> " + msg)


def _ask_yes(prompt, default=True, inp=input):
    d = "Y/n" if default else "y/N"
    while True:
        try:
            v = inp("%s [%s]: " % (prompt, d)).strip().lower()
        except EOFError:
            v = ""
        if not v:
            return default
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False


def _ask_choice(prompt, options, default, inp=input):
    """options: list of (value, label)."""
    print(prompt)
    for i, (val, label) in enumerate(options, 1):
        print("  %d) %s%s" % (i, label, "  (default)" if val == default else ""))
    while True:
        try:
            v = inp("Choose 1-%d: " % len(options)).strip()
        except EOFError:
            v = ""
        if not v:
            return default
        if v.isdigit() and 1 <= int(v) <= len(options):
            return options[int(v) - 1][0]
        for val, _ in options:
            if v == val:
                return val


def cli_choose_features(catalog, kit, current, inp=input):
    overrides = normalize_overrides(catalog, kit, current)
    print("\nFeatures (%s):" % catalog["kits"][kit].get("name", kit))
    ids = feature_ids(catalog)
    while True:
        selected = resolve_features(catalog, kit, overrides)
        locked = set(always_on_features(catalog))
        for cat, fids in feature_groups(catalog):
            print("  %s%s" % (cat["name"], (": " + cat["description"]) if cat["description"] else ""))
            for fid in fids:
                f = catalog["features"][fid]
                mark = "[x]" if fid in selected else "[ ]"
                extra = " (always on)" if fid in locked else ""
                if f.get("labels"):
                    extra += "  {%s}" % ", ".join(f["labels"])
                print("   %2d %s %-22s%s" % (ids.index(fid) + 1, mark, f.get("name", fid), extra))
        print("  -> " + describe_selection(catalog, kit, overrides))
        for note in selection_notes(catalog, kit, overrides):
            print("     note: " + note)
        v = inp("Toggle numbers (e.g. 3,7), or Enter to keep: ").strip()
        if not v:
            return overrides
        sel = set(selected)
        for part in v.replace(" ", ",").split(","):
            if part.isdigit() and 1 <= int(part) <= len(ids):
                fid = ids[int(part) - 1]
                if fid in locked:
                    continue
                sel ^= {fid}
        overrides = compute_overrides(catalog, kit, sel)


def run_cli_wizard(catalog, defaults, inp=input):
    a = dict(defaults)
    print("\nOCS-in-a-Kit installer (%s)\n" % KIT_VERSION)
    if defaults.get("_existing"):
        a["action"] = _ask_choice("An existing install was found in %s." % a["install_dir"],
                                  [("install", "Update / reinstall (keeps data and secrets)"),
                                   ("change-features", "Change kit / features only")], "install", inp)
    kits = [(k, "%s - %s" % (v.get("name", k), v.get("description", ""))) for k, v in catalog["kits"].items()]
    kit = _ask_choice("\n1) Choose a kit:", kits, a["kit"] if a["kit"] in catalog["kits"] else kits[0][0], inp)
    if kit != a["kit"]:
        a["features"] = []
    a["kit"] = kit
    print("   Preset: " + describe_selection(catalog, kit, []))
    if _ask_yes("   Customize features?", default=bool(a["features"]), inp=inp):
        a["features"] = cli_choose_features(catalog, kit, a["features"], inp)
    if a["action"] == "change-features":
        return cli_feature_setup(catalog, a, inp)
    print("\n2) What should be installed on THIS computer?")
    what = _ask_choice("", [("both", "Backend server and website"), ("backend", "Backend server only (Flask + Spring in Docker)"),
                            ("website", "Website only (frontend)")],
                       "both" if a["install_backend"] and a["install_website"] else
                       ("backend" if a["install_backend"] else "website"), inp)
    a["install_backend"] = what in ("both", "backend")
    a["install_website"] = what in ("both", "website")
    a["mode"] = _ask_choice("Mode:", [("local", "Try it on this computer (localhost)"),
                                      ("production", "Production server with a domain")], a["mode"], inp)
    print("\n3) School details")
    a["school_name"] = _ask("School name", a["school_name"],
                            lambda v: validate_answers(dict(a, school_name=v), catalog).get("school_name"), inp=inp)
    a["admin_email"] = _ask("Admin email", a["admin_email"],
                            lambda v: validate_answers(dict(a, admin_email=v), catalog).get("admin_email"), inp=inp)
    a["install_dir"] = _ask("Install folder", a["install_dir"], inp=inp)
    if a["mode"] == "production":
        a["domain"] = _ask("Domain (e.g. myschool.org)", a["domain"],
                           lambda v: validate_answers(normalize_answers(dict(a, domain=v)), catalog).get("domain"), inp=inp)
    if a["install_backend"]:
        print("\n4) Accounts. Passwords need 8+ characters with upper, lower, digit and one of %s."
              % SPRING_SYMBOLS)
        print("   Leave blank to keep the current one (re-run) or auto-generate.")
        labels = password_labels(catalog, a["kit"])
        for field, _key, _label in PASSWORD_FIELDS:
            a[field] = _ask(labels[field], "", secret=True, inp=inp,
                            validate=lambda v: ("Needs " + ", ".join(validate_password(v))) if v and validate_password(v) else None)
    cli_feature_setup(catalog, a, inp)
    if a["install_website"]:
        print("\n5) Website")
        for w in website_warnings(dict(a, website_publish=True)) if is_local(a) else []:
            print("   Note: " + w)
        a["website_publish"] = _ask_yes("Publish to GitHub Pages (uses the GitHub CLI 'gh')?", a["website_publish"], inp)
        if a["website_publish"]:
            a["github_owner"] = _ask("GitHub user or organization (blank = you)", a["github_owner"], inp=inp)
            a["github_repo"] = _ask("Repository name for the site", a["github_repo"], inp=inp)
        a["website_preview"] = _ask_yes("Run a local preview on this computer?", a["website_preview"] or not a["website_publish"], inp)
        if a["website_preview"]:
            a["preview_port"] = _ask("Preview port", str(a["preview_port"]), inp=inp)
    return a


def installed_services(a):
    out = set()
    if a.get("install_backend"):
        out |= {"flask", "spring"}
    if a.get("install_website"):
        out.add("pages")
    return out


def cli_feature_setup(catalog, a, inp=input):
    """Ask for the enabled features' settings (from the catalog). Blank = skip / keep."""
    services = installed_services(a)
    plan = feature_setup_plan(catalog, a["kit"], a["features"])
    askable = [(e, st) for e in plan for st in e["settings"]
               if st["service"] in services and st["key"] not in managed_keys(st["service"])]
    if not askable:
        return a
    print("\nFeature setup. Press Enter to skip any setting; you can add it later (see FEATURE-SETUP.md).")
    last = None
    settings = dict(a.get("settings") or {})
    for entry, st in askable:
        if entry is not last:
            print("\n  %s: %s" % (entry["name"], entry["summary"]))
            last = entry
        print("    %s -> %s%s" % (st["key"], SERVICE_WHERE[st["service"]], "  (required)" if st["required"] else ""))
        if st["help"]:
            print("      " + st["help"])
        if st["get_it"]:
            print("      get it: " + st["get_it"])
        cur = setting_value(settings, st)
        prompt = "    %s%s" % (st["key"], (" (e.g. %s)" % st["example"]) if st["example"] else "")
        v = _ask(prompt, "" if st["secret"] else cur, secret=st["secret"], inp=inp)
        if v:
            settings[st["id"]] = v
    a["settings"] = settings
    return a


def cli_prereq_report(items):
    print("\nPrerequisites:")
    for it in items:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[it["status"]]
        print("  [%s] %-28s %s" % (mark, it["name"], it["detail"]))
        if it["fix"]:
            print("         fix: %s" % it["fix"])
        if it["link"]:
            print("         see: %s" % it["link"])
    return not any(i["status"] == "fail" for i in items)


# ---------------------------------------------------------------------------
# Web GUI
# ---------------------------------------------------------------------------

GUI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OCS-in-a-Kit Installer</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--text:#1d2330;--muted:#5b6475;--line:#d9dde7;--accent:#2457d6;--accent-ink:#fff;
--ok:#18794e;--warn:#9a6700;--bad:#c22d2d;--soft:#eef2fd;--code:#f1f3f8;--radius:12px}
@media (prefers-color-scheme:dark){:root{--bg:#11141b;--card:#1a1f2a;--text:#e7eaf1;--muted:#9aa3b5;--line:#2d3443;
--accent:#7aa2ff;--accent-ink:#0d1220;--ok:#4cc38a;--warn:#e5b546;--bad:#ff7b7b;--soft:#202839;--code:#141821}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:20px 16px 8px;max-width:820px;margin:0 auto}
h1{font-size:1.4rem;margin:0}
.sub{color:var(--muted);margin:2px 0 0}
main{max-width:820px;margin:0 auto;padding:0 16px 48px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:20px;margin-top:16px}
h2{font-size:1.2rem;margin:0 0 4px}
h3{font-size:1rem;margin:18px 0 6px}
p.help{color:var(--muted);margin:4px 0 14px}
ol.stepper{list-style:none;display:flex;flex-wrap:wrap;gap:6px;padding:0;margin:12px 0 0}
ol.stepper li{font-size:.8rem;padding:3px 10px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
ol.stepper li.cur{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
ol.stepper li.done{color:var(--ok);border-color:var(--ok)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.choice{display:block;border:2px solid var(--line);border-radius:var(--radius);padding:14px;cursor:pointer;background:var(--card)}
.choice:hover{border-color:var(--accent)}
.choice input{position:absolute;opacity:0;pointer-events:none}
.choice.sel{border-color:var(--accent);background:var(--soft)}
.choice:focus-within{outline:3px solid var(--accent);outline-offset:2px}
.choice b{display:block;margin-bottom:4px}
.choice span{color:var(--muted);font-size:.9rem}
label.f{display:block;font-weight:600;margin:14px 0 4px}
input[type=text],input[type=email],input[type=password],input[type=number]{width:100%;padding:10px 12px;border:1px solid var(--line);
border-radius:8px;background:var(--bg);color:var(--text);font:inherit}
input:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.err{color:var(--bad);font-size:.9rem;margin-top:4px;min-height:0}
.note{border-left:4px solid var(--warn);background:var(--soft);padding:10px 12px;border-radius:6px;margin:12px 0}
.feat{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--line)}
.feat:first-child{border-top:0}
.feat input{margin-top:5px;width:18px;height:18px}
.feat small{display:block;color:var(--muted)}
.badges{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.tag.chg{border-color:var(--accent);color:var(--accent)}
.tag{font-size:.75rem;border:1px solid var(--line);border-radius:99px;padding:0 8px;color:var(--muted);margin-left:6px}
.summary{font-weight:600;padding:10px 12px;background:var(--soft);border-radius:8px;margin:10px 0}
.switch{display:flex;align-items:center;gap:8px;font-weight:600;margin:10px 0}
.rules{list-style:none;padding:0;margin:6px 0 0;display:flex;flex-wrap:wrap;gap:4px 12px;font-size:.85rem}
.rules li{color:var(--muted)} .rules li.ok{color:var(--ok)} .rules li.ok::before{content:"\2713 "} .rules li:not(.ok)::before{content:"\2022 "}
.row{display:flex;gap:8px;align-items:center}
.row input{flex:1}
.nav{display:flex;justify-content:space-between;gap:12px;margin-top:20px;flex-wrap:wrap}
button{font:inherit;padding:10px 18px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
button:disabled{opacity:.5;cursor:not-allowed}
button.small{padding:6px 10px;font-size:.85rem}
.check{display:flex;gap:10px;padding:10px 0;border-top:1px solid var(--line)}
.check:first-child{border-top:0}
.badge{font-weight:700;font-size:.8rem;min-width:44px;text-align:center;border-radius:6px;padding:2px 6px;height:fit-content}
.b-ok{color:var(--ok);border:1px solid var(--ok)} .b-warn{color:var(--warn);border:1px solid var(--warn)} .b-fail{color:var(--bad);border:1px solid var(--bad)}
.check small{display:block;color:var(--muted)}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85rem}
code{background:var(--code);padding:1px 5px;border-radius:4px;word-break:break-all}
pre.log{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px;height:340px;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:0}
ul.steps{list-style:none;padding:0;margin:0 0 14px}
ul.steps li{padding:4px 0}
ul.steps li::before{display:inline-block;width:1.6em;font-weight:700}
ul.steps li.pending::before{content:"\25CB";color:var(--muted)} ul.steps li.running::before{content:"\25B6";color:var(--accent)}
ul.steps li.done::before{content:"\2713";color:var(--ok)} ul.steps li.failed::before{content:"\2717";color:var(--bad)}
ul.steps li.skipped{color:var(--muted)} ul.steps li.skipped::before{content:"\2013"}
table{border-collapse:collapse;width:100%;font-size:.9rem} th,td{text-align:left;padding:6px;border-bottom:1px solid var(--line);vertical-align:top;word-break:break-word}
dl.review{display:grid;grid-template-columns:minmax(120px,30%) 1fr;gap:6px 12px;margin:0} dl.review dt{color:var(--muted)} dl.review dd{margin:0;word-break:break-word}
.dry{display:inline-block;background:var(--warn);color:#000;border-radius:6px;padding:0 8px;font-size:.8rem;font-weight:700;margin-left:8px}
.hidden{display:none!important}
a{color:var(--accent)}
.sr{position:absolute;left:-9999px}
@media (max-width:520px){.card{padding:14px}dl.review{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>OCS-in-a-Kit installer <span id="dry" class="dry hidden">DRY RUN</span></h1>
  <p class="sub" id="version"></p>
  <ol class="stepper" id="stepper" aria-label="Progress"></ol>
</header>
<main>
  <div id="pages"></div>
  <div class="nav" id="nav">
    <button type="button" id="back">Back</button>
    <button type="button" id="next" class="primary">Next</button>
  </div>
  <p class="err" id="globalErr" role="alert"></p>
</main>
<script>
"use strict";
const TOKEN = "__TOKEN__";
const H = {"Content-Type":"application/json","X-OCS-Token":TOKEN};
let S = null, A = null, SEL = null, page = 0, customize = false, prereqs = null, logNext = 0, polling = null;
const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path, body){
  const r = await fetch(path + (path.includes("?") ? "&" : "?") + "t=" + TOKEN,
    body === undefined ? {headers:H} : {method:"POST", headers:H, body:JSON.stringify(body)});
  if(!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
// ---- features (mirrors the Python functions; ids come only from the catalog) ----
const ids = () => Object.keys(S.catalog.features);
const locked = () => ids().filter(f => S.catalog.features[f].always_on);
function preset(kit){ const f = S.catalog.kits[kit].features; const s = new Set(f.includes("*") ? ids() : f); locked().forEach(x=>s.add(x)); return ids().filter(x=>s.has(x)); }
function resolve(kit, ov){ const s = new Set(preset(kit)); (ov||[]).forEach(t => { if(t[0]==="+") s.add(t.slice(1)); else if(t[0]==="-") s.delete(t.slice(1)); }); locked().forEach(x=>s.add(x)); return ids().filter(x=>s.has(x)); }
function overrides(kit, sel){ const b = new Set(preset(kit)), s = new Set(sel); locked().forEach(x=>s.add(x));
  return ids().filter(f=>s.has(f)&&!b.has(f)).map(f=>"+"+f).concat(ids().filter(f=>b.has(f)&&!s.has(f)).map(f=>"-"+f)); }
function describe(kit, ov){ let t = S.catalog.kits[kit].name || kit; (ov||[]).forEach(x => { const f = S.catalog.features[x.slice(1)]; t += (x[0]==="+"?" + ":" \u2212 ") + (f ? f.name : x.slice(1)); }); return t; }
function pwProblems(p){ const r=[]; if(p.length<8) r.push("len"); if(!/[A-Z]/.test(p)) r.push("upper"); if(!/[a-z]/.test(p)) r.push("lower");
  if(!/[0-9]/.test(p)) r.push("digit"); if(![...p].some(c=>S.symbols.includes(c))) r.push("symbol"); if([...p].some(c=>!S.allowed.includes(c))) r.push("bad"); return r; }
// ---- pages ----
const PAGES = [
  {id:"kit", title:"Kit", fields:["kit","features","action"]},
  {id:"install", title:"Install", fields:["install_backend","mode"]},
  {id:"school", title:"School", fields:["school_name","admin_email","install_dir","domain"]},
  {id:"accounts", title:"Accounts", fields:["admin_password","teacher_password","default_password"]},
  {id:"setup", title:"Features", fields:[]},
  {id:"website", title:"Website", fields:["website_preview","website_publish","github_owner","github_repo","preview_port"]},
  {id:"check", title:"Check", fields:[]},
  {id:"review", title:"Review", fields:[]},
  {id:"progress", title:"Install", fields:[]},
  {id:"finish", title:"Done", fields:[]},
];
function visible(p){
  if(A.action === "change-features") return ["kit","setup","check","review","progress","finish"].includes(p.id);
  if(p.id==="accounts") return A.install_backend;
  if(p.id==="website") return A.install_website;
  return true;
}
function vis(){ return PAGES.filter(visible); }
function radio(name, val, label, desc, cur){
  return `<label class="choice ${cur===val?"sel":""}"><input type="radio" name="${name}" value="${esc(val)}" ${cur===val?"checked":""}><b>${esc(label)}</b><span>${esc(desc)}</span></label>`;
}
function check(name, label, desc, cur){
  return `<label class="choice ${cur?"sel":""}"><input type="checkbox" name="${name}" ${cur?"checked":""}><b>${esc(label)}</b><span>${esc(desc)}</span></label>`;
}
function field(name, label, type, help, extra=""){
  const v = A[name] == null ? "" : A[name];
  return `<label class="f" for="f_${name}">${esc(label)}</label>${help?`<p class="help" id="h_${name}">${help}</p>`:""}
  <input id="f_${name}" name="${name}" type="${type}" value="${esc(v)}" ${help?`aria-describedby="h_${name} e_${name}"`:`aria-describedby="e_${name}"`} ${extra}>
  <div class="err" id="e_${name}" role="alert"></div>`;
}
function renderKit(){
  const kits = S.catalog.kits;
  let h = `<h2>Choose a kit</h2><p class="help">A kit is a starting set of features. You can switch features on or off below, now or later.</p>`;
  if(S.existing){
    h += `<div class="note"><b>Existing install found in ${esc(A.install_dir)}.</b><div class="grid" style="margin-top:8px" role="radiogroup" aria-label="What to do">
      ${radio("action","install","Update / reinstall","Get the selected version, keep data, secrets and passwords.",A.action)}
      ${radio("action","change-features","Change kit / features only","Only rewrite the kit settings and restart. No downloads, secrets untouched.",A.action)}</div></div>`;
  }
  h += `<div class="grid" role="radiogroup" aria-label="Kit">` + Object.entries(kits).map(([k,v]) => radio("kit",k,v.name||k,v.description||"",A.kit)).join("") + `</div>`;
  h += `<label class="switch"><input type="checkbox" id="customize" ${customize?"checked":""}> Customize features</label>`;
  h += `<div class="summary" id="kitSummary" aria-live="polite"></div><div id="kitNotes" aria-live="polite"></div><div id="featList"></div><div class="err" id="e_features" role="alert"></div>`;
  return h;
}
function renderFeatures(){
  const sel = new Set(resolve(A.kit, A.features)), base = new Set(preset(A.kit)), lk = new Set(locked()), labels = S.catalog.labels || {};
  $("#featList").innerHTML = S.groups.map(g => `<h3>${esc(g.name)}</h3>${g.description?`<p class="help">${esc(g.description)}</p>`:""}<div>` + g.features.map(f => { const d = S.catalog.features[f];
    const badges = (d.labels||[]).map(l => `<span class="tag" title="${esc(labels[l]||"")}">${esc(l)}<span class="sr">: ${esc(labels[l]||"")}</span></span>`).join("");
    return `<label class="feat"><input type="checkbox" data-f="${esc(f)}" ${sel.has(f)?"checked":""} ${(!customize||lk.has(f))?"disabled":""}>
      <span><b>${esc(d.name||f)}</b>${lk.has(f)?'<span class="tag">always on</span>':""}${(base.has(f)!==sel.has(f))?'<span class="tag chg">'+(sel.has(f)?"added":"removed")+'</span>':""}<small>${esc(d.description||"")}</small><span class="badges">${badges}</span></span></label>`; }).join("") + `</div>`).join("");
  $("#kitSummary").textContent = describe(A.kit, A.features);
  refreshSelection();
}
async function refreshSelection(){
  try { SEL = await api("/api/selection", {kit:A.kit, features:A.features});
    if($("#kitSummary")) $("#kitSummary").textContent = SEL.summary;
    if($("#kitNotes")) $("#kitNotes").innerHTML = SEL.notes.map(n=>`<div class="note">${esc(n)}</div>`).join("");
  } catch(err){ /* summary from the local mirror stays */ }
}
function renderInstall(){
  return `<h2>What should be installed on this computer?</h2>
  <p class="help"><b>Backend server</b>: the Flask and Spring APIs that store accounts and data. They run in Docker on this computer.<br>
  <b>Website</b>: the static Jekyll site students and teachers open. It is hosted for free on GitHub Pages, and you can preview it on this computer.</p>
  <div class="grid">${check("install_backend","Backend server","Flask + Spring in Docker (ports 8587, 8585).",A.install_backend)}
  ${check("install_website","Website (frontend)","Publish to GitHub Pages and/or preview locally.",A.install_website)}</div>
  <div class="err" id="e_install_backend" role="alert"></div>
  <h3>Mode</h3><div class="grid" role="radiogroup" aria-label="Mode">
  ${radio("mode","local","Try it on this computer","Everything on localhost. Nothing is public. Great for a first look.",A.mode)}
  ${radio("mode","production","Production server with a domain","Public server: flask.&lt;domain&gt;, spring.&lt;domain&gt;, pages.&lt;domain&gt; with HTTPS.",A.mode)}</div>
  <div class="err" id="e_mode" role="alert"></div>`;
}
function renderSchool(){
  return `<h2>School details</h2>` + field("school_name","School name","text","Shown on the website and admin pages.","autocomplete=organization") +
    field("admin_email","Admin email","email","Contact email for the admin account.","autocomplete=email") +
    field("install_dir","Install folder","text","Code, settings and data go here. Back it up.") +
    (A.mode==="production" ? field("domain","Your domain","text","For example <code>myschool.org</code>. The installer sets up flask., spring. and pages. subdomains.") : "") +
    "";
}
function pwField(name, label){
  return `<label class="f" for="f_${name}">${esc(label)}</label><div class="row"><input id="f_${name}" name="${name}" type="password" autocomplete="new-password" value="${esc(A[name]||"")}" aria-describedby="r_${name} e_${name}">
  <button type="button" class="small" data-show="${name}" aria-label="Show password">Show</button></div>
  <ul class="rules" id="r_${name}"><li data-r="len">8+ characters</li><li data-r="upper">uppercase</li><li data-r="lower">lowercase</li><li data-r="digit">digit</li><li data-r="symbol">symbol ${esc(S.symbols)}</li></ul>
  <div class="err" id="e_${name}" role="alert"></div>`;
}
function renderAccounts(){
  return `<h2>Accounts</h2><p class="help">Passwords for the accounts created on first start. <b>Leave blank to auto-generate</b> (or, on a re-run, keep the current one).
   Everything is saved to <code>${esc(A.install_dir)}/CREDENTIALS.txt</code> (readable only by you). Don't use <code>$</code>.</p>` +
   (SEL ? `<p class="help">Accounts for the <b>${esc(S.catalog.kits[A.kit].name||A.kit)}</b> kit: ` + SEL.roles.map(r=>`<b>${esc(r.label)}</b> (user <code>${esc(r.seed_uid)}</code>)`).join(", ") + `. Only Admins can turn features on or off.</p>` : "") +
   ["admin_password","teacher_password","default_password"].map(f => pwField(f, SEL ? SEL.password_labels[f] : f)).join("");
}
function services(){ const s = []; if(A.install_backend) s.push("flask","spring"); if(A.install_website) s.push("pages"); return s; }
const WHERE = {flask:"Flask backend (flask/.env)", spring:"Spring backend (spring/.env)", pages:"website _config.yml (public)"};
function setupFields(){ const sv = services(); return (SEL ? SEL.setup : []).flatMap(e => e.settings.filter(st => sv.includes(st.service) && !st.managed)); }
function renderSetup(){
  if(!SEL) return `<h2>Feature setup</h2><p>Loading...</p>`;
  const sv = services(); let n = 0;
  let h = `<h2>Feature setup</h2><p class="help">Settings for the features you picked. Everything is optional now: leave a field empty to skip it. Features missing a <b>required</b> setting are listed on the last page and in <code>FEATURE-SETUP.md</code> with instructions.</p>`;
  SEL.setup.forEach(e => {
    h += `<h3>${esc(e.name)}</h3>${e.summary?`<p class="help">${esc(e.summary)}</p>`:""}`;
    e.settings.forEach(st => {
      const id = "setting:" + st.id;
      if(st.managed){ h += `<p class="help"><code>${esc(st.key)}</code>: set by the installer.</p>`; return; }
      if(!sv.includes(st.service)){ h += `<p class="help"><code>${esc(st.key)}</code>${st.required?' <span class="tag">required</span>':""}: set it in the ${esc(WHERE[st.service])} where that part is installed.</p>`; return; }
      const v = (A.settings||{})[st.id] || "";
      h += `<label class="f" for="${esc(id)}">${esc(st.key)} ${st.required?'<span class="tag chg">required</span>':'<span class="tag">optional</span>'}${st.secret?'<span class="tag">secret</span>':""}</label>
        <p class="help" id="h_${esc(id)}">${esc(st.help)} <br><small>Goes to: ${esc(WHERE[st.service])}${st.used_by.length>1?" • used by "+esc(st.used_by.join(", ")):""}</small>${st.get_it?` <a href="${esc(st.get_it)}" target="_blank" rel="noopener">Get it</a>`:""}</p>
        <input id="${esc(id)}" name="${esc(id)}" data-setting="${esc(st.id)}" type="${st.secret?"password":"text"}" autocomplete="off" value="${esc(v)}" placeholder="${esc(st.example||"")}" aria-describedby="h_${esc(id)} e_${esc(id)}">
        <div class="err" id="e_${esc(id)}" role="alert"></div>`; n++;
    });
    if(!e.settings.length) h += `<p class="help">No settings needed.</p>`;
  });
  return h;
}
function renderWebsite(){
  const localWarn = A.mode==="local" ? `<div class="note">A site on GitHub Pages is public and can only talk to <b>production</b> backends (https). In "Try it on this computer" mode, only the <b>local preview</b> can log in to your localhost backends.</div>` : "";
  return `<h2>Website</h2><p class="help">The website is static, so it doesn't run on your server: GitHub hosts it for free. The installer uses the GitHub CLI (<code>gh</code>) to copy (fork) the kit website into your account, configure it and turn on GitHub Pages.</p>${localWarn}
  <div class="grid">${check("website_publish","Publish on GitHub Pages", A.mode==="production" ? "Live at pages."+(A.domain||"<domain>")+" (custom domain)." : "Live at <owner>.github.io/<repo>.", A.website_publish)}
  ${check("website_preview","Local preview","Run the site on this computer at localhost (uses Docker, no Ruby needed).", A.website_preview)}</div>
  <div class="err" id="e_website_preview" role="alert"></div>
  <div id="ghOpts" class="${A.website_publish?"":"hidden"}">` + field("github_owner","GitHub user or organization","text","Leave blank to use your own account.") +
  field("github_repo","Repository name","text","A new repository with this name is created (a fork of OCS-Kit-Pages).") + `</div>
  <div id="pvOpts" class="${A.website_preview?"":"hidden"}">` + field("preview_port","Preview port","number","Default 4500.","min=1024 max=65535") + `</div>`;
}
function renderCheck(){
  return `<h2>Check this computer</h2><p class="help">The installer needs a few tools. Fix anything marked FAIL, then press <b>Check again</b>.</p>
  <div id="checks" aria-live="polite">Checking...</div><p><button type="button" id="recheck" class="small">Check again</button></p>`;
}
function renderReview(){
  const rows = [["Action", A.action==="change-features" ? "Change kit / features only" : "Install / update"], ["Kit", describe(A.kit, A.features)]];
  if(A.action!=="change-features"){
    rows.push(["On this computer", [A.install_backend&&"Backend server", A.install_website&&"Website"].filter(Boolean).join(" + ")]);
    rows.push(["Mode", A.mode==="local" ? "Try it on this computer (localhost)" : "Production: " + A.domain]);
    rows.push(["School", A.school_name + " (" + A.admin_email + ")"]);
    if(A.install_website) rows.push(["Website", [A.website_publish&&("GitHub Pages: "+(A.github_owner||"(you)")+"/"+A.github_repo), A.website_preview&&("local preview on port "+A.preview_port)].filter(Boolean).join("; ")]);
    if(A.install_backend) rows.push(["Passwords", ["admin_password","teacher_password","default_password"].map(k=>A[k]?"set":"auto").join(" / ")]);
  }
  rows.push(["Install folder", A.install_dir]);
  rows.push(["Version", S.ref]);
  return `<h2>Review</h2><dl class="review">` + rows.map(r=>`<dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd>`).join("") + `</dl>
   <p class="help" style="margin-top:14px">Building the backends takes 5-15 minutes the first time. You can leave this page open and watch the progress.</p>`;
}
function renderProgress(){
  return `<h2>Installing...</h2><ul class="steps" id="steps" aria-live="polite"></ul><pre class="log" id="log" tabindex="0" aria-label="Install log"></pre>`;
}
function renderFinish(s){
  if(!s) return "";
  let h = `<h2>${s.ok ? (s.dry_run ? "Dry run finished" : "OCS-in-a-Kit is ready") : "Install did not finish"}</h2><p class="summary">${esc(s.kit)}</p>`;
  if(!s.ok) h += `<div class="note">Something failed. Read the last lines of the log, fix the problem and run the installer again: it continues where it can and keeps your settings.</div>`;
  if(s.urls.length) h += `<h3>Open</h3><table><tbody>` + s.urls.map(u=>`<tr><td>${esc(u.label)}</td><td><a href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.url)}</a></td></tr>`).join("") + `</tbody></table>`;
  if(s.credentials_file) h += `<h3>Accounts</h3><p>${s.accounts.map(a=>`${esc(a.label)}: <code>${esc(a.uid)}</code>`).join(" • ")}. Passwords are saved in <code>${esc(s.credentials_file)}</code> (only you can read it).</p>`;
  s.warnings.forEach(w => h += `<div class="note">${esc(w)}</div>`);
  if(s.features && s.features.length){
    h += `<h3>Features</h3><table><thead><tr><th>Feature</th><th>Status</th><th>What to do</th></tr></thead><tbody>` + s.features.map(f => `<tr><td>${esc(f.name)}</td>
      <td>${f.status==="ready" ? '<span class="badge b-ok">READY</span>' : '<span class="badge b-warn">NEEDS SETUP</span>'}</td>
      <td>${f.missing.map(m=>`Set <code>${esc(m.key)}</code> in ${esc(WHERE[m.service])}${m.get_it?` (<a href="${esc(m.get_it)}" target="_blank" rel="noopener">get it</a>)`:""}`).join("<br>")}
      ${f.status!=="ready" && f.steps.length ? "<ol>" + f.steps.map(x=>`<li>${esc(x)}</li>`).join("") + "</ol>" : ""}${f.verify?`<small>Check: ${esc(f.verify)}</small>`:""}</td></tr>`).join("") + `</tbody></table>
      <p class="help">${esc(s.feature_management)} Setup guide for every feature: <code>${esc(s.setup_guide)}</code></p>`;
  }
  if(s.production){
    const p = s.production;
    h += `<h3>Production: next steps</h3><p>1. Add these DNS records at your domain registrar:</p><table><thead><tr><th>Type</th><th>Name</th><th>Points to</th></tr></thead><tbody>` +
      p.dns.map(r=>`<tr><td>${esc(r.type)}</td><td>${esc(r.name)}</td><td>${esc(r.value)}</td></tr>`).join("") + `</tbody></table>`;
    if(p.nginx_commands.length) h += `<p>2. Once DNS points to this server, install the nginx configs (already written to ${p.nginx_files.map(f=>"<code>"+esc(f)+"</code>").join(" and ")}) and get HTTPS certificates:</p><pre class="log" style="height:auto">${esc(p.nginx_commands.join("\n"))}</pre>`;
  }
  h += `<h3>Stop, start and update</h3><table><tbody>` + s.manage.map(m=>`<tr><td>${esc(m.title)}</td><td><code>${esc(m.cmd)}</code></td></tr>`).join("") + `</tbody></table>
   <p class="help">Full log: <code>${esc(s.log_file)}</code></p><p><button type="button" id="close">Close the installer</button></p>`;
  return h;
}
// ---- flow ----
function render(){
  const vp = vis(), cur = vp[page];
  $("#stepper").innerHTML = vp.map((p,i)=>`<li class="${i===page?"cur":(i<page?"done":"")}" ${i===page?'aria-current="step"':""}>${esc(p.title)}</li>`).join("");
  const r = {kit:renderKit, install:renderInstall, school:renderSchool, accounts:renderAccounts, setup:renderSetup, website:renderWebsite, check:renderCheck,
             review:renderReview, progress:renderProgress, finish:()=>renderFinish(S.summary)}[cur.id];
  $("#pages").innerHTML = `<section class="card" aria-labelledby="ph">${r()}</section>`;
  const h2 = $("#pages h2"); if(h2){ h2.id="ph"; h2.tabIndex=-1; }
  $("#back").classList.toggle("hidden", page===0 || ["progress","finish"].includes(cur.id));
  $("#next").classList.toggle("hidden", ["progress","finish"].includes(cur.id));
  $("#next").textContent = cur.id==="review" ? (A.action==="change-features" ? "Apply changes" : "Install") : "Next";
  $("#globalErr").textContent = "";
  bind(cur.id);
}
function bind(id){
  if(id==="kit"){ renderFeatures(); $("#customize").addEventListener("change", e => { customize = e.target.checked; if(!customize) A.features = []; renderFeatures(); }); }
  if(id==="accounts") ["admin_password","teacher_password","default_password"].forEach(updRules);
  document.querySelectorAll("[data-show]").forEach(b => b.addEventListener("click", () => { const i = $("#f_"+b.dataset.show); const show = i.type==="password"; i.type = show?"text":"password"; b.textContent = show?"Hide":"Show"; b.setAttribute("aria-label", show?"Hide password":"Show password"); }));
  if(id==="check") runCheck();
  if(id==="recheck"||$("#recheck")) $("#recheck").addEventListener("click", runCheck);
  if(id==="progress") poll();
  if($("#close")) $("#close").addEventListener("click", async () => { await api("/api/shutdown", {}); document.body.innerHTML = "<main><div class='card'><h2>You can close this tab.</h2></div></main>"; });
}
function updRules(name){
  const v = A[name]||"", probs = pwProblems(v);
  document.querySelectorAll(`#r_${name} li`).forEach(li => li.classList.toggle("ok", v!=="" && !probs.includes(li.dataset.r)));
  $("#e_"+name).textContent = v && probs.includes("bad") ? "Only letters, digits and these symbols: " + S.allowed.replace(/[A-Za-z0-9]/g,"") : (v && probs.length ? "Not strong enough yet." : "");
}
function onInput(e){
  const t = e.target;
  if(t.dataset.f){ const sel = new Set(resolve(A.kit, A.features)); t.checked ? sel.add(t.dataset.f) : sel.delete(t.dataset.f); A.features = overrides(A.kit, [...sel]); renderFeatures(); return; }
  if(t.dataset.setting){ A.settings = A.settings || {}; A.settings[t.dataset.setting] = t.value; return; }
  if(!t.name) return;
  if(t.type==="radio"){ if(t.name==="kit" && A.kit!==t.value) A.features = []; A[t.name] = t.value; render(); return; }
  if(t.type==="checkbox"){ A[t.name] = t.checked; if(t.name==="website_publish"||t.name==="website_preview"||t.name.startsWith("install_")) { render(); } return; }
  A[t.name] = t.type==="number" ? parseInt(t.value||"0",10) : t.value;
  if(t.name.endsWith("_password")) updRules(t.name);
}
async function runCheck(){
  $("#checks").textContent = "Checking...";
  try { const r = await api("/api/check", A); prereqs = r.items;
    $("#checks").innerHTML = r.items.length ? r.items.map(i => `<div class="check"><span class="badge b-${i.status}">${i.status.toUpperCase()}</span><div><b>${esc(i.name)}</b><small>${esc(i.detail)}</small>
      ${i.fix?`<small>Fix: ${esc(i.fix)}</small>`:""}${i.link?`<small><a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.link)}</a></small>`:""}</div></div>`).join("") : "Nothing to check.";
    (r.warnings||[]).forEach(w => $("#checks").insertAdjacentHTML("beforeend", `<div class="note">${esc(w)}</div>`));
  } catch(err){ $("#checks").textContent = "Check failed: " + err.message; }
}
async function validate(fields){
  const r = await api("/api/validate", A);
  let first = null;
  fields.forEach(f => { const el = document.getElementById("e_"+f); if(el) el.textContent = r.errors[f] || ""; if(r.errors[f] && !first) first = f; });
  if(first){ const i = document.getElementById("f_"+first) || document.getElementById(first) || document.querySelector(`#pages [name="${first}"]`); if(i) i.focus(); $("#globalErr").textContent = "Please fix the highlighted fields."; return false; }
  return true;
}
async function next(){
  const vp = vis(), cur = vp[page];
  $("#next").disabled = true;
  try {
    const fields = cur.id==="setup" ? setupFields().map(st => "setting:" + st.id) : cur.fields;
    if(fields.length && !(await validate(fields))) return;
    if(cur.id==="check"){ if(!prereqs){ await runCheck(); }
      if(prereqs && prereqs.some(i=>i.status==="fail") && !S.dry_run){ $("#globalErr").textContent = "Fix the items marked FAIL first, then press Check again."; return; } }
    if(cur.id==="review"){
      const r = await api("/api/install", A);
      if(!r.ok){ $("#globalErr").textContent = Object.values(r.errors||{}).join(" ") || r.error || "Could not start."; return; }
    }
    page = Math.min(page+1, vp.length-1); render(); const h = $("#ph"); if(h) h.focus();
  } catch(err){ $("#globalErr").textContent = "Error: " + err.message; }
  finally { $("#next").disabled = false; }
}
async function poll(){
  try {
    const r = await api("/api/progress?since=" + logNext);
    logNext = r.next;
    const log = $("#log");
    if(log && r.lines.length){ const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 30; log.textContent += r.lines.join("\n") + "\n"; if(atEnd) log.scrollTop = log.scrollHeight; }
    if($("#steps")) $("#steps").innerHTML = r.steps.map(s=>`<li class="${s.status}">${esc(s.label)}<span class="sr"> (${s.status})</span></li>`).join("");
    if(r.done){ S.summary = r.summary; const vp = vis(); page = vp.findIndex(p=>p.id==="finish"); render(); return; }
  } catch(err){ /* keep polling */ }
  setTimeout(poll, 1000);
}
$("#pages").addEventListener("change", e => { if(e.target.id !== "customize") onInput(e); });
$("#pages").addEventListener("input", e => { if(["text","email","password","number"].includes(e.target.type)) onInput(e); });
$("#back").addEventListener("click", () => { page = Math.max(0, page-1); render(); });
$("#next").addEventListener("click", next);
(async function(){
  S = await api("/api/state");
  A = S.answers; A.settings = A.settings || {};
  try { SEL = await api("/api/selection", {kit:A.kit, features:A.features}); } catch(err){}
  customize = (A.features||[]).length > 0;
  $("#version").textContent = "Version " + S.ref + (S.dry_run ? "" : "") + " \u2022 " + S.os;
  $("#dry").classList.toggle("hidden", !S.dry_run);
  if(S.running){ page = vis().findIndex(p=>p.id==="progress"); }
  render();
})();
</script>
</body>
</html>
"""


class GuiState:
    def __init__(self, catalog, answers, args, existing=False):
        self.catalog = catalog
        self.answers = answers
        self.args = args
        self.existing = existing
        self.token = secrets.token_urlsafe(24)
        self.installer = None
        self.thread = None
        self.lock = threading.Lock()
        self.server = None


class GuiHandler(http.server.BaseHTTPRequestHandler):
    server_version = "OCSKitInstaller"

    def log_message(self, fmt, *args):  # quiet
        pass

    @property
    def st(self):
        return self.server.state

    def _authorized(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        tok = self.headers.get("X-OCS-Token") or (q.get("t") or [""])[0]
        return secrets.compare_digest(tok.encode(), self.st.token.encode())

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype == "text/html":
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("request too large")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        if not self._authorized():
            return self._send(403, {"error": "forbidden: open the link printed by the installer"})
        path = urllib.parse.urlparse(self.path).path
        st = self.st
        if path == "/":
            return self._send(200, GUI_HTML.replace("__TOKEN__", st.token), "text/html")
        if path == "/api/state":
            with st.lock:
                running = st.installer is not None
                summary = st.installer.summary if st.installer else None
            ans = saved_answers(st.answers, st.catalog)
            ans.update({k: st.answers[k] for k in ("action", "install_dir")})
            groups = [dict(cat, features=fids) for cat, fids in feature_groups(st.catalog)]
            return self._send(200, {"catalog": st.catalog, "groups": groups, "answers": ans, "existing": st.existing,
                                    "ref": st.args.ref, "dry_run": st.args.dry_run, "os": os_name(),
                                    "symbols": SPRING_SYMBOLS, "allowed": PASSWORD_ALLOWED,
                                    "running": running, "summary": summary})
        if path == "/api/progress":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                since = int((q.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            if not st.installer:
                return self._send(200, {"lines": [], "next": 0, "steps": [], "done": False})
            return self._send(200, st.installer.progress(since))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(403, {"error": "forbidden"})
        path = urllib.parse.urlparse(self.path).path
        st = self.st
        try:
            body = self._body()
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"error": "bad JSON"})
        if path == "/api/shutdown":
            self._send(200, {"ok": True})
            threading.Thread(target=st.server.shutdown, daemon=True).start()
            return None
        if not isinstance(body, dict):
            return self._send(400, {"error": "expected a JSON object"})
        if path == "/api/selection":
            try:
                return self._send(200, selection_info(st.catalog, body.get("kit"), body.get("features")))
            except (CatalogError, AttributeError, TypeError) as exc:
                return self._send(400, {"error": str(exc)})
        answers = normalize_answers(body, env={})
        if path == "/api/validate":
            return self._send(200, {"errors": validate_answers(answers, st.catalog),
                                    "warnings": website_warnings(answers)})
        if path == "/api/check":
            return self._send(200, {"items": check_prerequisites(answers), "warnings": website_warnings(answers)})
        if path == "/api/install":
            errors = validate_answers(answers, st.catalog)
            if errors:
                return self._send(200, {"ok": False, "errors": errors})
            with st.lock:
                if st.installer and not st.installer.done:
                    return self._send(409, {"ok": False, "error": "an install is already running"})
                st.installer = Installer(answers, st.catalog, st.args.repo_base, st.args.ref,
                                         dry_run=st.args.dry_run, echo=st.args.echo_log)
                st.thread = threading.Thread(target=st.installer.run, daemon=True)
                st.thread.start()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def make_gui_server(state, port=0):
    srv = _Server(("127.0.0.1", port), GuiHandler)
    srv.state = state
    state.server = srv
    return srv


def run_gui(state, open_browser=True, headless=False):
    srv = make_gui_server(state, state.args.port)
    port = srv.server_address[1]
    url = "http://127.0.0.1:%d/?t=%s" % (port, state.token)
    print("\nOCS-in-a-Kit installer is running at:\n\n    %s\n" % url)
    if headless:
        print("This computer has no display. From your own computer, forward the port over SSH:\n"
              "    ssh -L %d:127.0.0.1:%d <user>@<this-server>\n"
              "then open the link above in your browser.\n" % (port, port))
    print("Keep this window open. Press Ctrl+C to quit.\n", flush=True)
    if open_browser and not headless:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping the installer.")
    finally:
        srv.server_close()
    inst = state.installer
    return 0 if (inst is None or inst.ok is not False) else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def has_display():
    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Install OCS-in-a-Kit (backends in Docker, website on GitHub "
                                            "Pages and/or a local preview).")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--gui", action="store_true", help="use the web-page wizard even without a display "
                                                      "(prints the URL and an SSH port-forward hint)")
    m.add_argument("--cli", action="store_true", help="use the terminal wizard")
    m.add_argument("--answers", metavar="FILE", help="unattended install from a JSON answers file "
                                                     "(passwords may come from OCS_ADMIN_PASSWORD etc.)")
    p.add_argument("--install-dir", help="install folder (default ~/ocs-kit, or the answers file's)")
    p.add_argument("--kit", help="kit id from kits/catalog.json (see --list-features)")
    p.add_argument("--features", help="feature overrides relative to the kit, e.g. +finance-sim,-hallpass; "
                                      "an explicit list; or '*'")
    p.add_argument("--list-features", action="store_true", help="list kits and features and exit")
    p.add_argument("--change-features", action="store_true",
                   help="only change kit/features of an existing install (no downloads, secrets untouched)")
    p.add_argument("--repo-base", default=DEFAULT_REPO_BASE,
                   help="URL prefix or local folder containing OCS-Kit-Flask, OCS-Kit-Spring, OCS-Kit-Pages "
                        "(default %(default)s)")
    p.add_argument("--ref", default=KIT_VERSION, help="tag or branch to install (default %(default)s); "
                                                      "'main' or 'master' = each repo's default branch")
    p.add_argument("--catalog", help="path to kits/catalog.json")
    p.add_argument("--preview-port", type=int, help="local website preview port (default 4500)")
    p.add_argument("--port-offset", type=int, help="add N to every backend host port and use separate image "
                                                   "names, to run next to another copy (testing)")
    p.add_argument("--port", type=int, default=0, help="port for the installer web page (default random)")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser for the web wizard")
    p.add_argument("--dry-run", action="store_true", help="print every command and file write; execute nothing")
    p.add_argument("--yes", "-y", action="store_true", help="CLI: don't ask for a final confirmation")
    args = p.parse_args(argv)
    args.echo_log = False
    return args


def initial_answers(args, catalog):
    raw = {}
    if args.answers:
        with open(args.answers, encoding="utf-8") as fh:
            raw = json.load(fh)
    install_dir = args.install_dir or raw.get("install_dir") or DEFAULT_ANSWERS["install_dir"]
    saved = load_saved_answers(install_dir) or {}
    existing = bool(saved)
    merged = dict(saved)
    merged.update(raw)
    merged["install_dir"] = install_dir
    if args.kit:
        merged["kit"] = args.kit
        if args.features is None and saved.get("kit") != args.kit:
            merged["features"] = []
    if args.features is not None:
        merged["features"] = args.features
    if args.preview_port:
        merged["preview_port"] = args.preview_port
    if args.port_offset is not None:
        merged["port_offset"] = args.port_offset
    if args.change_features:
        merged["action"] = "change-features"
    elif not raw.get("action"):
        merged["action"] = "install"
    a = apply_setting_env(normalize_answers(merged), catalog)
    if a["kit"] not in catalog["kits"]:
        a["kit"] = "education" if "education" in catalog["kits"] else next(iter(catalog["kits"]))
    else:
        try:
            a["features"] = normalize_overrides(catalog, a["kit"], a["features"])
        except CatalogError:
            pass  # reported by validate_answers
    return a, existing


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    args = parse_args(argv)
    try:
        catalog = load_catalog(args.catalog, args.ref if args.ref not in ("default", "HEAD") else "main",
                               args.repo_base)
    except CatalogError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    if args.list_features:
        print(format_feature_list(catalog))
        return 0
    try:
        answers, existing = initial_answers(args, catalog)
    except (OSError, ValueError) as exc:
        print("Error reading answers: %s" % exc, file=sys.stderr)
        return 2
    if answers["action"] == "change-features" and not existing and not args.dry_run:
        print("Error: no previous install found in %s (missing %s)." % (answers["install_dir"], ANSWERS_FILE),
              file=sys.stderr)
        return 2
    if answers["action"] == "change-features" and existing:
        saved = load_saved_answers(answers["install_dir"]) or {}
        for k in ("install_backend", "install_website", "website_preview", "website_publish", "mode",
                  "school_name", "admin_email", "domain", "github_owner", "github_repo", "preview_port",
                  "port_offset"):
            if k in saved:
                answers[k] = normalize_answers(dict(saved))[k]

    unattended = bool(args.answers) or (args.change_features and (args.kit or args.features is not None))
    use_gui = not unattended and not args.cli and (args.gui or has_display())
    if use_gui:
        state = GuiState(catalog, answers, args, existing)
        return run_gui(state, open_browser=not args.no_browser, headless=args.gui and not has_display())

    if not unattended:
        answers["_existing"] = existing
        answers = run_cli_wizard(catalog, answers)
        answers.pop("_existing", None)
        answers = normalize_answers(answers, env={})
    errors = validate_answers(answers, catalog)
    if errors:
        print("Please fix these answers:", file=sys.stderr)
        for k, v in errors.items():
            print("  %s: %s" % (k, v), file=sys.stderr)
        return 2
    for w in website_warnings(answers):
        print("Note: " + w)
    items = check_prerequisites(answers)
    if not cli_prereq_report(items) and not args.dry_run:
        print("\nFix the items marked FAIL and run the installer again.", file=sys.stderr)
        return 3
    if not unattended and not args.yes:
        print("\nKit: " + describe_selection(catalog, answers["kit"], normalize_overrides(catalog, answers["kit"], answers["features"])))
        if not _ask_yes("Start now?", True):
            return 1
    args.echo_log = True
    inst = Installer(answers, catalog, args.repo_base, args.ref, dry_run=args.dry_run, echo=True)
    ok = inst.run()
    print(format_summary(inst.summary))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
