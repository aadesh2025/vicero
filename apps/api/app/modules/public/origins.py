"""Which websites may embed an agent's widget (the per-agent "Allowed websites" list).

The list lives in the widget config (`widget_configs.theme["allowedSites"]`), as bare hostnames. Empty or
absent means any site. A listed host also covers its subdomains (`customer.com` allows `www.customer.com`).
Ports and schemes are ignored: this is an embed allow-list, not an authentication boundary, and a
non-browser client can always send whatever `Origin` it likes.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

MAX_SITES = 50
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def normalize_site(entry: str) -> str:
    """`https://Customer.com:8443/path`, `*.customer.com` or `customer.com` -> `customer.com`. Raises ValueError."""
    raw = entry.strip().lower()
    if not raw:
        raise ValueError("a website entry is empty")
    host = urlsplit(raw if "://" in raw else f"//{raw}").hostname or ""
    host = host.removeprefix("*.").strip(".")
    if not _HOST.match(host) or ".." in host:
        raise ValueError(f"{entry!r} is not a valid website (use a domain such as customer.com)")
    return host


def normalize_sites(entries: list[str]) -> list[str]:
    if len(entries) > MAX_SITES:
        raise ValueError(f"at most {MAX_SITES} websites")
    seen: dict[str, None] = {}
    for entry in entries:
        seen[normalize_site(entry)] = None
    return list(seen)


def origin_host(origin: str) -> str | None:
    """The hostname of an `Origin` header value, or None for `null` / garbage."""
    try:
        parts = urlsplit(origin.strip().lower())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    return parts.hostname


def origin_allowed(origin: str, sites: list[str]) -> bool:
    """True when `sites` is empty (any site) or the origin's host is, or is under, a listed host."""
    if not sites:
        return True
    host = origin_host(origin)
    if host is None:
        return False
    return any(host == s or host.endswith(f".{s}") for s in sites)
