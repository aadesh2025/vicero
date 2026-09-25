"""Token-bucket-ish rate limiting: Redis fixed-window with in-memory fallback (docs/02 §5)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("ratelimit")


class RateLimiter:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: object | None = None
        self._checked = False
        self._mem: dict[str, list[float]] = {}

    async def _redis_client(self) -> object | None:
        if not self._checked:
            self._checked = True
            try:
                from redis.asyncio import from_url

                client = from_url(self._redis_url)
                await client.ping()
                self._redis = client
            except Exception as exc:
                log.warning("ratelimit_redis_unavailable", error=str(exc))
                self._redis = None
        return self._redis

    async def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        client = await self._redis_client()
        if client is not None:
            try:
                count = await client.incr(key)  # type: ignore[attr-defined]
                if count == 1:
                    await client.expire(key, window)  # type: ignore[attr-defined]
                if count > limit:
                    ttl = await client.ttl(key)  # type: ignore[attr-defined]
                    return False, max(int(ttl), 1)
                return True, 0
            except Exception as exc:
                log.warning("ratelimit_redis_error", error=str(exc))

        now = time.monotonic()
        bucket = [t for t in self._mem.get(key, []) if now - t < window]
        if len(bucket) >= limit:
            self._mem[key] = bucket
            return False, int(window - (now - bucket[0])) + 1
        bucket.append(now)
        self._mem[key] = bucket
        return True, 0


    async def count(self, key: str, window: int) -> int:
        """Current hits in the window without adding one — for lockouts that count *failures*
        only, where every attempt must not tick the counter."""
        client = await self._redis_client()
        if client is not None:
            try:
                value = await client.get(key)  # type: ignore[attr-defined]
                return int(value or 0)
            except Exception as exc:
                log.warning("ratelimit_redis_error", error=str(exc))
        now = time.monotonic()
        return len([t for t in self._mem.get(key, []) if now - t < window])


limiter = RateLimiter(settings.redis_url)


def rate_limit(scope: str, limit: int | None = None, window: int | None = None) -> Callable[..., Awaitable[None]]:
    """Dependency factory: throttle a route by client IP.

    Limit/window are resolved per-request (not at import time) so runtime overrides — e.g. the
    test suite raising `auth_rate_limit` — actually take effect.
    """

    async def _dep(request: Request) -> None:
        lim = limit or settings.auth_rate_limit
        win = window or settings.auth_rate_window
        ip = request.client.host if request.client else "unknown"
        allowed, retry_after = await limiter.hit(f"rl:{scope}:{ip}", lim, win)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, slow down.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dep
