"""Tools and tool-run logs."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, event, func, insert
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection as SyncConnection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey, uuid7


class Tool(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "tools"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # builtin | http | n8n | mcp | api | webhook | flow  (`http` is read as `api` with no connection)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # docs/28 §6. The connection supplies base URL + auth for `api` tools; RESTRICT so a connection
    # cannot be deleted from under a tool that still uses it.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("connections.id", ondelete="RESTRICT"))
    # {on_start, on_delay{after_s,text}, on_error} - stored in T1, spoken in T4.
    messages: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    rate_limit: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mode: Mapped[str] = mapped_column(String(8), default="sync", server_default="sync", nullable=False)


@event.listens_for(Tool, "after_insert")
def _bind_to_owner_agent(_mapper: Mapper[Tool], connection: SyncConnection, target: Tool) -> None:
    """A tool created for an agent is bound to it. Resolution reads `agent_tool_bindings` (docs/28 T1.5),
    so every path that sets `agent_id` - the service, the seed, tests - keeps meaning "this agent uses it"
    without each having to remember a second insert."""
    if target.agent_id is None:
        return
    from app.models.connections import AgentToolBinding

    connection.execute(
        insert(AgentToolBinding).values(
            id=uuid7(),
            organization_id=target.organization_id,
            agent_id=target.agent_id,
            tool_id=target.id,
            enabled=True,
            overrides={},
            position=0,
        )
    )


class ToolRun(Base, UUIDPrimaryKey):
    __tablename__ = "tool_runs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # success|error|timeout
    # docs/28 §6: who ran it, through which provider, and the request as sent - sanitized, never raw.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    provider: Mapped[str | None] = mapped_column(String(16))  # builtin|api|mcp|n8n|webhook|flow
    request_sanitized: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
