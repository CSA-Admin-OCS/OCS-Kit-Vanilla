"""Credential redaction for text that ends up in logs, reports, commit messages, PR bodies and issues.

Fail-closed by design: rather than parsing URLs exactly, it removes anything that could be a
credential, accepting that some harmless text is redacted too. ``redact_url`` is ``redact_text``
applied to one value, so there is a single implementation.

Per whitespace-separated token:
  1. a token containing '://': everything from the first '://' to the LAST '@' in the token is
     replaced with '://***@' (userinfo can contain '@', ':', '/', '?', '#'; when several URLs are
     glued together the whole run up to the last '@' goes);
  2. scp-like ``user@host:path``: the text before the last ``@host:`` is dropped;
     (for 1 and 2: if a secret param value is open where the cut ends, as in ``?token=a@b``,
     the rest of that value is redacted too);
  3. ``name=value`` pairs anywhere in the token (query strings, fragments, path segments, CLI
     flags, env lines): the value is replaced when the name looks secret-ish;
  4. long tokens are truncated AFTER redaction.
Then, over the whole text: anything shaped like a known secret (the scrub gate's patterns plus
looser variants) is replaced, whatever it is attached to.

Everything is str.find/rfind or regexes without nested quantifiers, so it runs in linear time.
"""

import re
from urllib.parse import unquote

from .scrub import SECRET_PATTERNS

MASK = "***"
REDACTED = "REDACTED"
MAX_TOKEN = 8192  # redacted tokens longer than this are cut

# param names whose values are secrets: unanchored, case-insensitive (pass covers passwd, db_pass, ...)
_SECRET_NAME = re.compile(r"(?i)(token|secret|pass|pwd|key|sig|auth|credential|session|cookie|bearer)")
_TOKEN = re.compile(r"\S+")
# query/fragment delimiters (kept by split); a param value ends at & ; or #
_DELIM = re.compile(r"([?&;#])")
_VALUE_END = re.compile(r"[&;#]")
# the host part of scp-like user@host: (single character class, no nesting)
_SCP_AT = re.compile(r"@[A-Za-z0-9][A-Za-z0-9.\-]*:")
_LEAD = "([{<'\"`"
# secret-shaped values, wherever they appear: the scrub gate's patterns plus looser variants
_SECRET_VALUES = [rx for rule, rx in SECRET_PATTERNS if rule != "secret:private-key"] + [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{16,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{16,}"),
    re.compile(r"gsk_[0-9A-Za-z]{16,}"),
    re.compile(r"xox[a-z]-[0-9A-Za-z\-]{8,}"),
    re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{12,}"),
    re.compile(r"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{16,}"),
]


def _secret_name(name):
    return bool(_SECRET_NAME.search(name)) or ("%" in name and bool(_SECRET_NAME.search(unquote(name))))


def _redact_params(tok):
    """Every ``name=value`` (split on ? & ; #, and on '=' chains): a secret-ish name redacts the rest of
    the value up to the next '&', ';' or '#' ('?' inside a value does not end it).
    Returns (text, open) where ``open`` means a secret value was still running at the end."""
    if "=" not in tok:
        return tok, False
    out = []
    redacting = False
    for part in _DELIM.split(tok):
        if len(part) == 1 and part in "?&;#":
            if part != "?":
                redacting = False
            if not redacting:
                out.append(part)
            continue
        if redacting or not part:
            continue
        if "=" in part:
            pieces = part.split("=")
            for i in range(len(pieces) - 1):
                if _secret_name(pieces[i]):
                    part = "=".join(pieces[:i + 1]) + "=" + REDACTED
                    redacting = True
                    break
        out.append(part)
    return "".join(out), redacting


def _cut(tok, a, b, repl):
    """Replace tok[a:b] with ``repl``. If a secret param value is open at ``b`` (its name was in the
    removed span, e.g. ``?token=x@...``), the value's continuation after ``b`` is redacted too."""
    _, still_open = _redact_params(tok[:b])
    rest = tok[b:]
    if still_open:
        m = _VALUE_END.search(rest)
        rest = REDACTED + (rest[m.start():] if m else "")
    return tok[:a] + repl + rest


def _redact_token(m):
    tok = m.group(0)
    if "@" in tok:
        first = tok.find("://")
        if first >= 0:
            last_at = tok.rfind("@")
            if last_at > first:
                tok = _cut(tok, first + 3, last_at + 1, MASK + "@")
        head = tok[:first] if first >= 0 else tok
        if "@" in head:  # scp-like user@host:path before any '://'
            last = None
            for sm in _SCP_AT.finditer(head):
                last = sm
            if last is not None:
                tok = _cut(tok, len(head) - len(head.lstrip(_LEAD)), last.start() + 1, "")
    tok, _ = _redact_params(tok)
    if len(tok) > MAX_TOKEN and "://" in tok:
        tok = tok[:MAX_TOKEN] + "...[truncated]"
    return tok


def redact_text(text):
    """Remove credentials from arbitrary text (see module docstring). Linear time."""
    if not text:
        return text
    text = _TOKEN.sub(_redact_token, str(text))
    for rx in _SECRET_VALUES:
        text = rx.sub(REDACTED, text)
    return text


def redact_url(url):
    """``redact_text`` for a single URL/path value; None and '' pass through."""
    if not url:
        return url
    return redact_text(str(url).strip())


def has_credentials(url):
    return bool(url) and redact_url(url) != str(url).strip()
