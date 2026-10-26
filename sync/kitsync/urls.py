"""Credential redaction for URLs that end up in logs, reports, commit messages, PR bodies and issues.

One parser: ``redact_text`` finds URL tokens in free text and runs each through ``redact_url``.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "REDACTED"

# query parameter names whose values are secrets (exact name, or ending in _<name> / -<name>)
_SECRET_PARAM = re.compile(
    r"(?i)^(?:.*[_\-.])?(token|access_token|private_token|id_token|refresh_token|key|api_key|apikey|"
    r"password|passwd|pwd|pass|secret|client_secret|sig|signature|auth|credentials?)$")

_SCHEME = r"[A-Za-z][A-Za-z0-9+.\-]*://"
# a URL token in text: scheme:// then anything up to whitespace, a quote, an angle bracket, a
# backtick, or the start of the next URL (so "a://x,b://y" yields two tokens)
_URL_IN_TEXT = re.compile(r"%s(?:(?!%s)[^\s'\"<>`])+" % (_SCHEME, _SCHEME))
# scp-like ssh syntax in text: user@host:path, where path has a '/' or ends in .git
_SCP_IN_TEXT = re.compile(r"(?<![\w.@\-/])[\w.\-]+@[\w\-]+(?:\.[\w\-]+)*:(?:[\w.~\-]+/[\w./~\-]*|[\w.~\-]+\.git)\b/?")
_SCP = re.compile(r"^[^@/\s]+@([^:/\s]+):(.*)$")
# trailing punctuation that belongs to the sentence, not the URL
_TRAILING = ".,;:!?)]}>'\"`"


def _redact_query(query):
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not any(_SECRET_PARAM.match(k) for k, _ in pairs):
        return query
    return urlencode([(k, REDACTED if _SECRET_PARAM.match(k) else v) for k, v in pairs], safe="/:")


def redact_url(url):
    """Drop userinfo (user, password, token) and secret query values; keep scheme, host[:port], path.

    ``https://x-access-token:T@github.com/o/r.git`` -> ``https://github.com/o/r.git``
    ``https://u:p@ss@host/r`` (``@`` in the password) -> ``https://host/r``
    ``https://h/r.git?access_token=T&ref=main``    -> ``https://h/r.git?access_token=REDACTED&ref=main``
    ``git@github.com:o/r.git`` (scp-like ssh)      -> ``github.com:o/r.git``
    Local paths and URLs without credentials are returned unchanged.
    """
    if not url:
        return url
    s = str(url).strip()
    m = re.match(_SCHEME, s)
    if not m:
        m2 = _SCP.match(s)
        return "%s:%s" % (m2.group(1), m2.group(2)) if m2 else s
    scheme, rest = s[:m.end()], s[m.end():]
    # userinfo runs to the LAST '@' before the first '/' (a raw '@', '?' or '#' in a password still ends here)
    first_slash = rest.find("/")
    at = rest.rfind("@", 0, first_slash if first_slash >= 0 else len(rest))
    if at >= 0:
        rest = rest[at + 1:]
    out = scheme + rest
    try:
        parts = urlsplit(out)
    except ValueError:
        return out
    query = _redact_query(parts.query)
    if query != parts.query:
        out = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    return out


def _redact_token(tok):
    core = tok.rstrip(_TRAILING)
    return redact_url(core) + tok[len(core):]


def redact_text(text):
    """Redact every URL (scheme:// and scp-like user@host:path) inside arbitrary text."""
    if not text:
        return text
    text = _URL_IN_TEXT.sub(lambda m: _redact_token(m.group(0)), str(text))
    return _SCP_IN_TEXT.sub(lambda m: redact_url(m.group(0)), text)


def has_credentials(url):
    return bool(url) and redact_url(url) != str(url).strip()
