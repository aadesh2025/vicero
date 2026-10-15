"""Per-organisation third-party connections that Vicero calls itself (ADR-121). First user: Google Calendar."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class GoogleCalendarConnection(Base, UUIDPrimaryKey, TimestampMixin):
    """One Google account per organisation. Tokens are encrypted with the app key (`app.core.crypto`) and are never
    returned by any endpoint or written to a log."""

    __tablename__ = "google_calendar_connections"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, index=True
    )
    connected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    #: The Google account that granted access (shown in the dashboard so people know whose calendar it is).
    google_email: Mapped[str | None] = mapped_column(String(320))
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary", nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    access_token_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: active | needs_reconnect (Google revoked or expired the grant; bookings keep working, without events)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
