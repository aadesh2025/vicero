"""Channel connections (widget, telegram, whatsapp, slack, discord, api)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Channel(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "channels"
    __table_args__ = (
        Index("ix_channels_org_type", "organization_id", "type"),
        # One phone number / page / Instagram account can be attached to exactly one org (ADR-113).
        Index(
            "uq_channels_type_external_id",
            "type",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    # widget|telegram|whatsapp|instagram|facebook|slack|discord|api
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    webhook_secret: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    #: Provider-side id this channel receives for: WhatsApp phone_number_id, Messenger page id, Instagram
    #: account id. The shared Meta webhook routes on (type, external_id). Null for non-Meta/unknown.
    external_id: Mapped[str | None] = mapped_column(String(255))
    #: Parent object: WhatsApp WABA id, or the Facebook Page id behind a Messenger/Instagram channel.
    external_parent_id: Mapped[str | None] = mapped_column(String(255))
    #: manual (tokens pasted by the customer) | meta_oauth (one-click connect).
    connection_source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    #: active | needs_reconnect | disconnected. Anything but active never sends.
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    token_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
