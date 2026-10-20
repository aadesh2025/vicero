"""Human handoff records."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class Handoff(Base, UUIDPrimaryKey):
    __tablename__ = "handoffs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    # Nullable — a workflow-approval handoff (docs/17 Phase 2 item 4) may have no conversation
    # at all, since `WorkflowRun.conversation_id` is itself nullable (a standalone workflow
    # with no chat agent behind it). Every chat handoff still always sets this.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # Set instead of `conversation_id` when this handoff exists because a workflow paused on
    # an Approval node, not because a chat conversation was escalated. `requested_by` is
    # `"workflow"` in that case (not `bot|user`) — see `app.workflows.service`.
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[str] = mapped_column(String(16), nullable=False)  # bot|user|workflow
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)  # open|assigned|resolved
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    # What a tool-approval request is asking to run (docs/28 T4.4): {tool_id, run_id, args, agent_id}. NULL otherwise.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
