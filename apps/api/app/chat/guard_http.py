"""One long-lived HTTP client for the guard calls (L2 injection, L3 policy), and a hard time box.

Two production facts shaped this (ADR-115):

* `httpx.Timeout` does not cover name resolution. `getaddrinfo("api.groq.com")` measured ~4 s on every
  DNS-cache miss inside the worker container, so a "300 ms" guard call could cost far more than 300 ms.
  `bounded()` wraps the **whole** call (DNS + connect + request) in `asyncio.wait_for`, so the cost of a
  slow resolver is capped at the budget. The resolver thread may keep running after the cancel; the
  caller does not wait for it.
* A client per call pays DNS and TLS every time. One client per event loop keeps the connection alive
  (and with it the resolved address) between turns. An `httpx.AsyncClient` is bound to the loop it was
  first used on, so the slot is per thread *and* per loop: a new loop (Celery's `asyncio.run` per task)
  gets a fresh client instead of reusing a dead one.

Tests inject a `transport`; those calls build a throwaway client exactly as before and never touch the
shared one.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

import httpx

_T = TypeVar("_T")

#: Extra time on top of the configured per-call budget before `bounded()` gives up. The httpx timeouts
#: normally fire first; this margin only matters for what they cannot see (DNS).
BOUND_MARGIN_SECONDS = 0.25

_KEEPALIVE_SECONDS = 120.0
_local = threading.local()


def shared_client() -> httpx.AsyncClient:
    """The client for the running loop on this thread, created on first use."""
    loop = asyncio.get_running_loop()
    slot: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = getattr(_local, "slot", None)
    if slot is not None and slot[0] is loop and not loop.is_closed():
        return slot[1]
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),  # overridden per request; this only guards a forgotten one
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=_KEEPALIVE_SECONDS),
    )
    _local.slot = (loop, client)
    return client


def make_client(transport: httpx.AsyncBaseTransport | None) -> tuple[httpx.AsyncClient, bool]:
    """`(client, owned)`: the shared client, or a throwaway one (owned=True, caller closes it) for tests."""
    if transport is not None:
        return httpx.AsyncClient(transport=transport), True
    return shared_client(), False


async def bounded(coro: Coroutine[Any, Any, _T], timeout_ms: int) -> _T:
    """Run `coro` for at most `timeout_ms` (+ margin), DNS included. Raises `TimeoutError` past that."""
    return await asyncio.wait_for(coro, timeout=timeout_ms / 1000.0 + BOUND_MARGIN_SECONDS)


def describe(exc: BaseException, timeout_ms: int) -> str:
    """A log-safe reason. `TimeoutError()` stringifies to "", which read as "unknown error" in the logs."""
    if isinstance(exc, TimeoutError):
        return f"timed out after {timeout_ms} ms (DNS + connect + request)"
    return (str(exc) or type(exc).__name__)[:200]
