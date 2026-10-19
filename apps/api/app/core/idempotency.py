"""One-shot claims with a TTL, for de-duplicating requests (idempotency keys, signature replays).

`claim(key, ttl)` is True the first time a key is seen inside its TTL and False after. Redis does it atomically
(`SET NX EX`) so it holds across API processes; if Redis is unreachable it falls back to a per-process table,
which is weaker (two processes can each accept the first copy) but never blocks a request on the cache.
"""

from __future__ import annotations

import time

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("idempotency")

_mem: dict[str, float] = {}
_MAX_MEM = 10_000


async def claim(key: str, ttl_s: int) -> bool:
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            return bool(await client.set(f"vicero:idem:{key}", "1", nx=True, ex=ttl_s))
        finally:
            await client.aclose()
    except Exception as exc:
        log.warning("idempotency_redis_unavailable", error=str(exc))
    now = time.monotonic()
    if len(_mem) > _MAX_MEM:
        for k in [k for k, exp in _mem.items() if exp <= now]:
            _mem.pop(k, None)
    if _mem.get(key, 0) > now:
        return False
    _mem[key] = now + ttl_s
    return True
