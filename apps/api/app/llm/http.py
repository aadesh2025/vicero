"""Outbound HTTP for chat providers: one keep-alive client per event loop, one retry on a failed connect.

**Why.** Every provider call used to open a brand-new `httpx.AsyncClient`, so every model call paid
DNS + TCP + TLS before the first byte, which is 3-4 round trips. On the production VPS each of
those round trips crosses a link that drops 10-22% of packets (2026-10-21, mtr), and a lost
handshake packet costs a 1 s+ retransmit. A turn makes one to three model calls. Reusing the
connection removes those round trips from every call after the first, which is the single
cheapest latency win available without touching the prompt.

**Connect failures are retried once.** `httpx.ConnectError` / `ConnectTimeout` (DNS failure,
refused, unanswered SYN) mean the request never left this process, so a retry cannot duplicate
a completion or double-bill. Read errors after the request was sent are *not* retried here;
the fallback chain (`app.llm.fallback`) owns that decision.

The connect phase gets its own short timeout (`llm_connect_timeout_seconds`, default 5 s). A
single 60 s timeout meant an unanswered SYN could hold a visitor for the whole minute.

**Same lifecycle rule as `chat.guard_http`.** An `httpx.AsyncClient` is bound to the loop it first
ran on, so the shared slot is per thread *and* per loop. Calls that inject a test `transport`, or
that carry a request hook (the tenant-endpoint SSRF guard), get a throwaway client exactly as
before and never touch the shared one.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("llm.http")

_T = TypeVar("_T")

_KEEPALIVE_SECONDS = 120.0
_RETRY_PAUSE_SECONDS = 0.3
_local = threading.local()

#: Exceptions raised before any byte of the request was sent. Safe to retry.
CONNECT_ERRORS: tuple[type[Exception], ...] = (httpx.ConnectError, httpx.ConnectTimeout)


def timeout(total: float) -> httpx.Timeout:
    """The provider's overall timeout, with a short connect phase."""
    connect = min(float(settings.llm_connect_timeout_seconds), float(total))
    return httpx.Timeout(total, connect=connect)


def shared_client() -> httpx.AsyncClient:
    """The provider client for the running loop on this thread, created on first use."""
    loop = asyncio.get_running_loop()
    slot: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = getattr(_local, "slot", None)
    if slot is not None and slot[0] is loop and not loop.is_closed() and not slot[1].is_closed:
        return slot[1]
    client = httpx.AsyncClient(
        timeout=timeout(60.0),  # overridden per request
        follow_redirects=False,
        limits=httpx.Limits(
            max_connections=50, max_keepalive_connections=20, keepalive_expiry=_KEEPALIVE_SECONDS
        ),
    )
    _local.slot = (loop, client)
    return client


@asynccontextmanager
async def provider_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    event_hooks: Mapping[str, list[Callable[..., Any]]] | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """The shared client, or a throwaway one closed on exit (tests, SSRF-guarded tenant endpoints)."""
    if transport is not None or event_hooks:
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            event_hooks=dict(event_hooks) if event_hooks else None,
        ) as client:
            yield client
        return
    yield shared_client()


async def with_connect_retry(call: Callable[[], Awaitable[_T]], *, provider: str) -> _T:
    """Run `call`, retrying `llm_connect_retries` times when the connection could not be opened."""
    retries = max(0, int(settings.llm_connect_retries))
    attempt = 0
    while True:
        try:
            return await call()
        except CONNECT_ERRORS as exc:
            if attempt >= retries:
                raise
            attempt += 1
            log.info(
                "llm_connect_retry",
                provider=provider,
                attempt=attempt,
                error=(str(exc) or type(exc).__name__)[:160],
            )
            await asyncio.sleep(_RETRY_PAUSE_SECONDS * attempt)
