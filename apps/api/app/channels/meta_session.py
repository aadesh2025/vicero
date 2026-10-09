"""Short-lived, server-side store for the Facebook page picker (ADR-113).

After a customer logs in with Facebook we hold one page access token per Page they manage. Those tokens
must never reach the browser, so the browser gets only a random session id; the tokens wait here (encrypted,
10 minutes, bound to the org and user that started the flow) until the customer picks which Pages to connect.

Redis is the store (it is already a hard dependency of the worker). If Redis is unreachable it falls back to
process memory, which is fine for dev and tests; with several API workers that fallback could miss, and the
customer simply restarts the flow.
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from typing import Any

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.logging import get_logger

log = get_logger("channels.meta_session")

TTL_SECONDS = 600

_memory: dict[str, tuple[float, str]] = {}


async def _redis() -> Any | None:
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url)
        await client.ping()
        return client
    except Exception as exc:
        log.warning("meta_session_redis_unavailable", error=str(exc))
        return None


def _key(session_id: str) -> str:
    return f"meta:pages:{session_id}"


async def create(org_id: uuid.UUID, user_id: uuid.UUID, pages: list[dict[str, Any]], meta_user_id: str) -> str:
    """Store the pages (each including its ``access_token``) and return the opaque session id."""
    session_id = secrets.token_urlsafe(24)
    blob = encrypt(json.dumps({"org": str(org_id), "user": str(user_id), "meta_user_id": meta_user_id, "pages": pages}))
    client = await _redis()
    if client is not None:
        try:
            await client.set(_key(session_id), blob, ex=TTL_SECONDS)
            return session_id
        except Exception as exc:
            log.warning("meta_session_redis_write_failed", error=str(exc))
        finally:
            await client.aclose()
    _memory[session_id] = (time.monotonic() + TTL_SECONDS, blob)
    return session_id


async def load(session_id: str, org_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any] | None:
    """The stored session, or None if it is missing, expired, or belongs to a different org/user."""
    blob: str | None = None
    client = await _redis()
    if client is not None:
        try:
            raw = await client.get(_key(session_id))
            blob = raw.decode() if isinstance(raw, bytes) else raw
        except Exception as exc:
            log.warning("meta_session_redis_read_failed", error=str(exc))
        finally:
            await client.aclose()
    if blob is None:
        entry = _memory.get(session_id)
        if entry and entry[0] > time.monotonic():
            blob = entry[1]
        else:
            _memory.pop(session_id, None)
    if blob is None:
        return None
    try:
        data = json.loads(decrypt(blob))
    except Exception:
        return None
    if data.get("org") != str(org_id) or data.get("user") != str(user_id):
        return None
    return data  # type: ignore[no-any-return]


async def discard(session_id: str) -> None:
    _memory.pop(session_id, None)
    client = await _redis()
    if client is not None:
        try:
            await client.delete(_key(session_id))
        except Exception:
            pass
        finally:
            await client.aclose()
