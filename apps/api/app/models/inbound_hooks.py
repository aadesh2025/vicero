"""Inbound trigger hooks: a secret URL an external system calls to start or feed an agent (docs/28 section 5.5)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class InboundHook(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "inbound_hooks"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    # The unguessable part of the public URL. Not a secret on its own: callers still have to authenticate.
    public_id: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # send_message | run_workflow | set_variables | agent_turn
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    action_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # {field: "$.selector"} - which parts of the caller's JSON the action may use.
    payload_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(8), nullable=False)  # hmac | bearer
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet; shown to the owner once
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_called_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class InboundHookEvent(Base, UUIDPrimaryKey):
    """One authenticated call to a hook. Kept 30 days; the payload is stored sanitized and capped."""

    __tablename__ = "inbound_hook_events"

    inbound_hook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_hooks.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | error | duplicate
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    payload_sanitized: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
