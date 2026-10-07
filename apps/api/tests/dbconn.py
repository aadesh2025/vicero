"""Test-suite database connections that survive a flaky localhost port-forward.

Why this exists (measured 2026-09-25): on a Windows host whose Postgres runs in Docker Desktop's WSL2
VM, roughly one fresh TCP connection in ~600 to the published port never gets its SYN answered.
Windows gives up after exactly 21 s (`OSError: [WinError 121] The semaphore timeout period has
expired`), and now and then a connection that *is* accepted goes quiet and never answers a query.
The suite opens a connection per test, so a full run meets a few of these; without limits a stalled
one hangs the run for as long as you are willing to wait, which is how `test_client_ip.py` "timed out
at 200 s" while passing in 3 s on its own.

Two cheap limits fix it without touching the code under test: a short connect timeout with a retry
(a dropped SYN costs 5 s and a second attempt, not 21 s and a failed test), and a command timeout
(a connection that goes silent fails the test instead of hanging the run).
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.config import settings

CONNECT_TIMEOUT_S = 5
COMMAND_TIMEOUT_S = 120
ATTEMPTS = 4


def new_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        connect_args={"timeout": CONNECT_TIMEOUT_S, "command_timeout": COMMAND_TIMEOUT_S},
    )


async def connect(engine: AsyncEngine) -> AsyncConnection:
    """`engine.connect()` with a bounded retry for a dropped connection attempt."""
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            return await asyncio.wait_for(engine.connect(), timeout=CONNECT_TIMEOUT_S + 3)
        except (OSError, TimeoutError) as exc:  # a dropped SYN, or no answer
            last = exc
            await asyncio.sleep(0.3 * (attempt + 1))
    assert last is not None
    raise last


def describe() -> dict[str, Any]:
    return {"connect_timeout_s": CONNECT_TIMEOUT_S, "command_timeout_s": COMMAND_TIMEOUT_S, "attempts": ATTEMPTS}
