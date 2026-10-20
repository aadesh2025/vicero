"""Saved external-API connections and per-agent tool bindings (docs/28 §6, ADR-132)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ARRAY, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Connection(Base, UUIDPrimaryKey, TimestampMixin):
    """How to log in to one external system: base URL, auth type and encrypted secrets.

    Reused by many API tools and MCP servers. `secrets_enc` is Fernet (app.core.crypto) over a JSON
    object whose shape depends on `auth_type`; it is never returned by the API (masked only) and never
    reaches the model, `tool_runs`, `agent_steps` or a log.
    """

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_connections_org_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    # none | api_key | bearer | basic | custom_headers | oauth2_client_credentials
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    secrets_enc: Mapped[str | None] = mapped_column(Text)
    default_headers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Hosts a tool on this connection may call. Defaults to the base URL's host alone.
    allowed_hosts: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    health_path: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AgentToolBinding(Base, UUIDPrimaryKey, TimestampMixin):
    """Which tools an agent may use, in what order, with per-agent overrides (description,
    messages, approval, rate limit). A tool row can be bound to many agents."""

    __tablename__ = "agent_tool_bindings"
    __table_args__ = (UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool_bindings_agent_tool"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    tool_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
