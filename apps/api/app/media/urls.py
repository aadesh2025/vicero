"""Signed, short-lived public URLs. Meta downloads the image we send from one of these (docs/26 §8)."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from app.core.config import settings


def _key() -> bytes:
    return (settings.media_url_signing_key or settings.secret_key).encode()


def _mac(media_id: uuid.UUID, exp: int) -> str:
    return hmac.new(_key(), f"{media_id}|{exp}".encode(), hashlib.sha256).hexdigest()


def sign(media_id: uuid.UUID, ttl: int | None = None) -> tuple[int, str]:
    exp = int(time.time()) + (settings.media_public_url_ttl_seconds if ttl is None else ttl)
    return exp, _mac(media_id, exp)


def verify(media_id: uuid.UUID, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    return hmac.compare_digest(_mac(media_id, exp), sig)


def public_url(media_id: uuid.UUID, ttl: int | None = None) -> str:
    exp, sig = sign(media_id, ttl)
    base = settings.api_base_url.rstrip("/")
    return f"{base}/v1/media/public/{media_id}?exp={exp}&sig={sig}"
