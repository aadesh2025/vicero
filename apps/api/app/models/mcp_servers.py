"""Org-scoped MCP server registrations (docs/17 Phase 1, §4)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MCPServer(Base, UUIDPrimaryKey, TimestampMixin):
    """One org's registered MCP server. Never resolved against another org's credentials —
    same rule ADR-055 established for guard models and ADR-047/048 for provider keys."""

    __tablename__ = "mcp_servers"
    __table_args__ = (Index("uq_mcp_servers_org_slug", "organization_id", "slug", unique=True),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # stdio | sse | streamable_http. stdio is a subprocess on our host, so staff only (docs/28 section 5.3).
    transport: Mapped[str] = mapped_column(String(24), nullable=False)
    # stdio: the command to spawn. sse: the server URL.
    url_or_command: Mapped[str] = mapped_column(Text, nullable=False)
    # Encrypted JSON (app.core.crypto): {"args": [...], "env": {...}, "headers": {...}}.
    # Never plaintext at rest — this can carry a bearer token or subprocess env secrets.
    auth_config_enc: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Prefix of the tool names the model sees: `<slug>__<tool>`. Unique per org.
    slug: Mapped[str | None] = mapped_column(String(32))
    # Optional Connection supplying credentials (header auth only). Its allowed_hosts must include this URL's host.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("connections.id", ondelete="RESTRICT"))
    # Discovery result: [{name, description, input_schema}]. A turn reads only this, never the live server.
    tools_cache: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    tools_cached_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AgentMCPBinding(Base, UUIDPrimaryKey, TimestampMixin):
    """Which of a server's tools one agent may use. An empty `enabled_tools` means none, never all."""

    __tablename__ = "agent_mcp_bindings"
    __table_args__ = (UniqueConstraint("agent_id", "mcp_server_id", name="uq_agent_mcp_bindings_agent_server"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    mcp_server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    enabled_tools: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    # {tool_name: {"description": str}} - what this agent's model reads instead of the server's text.
    tool_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
