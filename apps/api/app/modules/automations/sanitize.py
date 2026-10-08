"""Make anything that came from n8n safe to store and show to a client.

A run report carries free text from outside our trust boundary (n8n error messages, the workflow's input and
output). Before it reaches the database it is stripped of credentials, cut to a fixed size, and the error is
replaced by a short plain-language sentence. The raw error never reaches the client.
"""

from __future__ import annotations

import json
import re
from typing import Any

ERROR_MAX = 500
SUMMARY_MAX = 2000

_REDACTED = "[hidden]"

# Key names whose value is never kept, whatever it holds.
_SENSITIVE_KEY = re.compile(
    r"(authorization|auth|token|secret|password|passwd|pwd|api[-_]?key|apikey|cookie|credential|signature|"
    r"private[-_]?key|bearer|session|otp|cvv|card[-_]?number)",
    re.IGNORECASE,
)

# (pattern, replacement) for text that looks like a credential. Order matters: specific shapes first.
_TEXT_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + _REDACTED),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}"), "Basic " + _REDACTED),
    (re.compile(r"\b(?:sk|gsk|pk|rk|xox[abp]|ghp|gho|AKIA|AIza)[-_A-Za-z0-9]{12,}"), _REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), _REDACTED),  # a JWT
    (
        re.compile(
            r"(?i)\b(api[-_]?key|token|secret|password|passwd|pwd|authorization|signature)\b(\s*[:=]\s*)[^\s,;&\"']+"
        ),
        r"\1\2" + _REDACTED,
    ),
    (re.compile(r"(?i)([?&](?:api[-_]?key|key|token|secret|password|sig|signature)=)[^&\s]+"), r"\1" + _REDACTED),
    (re.compile(r"\b[A-Za-z0-9_-]{40,}\b"), _REDACTED),  # any long opaque blob
)


def redact_text(text: str) -> str:
    for pattern, replacement in _TEXT_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {
            str(k): (_REDACTED if _SENSITIVE_KEY.search(str(k)) else _scrub(v, depth + 1))
            for k, v in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [_scrub(v, depth + 1) for v in value[:20]]
    if isinstance(value, str):
        return redact_text(value)
    return value


def summarize(value: Any, limit: int = SUMMARY_MAX) -> str | None:
    """A redacted, size-limited text view of an input or output payload (None when there is nothing)."""
    if value is None or value == "" or value == {} or value == []:
        return None
    if isinstance(value, str):
        return truncate(redact_text(value), limit) or None
    try:
        text = json.dumps(_scrub(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = redact_text(str(value))
    return truncate(text, limit) or None


# (pattern on the raw error, what the client reads). First match wins.
_FRIENDLY: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)timed? ?out|etimedout|esockettimedout|deadline"),
        "A connected service took too long to answer.",
    ),
    (
        re.compile(
            r"(?i)\b401\b|\b403\b|unauthori[sz]ed|forbidden|invalid (?:api )?key|credential|authenticat|expired token"
        ),
        "A connected account refused the login. Our team will reconnect it.",
    ),
    (re.compile(r"(?i)\b404\b|not found|no such"), "A connected service could not find the item it was asked for."),
    (re.compile(r"(?i)\b429\b|rate.?limit|too many requests|quota"), "A connected service is busy right now."),
    (
        re.compile(
            r"(?i)econnrefused|enotfound|econnreset|getaddrinfo|could not (?:connect|resolve)|network|socket"
            r"|connection (?:cannot|could not) be established|incorrect host|refused the connection"
        ),
        "A connected service could not be reached.",
    ),
    (
        re.compile(r"(?i)\b5\d\d\b|internal server error|bad gateway|service unavailable"),
        "A connected service had an error.",
    ),
    (
        re.compile(r"(?i)invalid|missing|required|validation|unprocessable|\b400\b|\b422\b|malformed|parse"),
        "The information sent to this automation was incomplete or in the wrong format.",
    ),
)
_DEFAULT_ERROR = "The automation hit an unexpected problem. Our team has been notified."


def friendly_error(raw: str | None) -> str | None:
    """A short, plain-language reason for a failure. Never the raw n8n text."""
    if not raw or not raw.strip():
        return None
    for pattern, message in _FRIENDLY:
        if pattern.search(raw):
            return message
    return _DEFAULT_ERROR
