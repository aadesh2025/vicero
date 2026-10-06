"""Display currency from the visitor's country (docs/22 §3, ADR-106).

**Display only.** The country decides which price list the pricing page *shows*. It must never
decide what is charged, what an org's ledger row says, or which plan an org gets: a VPN or a
forged header can put anyone in any country, and that is acceptable precisely because the worst
outcome is seeing another region's price list. Every money write takes its currency from an
explicit staff choice (admin API), never from here.

Pure functions, no DB, no I/O — `resolve_currency` only reads the request it is handed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from app.core.config import settings
from app.core.plans import DEFAULT_CURRENCY, SUPPORTED_CURRENCIES

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
    if country is not None:
        return currency_for_country(country), "geo"
    return DEFAULT_CURRENCY, "default"
