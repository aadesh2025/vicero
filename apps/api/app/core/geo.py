"""Display currency from the visitor's country (docs/22 §3, ADR-106).

**Display only.** The country decides which price list the pricing page *shows*. It must never
decide what is charged, what an org's ledger row says, or which plan an org gets: a VPN or a
forged header can put anyone in any country, and that is acceptable precisely because the worst
outcome is seeing another region's price list. Every money write takes its currency from an
explicit staff choice (admin API), never from here.

No DB. The only I/O is a local IP-to-country file lookup (`GEOIP_DB_PATH`), used when no trusted
proxy header supplied a country.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Literal

import maxminddb

from app.core.clientip import resolve_client
from app.core.config import settings
from app.core.plans import DEFAULT_CURRENCY, SUPPORTED_CURRENCIES

log = logging.getLogger(__name__)

CurrencySource = Literal["query", "geo", "default"]

#: The 27 EU member states (ISO 3166-1 alpha-2) — **EU27, not the euro area**. Some members
#: (Denmark, Sweden, Poland …) do not use the euro; the EUR list is shown to the whole EU on
#: purpose, so a Swedish visitor sees a EUR price rather than USD.
EU27 = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
        "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    }
)

#: Cloudflare's "unknown" (XX) and "Tor" (T1) pseudo-countries: treated as no information.
_UNKNOWN_COUNTRIES = frozenset({"XX", "T1"})


def currency_for_country(country: str | None) -> str:
    """IN → INR, EU27 → EUR, everything else (including unknown / garbage) → USD."""
    if not isinstance(country, str):
        return DEFAULT_CURRENCY
    code = country.strip().upper()
    if code == "IN":
        return "INR"
    if code in EU27:
        return "EUR"
    return DEFAULT_CURRENCY


def geo_headers() -> tuple[str, ...]:
    """The configured country header names, in precedence order. Also what `Vary` must list."""
    return tuple(h.strip() for h in settings.geo_country_headers.split(",") if h.strip())


def country_from_headers(headers: Mapping[str, str]) -> str | None:
    """First usable country in the configured headers, or `None`. Ignores the headers entirely
    unless `TRUST_GEO_HEADERS` is on (docs/ENV.md: only behind a proxy that sets them)."""
    if not settings.trust_geo_headers:
        return None
    for name in geo_headers():
        value = headers.get(name, "").strip().upper()
        if len(value) == 2 and value.isalpha() and value not in _UNKNOWN_COUNTRIES:
            return value
    return None


@lru_cache(maxsize=2)
def _reader(path: str) -> maxminddb.Reader | None:
    """Open the .mmdb once per path. A missing/corrupt file is logged and treated as 'no database'
    - a pricing page must never 500 because a geo file is absent."""
    try:
        return maxminddb.open_database(path)
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        log.warning("geoip database %r could not be opened; IP geolocation is off", path)
        return None


def country_from_ip(ip: str | None) -> str | None:
    """ISO country for `ip` from the offline database, or `None` (no database, private/unknown
    address, not in the database, or a lookup error). Never raises."""
    if not ip or not settings.geoip_db_path:
        return None
    reader = _reader(settings.geoip_db_path)
    if reader is None:
        return None
    try:
        record = reader.get(ip)
    except (ValueError, maxminddb.InvalidDatabaseError):
        return None
    if not isinstance(record, dict):
        return None
    country = record.get("country")
    code = country.get("iso_code") if isinstance(country, dict) else None
    if isinstance(code, str) and len(code) == 2 and code.upper() not in _UNKNOWN_COUNTRIES:
        return code.upper()
    return None


def normalise_currency(value: str | None) -> str | None:
    """`'inr'` → `'INR'`; anything not in `SUPPORTED_CURRENCIES` → `None`."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if code in SUPPORTED_CURRENCIES else None


def resolve_currency(request: Any, override: str | None = None) -> tuple[str, CurrencySource]:
    """Pick the display currency. Precedence: explicit `override` (the manual switcher's
    `?currency=`) → country header → USD. An unsupported override is ignored, not an error — it
    falls through to the next rule, so a typo never 500s or blocks the page."""
    chosen = normalise_currency(override)
    if chosen is not None:
        return chosen, "query"
    country = country_from_headers(getattr(request, "headers", {}) or {})
    if country is None and settings.geoip_db_path:
        # Fallback when no proxy supplies a country: look the visitor's address up offline.
        # `resolve_client` only believes X-Forwarded-For from a trusted proxy (TRUSTED_PROXIES).
        client = resolve_client(request)
        if client.known:
            country = country_from_ip(client.ip)
    if country is not None:
        return currency_for_country(country), "geo"
    return DEFAULT_CURRENCY, "default"
