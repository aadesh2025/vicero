"""Live ingest progress: what stage a document is in and how far along, readable by the API.

**Why Redis and not the `documents` row.** The ingest task runs inside one database transaction
that commits only at the end (`worker.tasks._run_ingest`), so nothing it writes to the row is
visible until it finishes. That is why a document showed "Queued" for the whole minute it was
being embedded, and looked stuck. Committing mid-ingest would split one atomic re-ingest into
pieces that can be observed half-done. A short-lived Redis key is the honest side channel: the
worker writes it, the documents list reads it, and it expires on its own.

Best-effort in both directions. A Redis outage means no progress bar, never a failed ingest and
never a failed page load.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("rag.progress")

_TTL_SECONDS = 3600
_PREFIX = "ingest:progress:"

#: Stage names the UI understands, in order.
STAGES = ("starting", "fetching", "crawling", "reading", "chunking", "embedding", "saving")


def _key(document_id: uuid.UUID | str) -> str:
    return f"{_PREFIX}{document_id}"


#: After a failed Redis call, skip progress I/O for this long. Without it a missing Redis would cost a
#: one-second connect timeout on every embedding batch of every ingest.
_BACKOFF_SECONDS = 30.0
_down_until = 0.0


def _client() -> Any:
    from redis.asyncio import from_url

    return from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)


def _available() -> bool:
    return time.monotonic() >= _down_until


def _mark_down() -> None:
    global _down_until
    _down_until = time.monotonic() + _BACKOFF_SECONDS


async def report(document_id: uuid.UUID | str, stage: str, done: int = 0, total: int = 0) -> None:
    """Record where an ingest is. Never raises."""
    if not _available():
        return
    payload = json.dumps({"stage": stage, "done": int(done), "total": int(total), "at": int(time.time())})
    try:
        client = _client()
        try:
            await client.set(_key(document_id), payload, ex=_TTL_SECONDS)
        finally:
            await client.aclose()
    except Exception as exc:
        _mark_down()
        log.debug("ingest_progress_write_failed", error=str(exc)[:120])


async def clear(document_id: uuid.UUID | str) -> None:
    if not _available():
        return
    try:
        client = _client()
        try:
            await client.delete(_key(document_id))
        finally:
            await client.aclose()
    except Exception as exc:
        _mark_down()
        log.debug("ingest_progress_clear_failed", error=str(exc)[:120])


async def read_many(document_ids: Iterable[uuid.UUID | str]) -> dict[str, dict[str, Any]]:
    """`{document_id: {stage, done, total, at}}` for the ids that have progress. Never raises."""
    ids = [str(i) for i in document_ids]
    if not ids or not _available():
        return {}
    try:
        client = _client()
        try:
            values = await client.mget([_key(i) for i in ids])
        finally:
            await client.aclose()
    except Exception as exc:
        _mark_down()
        log.debug("ingest_progress_read_failed", error=str(exc)[:120])
        return {}
    out: dict[str, dict[str, Any]] = {}
    for doc_id, raw in zip(ids, values, strict=True):
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("stage") in STAGES:
            out[doc_id] = data
    return out
