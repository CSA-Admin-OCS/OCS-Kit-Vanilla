"""Credential redaction for URLs that end up in logs, reports, commit messages, PR bodies and issues.

One parser: ``redact_text`` finds URL tokens in free text and runs each through ``redact_url``.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "REDACTED"

# query parameter names whose values are secrets: unanchored, case-insensitive substring match
# (accessToken, X-Api-Key, authorization, client_secret_v2, sessionid, ...). Over-redacting is fine.
_SECRET_PARAM = re.compile(r"(?i)(token|secret|passw|pwd|key|sig|auth|credential|session|cookie|bearer)")

_SCHEME = r"[A-Za-z][A-Za-z0-9+.\-]*://"
# whitespace/quote/angle-bracket separated tokens (linear); '()[]' are not separators so a query
# after a parenthesis in a path stays attached to its URL
_TOKEN = re.compile(r"[^\s'\"<>`]+")
# scheme starts inside a token; the lookbehind keeps each scan position O(1) for long letter runs
_SCHEME_START = re.compile(r"(?<![A-Za-z0-9+.\-])" + _SCHEME)
# scp-like ssh syntax in text: user@host:path, where path has a '/' or ends in .git
_SCP_IN_TEXT = re.compile(r"(?<![\w.@\-/])[\w.\-]+@[\w\-]+(?:\.[\w\-]+)*:(?:[\w.~\-]+/[\w./~\-]*|[\w.~\-]+\.git)\b/?")
_SCP = re.compile(r"^[^@/\s]+@([^:/\s]+):(.*)$")
# trailing punctuation that belongs to the sentence, not the URL
_TRAILING = ".,;:!?)]}>'\"`"
# longest URL piece parsed; anything longer is cut (and marked) rather than parsed
MAX_URL = 8192


def _redact_query(query):
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not any(_SECRET_PARAM.search(k) for k, _ in pairs):
        return query
    return urlencode([(k, REDACTED if _SECRET_PARAM.search(k) else v) for k, v in pairs], safe="/:")


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


def _redact_piece(piece):
    if "@" not in piece and "?" not in piece:
        return piece  # no userinfo and no query: nothing to redact (keeps long inputs fast)
    core = piece.rstrip(_TRAILING)
    tail = piece[len(core):]
    if len(core) > MAX_URL:
        return redact_url(core[:MAX_URL]) + "...[truncated]"
    return redact_url(core) + tail


def _redact_token(tok):
    if "://" not in tok:
        return tok
    starts = [m.start() for m in _SCHEME_START.finditer(tok)]
    if not starts:
        return tok
    out = [tok[:starts[0]]]
    for i, a in enumerate(starts):
        b = starts[i + 1] if i + 1 < len(starts) else len(tok)
        out.append(_redact_piece(tok[a:b]))
    return "".join(out)


def redact_text(text):
    """Redact every URL (scheme:// and scp-like user@host:path) inside arbitrary text. Linear time."""
    if not text:
        return text
    text = _TOKEN.sub(lambda m: _redact_token(m.group(0)), str(text))
    if "@" in text:
        text = _SCP_IN_TEXT.sub(lambda m: redact_url(m.group(0)), text)
    return text


def has_credentials(url):
    return bool(url) and redact_url(url) != str(url).strip()
