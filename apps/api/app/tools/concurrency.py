"""A per-organization cap on outbound tool calls in flight (docs/28 section 9, T4.6).

An in-process semaphore per org: it bounds how much of this process one tenant can use. It is not a global count
across processes (each API / worker process has its own), which is what a small single-host deployment needs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import settings

_slots: dict[tuple[int, uuid.UUID], asyncio.Semaphore] = {}
_MAX_ENTRIES = 2000


@asynccontextmanager
async def org_slot(org_id: uuid.UUID) -> AsyncIterator[None]:
    """Hold one of the organization's outbound slots for the duration of the block."""
    # A semaphore belongs to the event loop it first waits on, and a Celery task runs on a fresh loop, so the loop
    # is part of the key. Old loops' entries are dropped when the table grows.
    key = (id(asyncio.get_running_loop()), org_id)
    sem = _slots.get(key)
    if sem is None:
        if len(_slots) > _MAX_ENTRIES:
            _slots.clear()
        sem = _slots[key] = asyncio.Semaphore(max(1, settings.tool_max_concurrency_per_org))
    async with sem:
        yield
