"""Shared tool types: execution context and result."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentVersion


@dataclass(slots=True)
class ToolContext:
    """Everything a tool implementation may need at execution time."""

    session: AsyncSession
    org_id: uuid.UUID
    agent_id: uuid.UUID
    version: AgentVersion
    conversation_id: uuid.UUID | None = None
    # The tool_run row id for this call — set before dispatch so async tools (n8n) can pass it
    # as a callback token that later resolves the pending run.
    run_id: uuid.UUID | None = None
    # Where `send_product_images` queues photos for delivery after the reply. `None` means this
    # surface cannot show images (web widget, Playground), so the tool is not offered at all.
    outbound_media: list[uuid.UUID] | None = None
    # Where the interactive-block tools queue their (validated) blocks for this turn (docs/27 Phase 2).
    # `None` means blocks are not offered in this conversation — the tools are not registered either.
    blocks: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class ToolResult:
    output: dict[str, Any]
    status: str = "success"  # success | error | timeout
    error: str | None = None
