"""Credential redaction for URLs that end up in logs, reports, commit messages, PR bodies and issues."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# query parameters whose values are treated as secrets
_SECRET_PARAM = re.compile(r"(?i)(token|key|secret|password|passwd|pwd|sig|signature|auth|credential)")
# scheme://userinfo@ anywhere in free text (git error output, exception messages)
_TEXT_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s@'\"<>]+@")
# scp-like ssh syntax: user@host:path (no scheme)
_SCP = re.compile(r"^[^@/\s]+@([^:/\s]+):(.*)$")


def redact_url(url):
    """Drop userinfo (user, password, token) and secret-looking query values; keep scheme, host[:port], path.

    ``https://x-access-token:T@github.com/o/r.git`` -> ``https://github.com/o/r.git``
    ``git@github.com:o/r.git`` (scp-like ssh)      -> ``github.com:o/r.git``
    Local paths and URLs without credentials are returned unchanged.
    """
    if not url:
        return url
    s = str(url).strip()
    if "://" not in s:
        m = _SCP.match(s)
        return "%s:%s" % (m.group(1), m.group(2)) if m else s
    try:
        parts = urlsplit(s)
    except ValueError:
        return redact_text(s)
    netloc = parts.netloc
    if "@" in netloc:
        hostport = netloc.rsplit("@", 1)[1]
        netloc = hostport
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if any(_SECRET_PARAM.search(k) for k, _ in pairs):
            query = urlencode([(k, "REDACTED" if _SECRET_PARAM.search(k) else v) for k, v in pairs])
    if netloc == parts.netloc and query == parts.query:
        return s
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def redact_text(text):
    """Remove ``scheme://userinfo@`` from every URL inside arbitrary text."""
    if not text:
        return text
    return _TEXT_USERINFO.sub(r"\1", str(text))


def has_credentials(url):
    return bool(url) and redact_url(url) != str(url).strip()
