"""Throwaway-mailbox detection — a *soft* signal, never the control (docs/18 §10, ADR-091).

Email verification is what proves someone owns an address. This only makes minting trials
annoying, so it is built to be wrong safely:

* **A maintained list, refreshed on a schedule.** Throwaway services rotate domains faster than any
  list compiled by hand, so the domains come from the open-source
  `disposable-email-domains` blocklist (`DISPOSABLE_LIST_URL`), fetched weekly by Celery beat into
  Redis. The short hand-written set in `policy.py` stays as the offline fallback.
* **Fail open.** Feed unreachable, Redis down, DNS slow: the answer is "not known to be
  disposable", never an error, and a signup is never delayed for more than the DNS timeout.
* **The MX heuristic never blocks.** A domain whose mail servers do not exist, or whose mail
  servers belong to a listed throwaway service, is *recorded* on the signup's audit row and
  logged — a signal for a human, or for stricter limits later. Only a direct list hit refuses a
  signup, and only while `BLOCK_DISPOSABLE_EMAILS` is on.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import limiter
from app.modules.auth.policy import DISPOSABLE_DOMAINS

log = get_logger("auth.disposable")

REDIS_KEY = "botforge:disposable_domains"
#: How long the Redis copy lives if the weekly refresh stops working (then we fall back to the seed).
REDIS_TTL_SECONDS = 14 * 86400
_LOCAL_CACHE_SECONDS = 600
#: A list outside this range is a wrong file (an HTML error page, a truncated download), not a list.
_MIN_DOMAINS, _MAX_DOMAINS = 1_000, 500_000
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

_cache: frozenset[str] = frozenset()
_cache_loaded_at: float = -1e9


def _suffixes(domain: str) -> list[str]:
    """`a.b.mailinator.com` → itself and every parent down to two labels (a sub-domain of a listed
    throwaway domain is a throwaway)."""
    labels = domain.strip().lower().rstrip(".").split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)] or [domain.lower()]


def parse_list(text: str) -> set[str]:
    """One domain per line; `#` comments and blanks ignored; anything that isn't a domain dropped."""
    out: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line and _DOMAIN_RE.match(line):
            out.add(line)
    return out


async def _redis() -> Any | None:
    try:
        return await limiter._redis_client()
    except Exception:  # never let a cache lookup fail a signup
        return None


async def _known_domains() -> frozenset[str]:
    """Seed plus the refreshed list. The Redis copy is re-read at most every 10 minutes."""
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if now - _cache_loaded_at > _LOCAL_CACHE_SECONDS:
        _cache_loaded_at = now  # even on failure: don't hammer a broken Redis on every signup
        client = await _redis()
        if client is not None:
            try:
                members = await client.smembers(REDIS_KEY)
                _cache = frozenset(m.decode() if isinstance(m, bytes) else str(m) for m in members)
            except Exception as exc:
                log.warning("disposable_list_read_failed", error=str(exc))
    return DISPOSABLE_DOMAINS | _cache


async def is_listed(domain: str) -> bool:
    known = await _known_domains()
    return any(s in known for s in _suffixes(domain))


async def refresh(*, client: httpx.AsyncClient | None = None) -> int:
    """Fetch the list and replace the Redis copy atomically. Returns the number of domains stored.

    Raises on a bad download and leaves the previous copy untouched — a failed refresh must never
    leave the store emptier than it was.
    """
    global _cache, _cache_loaded_at
    own = client is None
    http = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        resp = await http.get(settings.disposable_list_url)
        resp.raise_for_status()
    finally:
        if own:
            await http.aclose()
    domains = parse_list(resp.text)
    if not _MIN_DOMAINS <= len(domains) <= _MAX_DOMAINS:
        raise ValueError(f"disposable list has {len(domains)} domains; refusing to store it")

    redis = await _redis()
    if redis is not None:
        tmp = f"{REDIS_KEY}:new"
        pipe = redis.pipeline()
        pipe.delete(tmp)
        pipe.sadd(tmp, *domains)
        pipe.expire(tmp, REDIS_TTL_SECONDS)
        pipe.rename(tmp, REDIS_KEY)
        await pipe.execute()
    else:
        log.warning("disposable_list_no_redis", domains=len(domains))
    _cache, _cache_loaded_at = frozenset(domains), time.monotonic()
    log.info("disposable_list_refreshed", domains=len(domains))
    return len(domains)


# ── the MX heuristic (signal only) ───────────────────────────────────────────
def _resolve_mx(domain: str, timeout: float) -> tuple[str, list[str]]:
    """('ok', [mx hosts]) | ('no_domain', []) | ('no_mail', []) | ('unknown', []). Blocking DNS."""
    import dns.exception
    import dns.resolver

    res = dns.resolver.Resolver()
    res.lifetime = timeout
    try:
        answers = res.resolve(domain, "MX")
        return "ok", [str(r.exchange).rstrip(".").lower() for r in answers]
    except dns.resolver.NXDOMAIN:
        return "no_domain", []
    except dns.resolver.NoAnswer:
        pass  # no MX record: mail may still go to the domain's A/AAAA record (RFC 5321 §5.1)
    except (dns.exception.DNSException, OSError):
        return "unknown", []
    for rtype in ("A", "AAAA"):
        try:
            res.resolve(domain, rtype)
            return "ok", []
        except dns.resolver.NoAnswer:
            continue
        except dns.resolver.NXDOMAIN:
            return "no_domain", []
        except (dns.exception.DNSException, OSError):
            return "unknown", []
    return "no_mail", []


async def mx_signal(domain: str) -> str | None:
    """Why this domain's mail setup looks throwaway, or None. Never raises, never blocks a signup."""
    if not settings.disposable_mx_check_enabled:
        return None
    try:
        state, hosts = await asyncio.wait_for(
            asyncio.to_thread(_resolve_mx, domain, settings.disposable_mx_timeout),
            timeout=settings.disposable_mx_timeout + 0.5,
        )
    except Exception:
        return None
    if state in ("no_domain", "no_mail"):
        return "no_mail_server"
    for host in hosts:
        if await is_listed(host):
            return "mx_on_listed_domain"
    return None
