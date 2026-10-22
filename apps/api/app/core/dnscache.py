"""Process-wide name-resolution cache: retry a flaky lookup, and serve the last good answer when it fails.

**Why this exists (measured on the production VPS, 2026-10-21).** The host loses 10-22% of packets,
starting at its provider's upstream router (mtr: loss begins at the first hop past the gateway),
and that loss reaches DNS. Every public resolver tried (9.9.9.9, 1.0.0.1, 1.1.1.1, 8.8.8.8)
dropped 1 query in 5-10 over UDP *and* TCP. A dropped query surfaced to visitors as
``network error: [Errno -3] Temporary failure in name resolution``: the Groq or Gemini call never
started. It also failed URL ingest, because `ssrf.is_blocked_host` treats an unresolvable host as
blocked. Docker's ``dns:`` setting (ADR-119) cannot help when the packets themselves are lost.

**What it does.** It wraps ``socket.getaddrinfo``, which asyncio, anyio/httpx, asyncpg and the
SSRF checks all go through. For public hostnames only:

1. A fresh answer (younger than `ttl`) comes from memory, so a new connection costs no DNS round
   trip at all. That is also a latency win on every turn that opens a connection.
2. A transient failure (``EAI_AGAIN``) is retried a couple of times with a short pause.
3. If every retry fails and an answer younger than `stale_ttl` exists, that answer is returned and
   a warning is logged. Groq, Gemini and the Graph API sit behind anycast addresses that do not
   move between lookups, so a day-old address beats a failed reply.

**What it deliberately does not cache.**

* Dot-less names (``postgres``, ``redis``, ``ollama``, ``docling``, ``localhost``). Those are
  Docker service names whose address changes when a container is recreated. Docker's embedded
  resolver answers them locally, without the lossy network, so caching them adds a stale-IP risk
  and no benefit.
* IP literals: nothing to resolve.
* Definitive failures (``EAI_NONAME``): a name that does not exist must still fail, and fast.

**Security note.** Pinning a name for `ttl` seconds narrows, not widens, the DNS-rebinding window
that `loaders.load_url` documents: the SSRF check and the connection that follows now see the same
cached address. Every answer still goes through the same SSRF checks as before.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger

log = get_logger("core.dnscache")

#: Errors worth retrying. ``EAI_AGAIN`` is what glibc returns when the resolver times out; on
#: musl and some platforms a timeout surfaces as ``EAI_FAIL``.
_TRANSIENT = frozenset({getattr(socket, "EAI_AGAIN", -3), getattr(socket, "EAI_FAIL", -4)})

_Key = tuple[str, Any, int, int, int, int]
_Entry = tuple[float, Any]  # (resolved_at, monotonic seconds; the getaddrinfo result)

_MAX_ENTRIES = 2048

_lock = threading.Lock()
_cache: dict[_Key, _Entry] = {}
_original: Callable[..., Any] | None = None
_ttl = 300.0
_stale_ttl = 86400.0
_retries = 2
_retry_delay = 0.25


def _cacheable(host: Any) -> bool:
    if not isinstance(host, str) or "." not in host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return False


def _resolve(original: Callable[..., Any], args: tuple[Any, ...]) -> Any:
    """Call the real resolver, retrying transient failures. Raises the last error."""
    attempt = 0
    while True:
        try:
            return original(*args)
        except socket.gaierror as exc:
            if exc.errno not in _TRANSIENT or attempt >= _retries:
                raise
            attempt += 1
            # Blocking on purpose: getaddrinfo already runs on a worker thread under asyncio.
            time.sleep(_retry_delay * attempt)


def _store(key: _Key, result: Any) -> None:
    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            # Drop the oldest quarter. A process talks to a handful of hosts; this only bounds a
            # pathological stream of distinct tenant URLs.
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _MAX_ENTRIES // 4]
            for old_key, _ in oldest:
                _cache.pop(old_key, None)
        _cache[key] = (time.monotonic(), result)


def cached_getaddrinfo(
    host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0
) -> Any:
    """Drop-in replacement for ``socket.getaddrinfo`` (same signature and return value)."""
    original = _original or socket.getaddrinfo
    args = (host, port, family, type, proto, flags)
    if not _cacheable(host):
        return original(*args)

    key: _Key = (host.lower(), port, int(family), int(type), int(proto), int(flags))
    entry = _cache.get(key)
    now = time.monotonic()
    if entry is not None and now - entry[0] < _ttl:
        return entry[1]

    try:
        result = _resolve(original, args)
    except socket.gaierror as exc:
        if exc.errno in _TRANSIENT and entry is not None and now - entry[0] < _stale_ttl:
            log.warning(
                "dns_served_stale",
                host=host,
                age_s=int(now - entry[0]),
                error=str(exc)[:120],
                impact="used the last good address because every lookup retry failed",
            )
            return entry[1]
        log.warning("dns_lookup_failed", host=host, error=str(exc)[:120], retries=_retries)
        raise

    _store(key, result)
    return result


def install(
    *, ttl: float = 300.0, stale_ttl: float = 86400.0, retries: int = 2, retry_delay: float = 0.25
) -> bool:
    """Patch ``socket.getaddrinfo`` for this process. Idempotent. Returns True if it patched now."""
    global _original, _ttl, _stale_ttl, _retries, _retry_delay
    with _lock:
        _ttl, _stale_ttl, _retries, _retry_delay = ttl, stale_ttl, max(0, retries), max(0.0, retry_delay)
        if _original is not None:
            return False
        _original = socket.getaddrinfo
        socket.getaddrinfo = cached_getaddrinfo
    log.info("dns_cache_installed", ttl_s=ttl, stale_ttl_s=stale_ttl, retries=retries)
    return True


def uninstall() -> None:
    """Restore the real resolver and drop the cache (tests)."""
    global _original
    with _lock:
        if _original is not None:
            socket.getaddrinfo = _original
            _original = None
        _cache.clear()


def is_installed() -> bool:
    return _original is not None


def install_from_settings() -> bool:
    """Entry-point hook for the API and the Celery worker: install unless `DNS_CACHE_ENABLED=false`."""
    from app.core.config import settings

    if not settings.dns_cache_enabled:
        return False
    return install(
        ttl=settings.dns_cache_ttl_seconds,
        stale_ttl=settings.dns_cache_stale_seconds,
        retries=settings.dns_lookup_retries,
    )
