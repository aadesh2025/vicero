"""Declarative base, common mixins, and a UUIDv7 generator.

UUIDv7 is time-ordered, which keeps primary-key index inserts local (better than v4).
Python's stdlib has no uuid7 yet, so we build one from a 48-bit millisecond timestamp
plus random bits, per the RFC 9562 layout.
"""

from __future__ import annotations

import datetime as dt
import os
import time
import uuid

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUIDv7 (RFC 9562)."""
    unix_ms = int(time.time() * 1000)
    rand = os.urandom(10)
    raw = bytearray(unix_ms.to_bytes(6, "big") + rand)
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # variant 10
    return uuid.UUID(bytes=bytes(raw))


class Base(DeclarativeBase):
    """Declarative base for all models."""


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid7
    )


def _utcnow() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Python-side default/onupdate: the value is set on the instance at flush time, so the
    # attribute is never expired (avoids async lazy-load / MissingGreenlet when serializing).
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def normalize_email(email: str) -> str:
    """Collapse the cosmetic variants of one mailbox into one comparable string.

    Lowercase always; `+tag` stripped for every domain; dots removed **only** for Gmail, where
    they are genuinely insignificant. Used for "one trial per person" (`users.email_normalized`),
    never for the address we send to. It lives here, not in the auth module, because the `User`
    model keeps the column in step itself and models may depend on nothing but this file.
    """
    local, _, domain = email.strip().lower().partition("@")
    if domain in {"gmail.com", "googlemail.com"}:
        domain = "gmail.com"
        local = local.replace(".", "")
    return f"{local.split('+', 1)[0]}@{domain}"
