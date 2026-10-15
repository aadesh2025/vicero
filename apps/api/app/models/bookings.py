"""Appointments booked by an agent's `book_appointment` tool (ADR-120).

The table is the source of truth for idempotency and availability; a calendar event (Google, through n8n) is a
mirror of a row, never the other way round. One row per `(organization_id, idempotency_key)`: a retried webhook
carries the same tool-run id and gets the same row back instead of a second appointment.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Booking(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_bookings_org_idempotency"),
        Index("ix_bookings_org_agent_start", "organization_id", "agent_id", "start_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    #: The channel the customer used, so the confirmation goes back on it.
    channel: Mapped[str | None] = mapped_column(String(16))
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Email or phone, as the customer gave it (normalised: lowercased email / digits with a leading +).
    customer_contact: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", nullable=False)  # confirmed|cancelled
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Calendar event id once n8n has mirrored the booking (null when no calendar is connected).
    external_event_id: Mapped[str | None] = mapped_column(String(255))
    confirmation_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
