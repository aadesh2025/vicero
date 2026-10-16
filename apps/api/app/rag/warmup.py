"""Warm-up and keep-warm for the stages that go cold when the bot is idle (ADR-122).

A WhatsApp turn after a quiet spell spent 8 s in retrieval: the embedding model had been unloaded by Ollama
(its default is 5 minutes) and the first call paid for loading it. Other first calls after idle pay in the
same way: a DNS lookup and TLS handshake to Groq and to Meta, a cold database connection.

`warm()` touches each of those once, times it, and logs which one was cold (`warmup_stage_cold`). It runs
when a worker starts and then every `WARMUP_INTERVAL_SECONDS` from beat, so the models and connections stay
loaded. It never raises: a warm-up that can break the worker is worse than a cold start.

Only stage names and milliseconds are logged.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import guard_http
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("rag.warmup")

#: Hosts whose first request after idle pays DNS + TLS. A HEAD on `/` costs nothing and any status will do.
_HOSTS = {"groq": "https://api.groq.com/", "graph": "https://graph.facebook.com/"}


async def _embed() -> None:
    from app.llm.registry import build_embedding_provider

    embedder = build_embedding_provider(settings.embedding_provider, settings.embedding_model)
    await embedder.embed(["warm-up"])


def _db(session_factory: async_sessionmaker[AsyncSession]) -> Callable[[], Awaitable[None]]:
    async def touch() -> None:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))

    return touch


def _host(url: str) -> Callable[[], Awaitable[None]]:
    async def touch() -> None:
        client = guard_http.shared_client()
        try:
            await guard_http.bounded(client.head(url, timeout=httpx.Timeout(3.0)), 3000)
        except httpx.HTTPStatusError:
            pass  # an answer of any kind means the name resolved and TLS is up

    return touch


async def warm(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Touch every cold-prone stage once. Returns `{stage_ms: ...}`; a stage that failed reports -1."""
    if not settings.warmup_enabled or settings.llm_force_fake:
        return {}
    stages: dict[str, Callable[[], Awaitable[None]]] = {"embed": _embed, "db": _db(session_factory)}
    stages.update({name: _host(url) for name, url in _HOSTS.items()})
    result: dict[str, int] = {}
    for name, fn in stages.items():
        t0 = time.perf_counter()
        try:
            await fn()
        except Exception as exc:
            result[name] = -1
            log.warning("warmup_stage_failed", stage=name, error=type(exc).__name__)
            continue
        ms = int((time.perf_counter() - t0) * 1000)
        result[name] = ms
        if ms >= settings.warmup_cold_ms:
            log.warning("warmup_stage_cold", stage=name, ms=ms, threshold_ms=settings.warmup_cold_ms)
    log.info("warmup_done", **{f"{k}_ms": v for k, v in result.items()})
    return result
