"""The agent-level events (docs/28 T3.2): built here so every emit site sends the same, already-sanitized shape."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, CrmContact, Message
from app.webhooks.dispatch import emit_event, sanitize_payload

_SNIPPET = 200


async def emit_tool_called(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    tool_name: str,
    provider: str,
    status: str,
    latency_ms: int | None,
    args: dict[str, Any],
    run_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
) -> None:
    """`tool.called`: which tool, how it went, and a redacted view of the arguments (never the raw object)."""
    await emit_event(
        session,
        org_id,
        "tool.called",
        {
            "agent_id": str(agent_id) if agent_id else None,
            "conversation_id": str(conversation_id) if conversation_id else None,
            "tool": tool_name,
            "provider": provider,
            "status": status,
            "latency_ms": latency_ms,
            "args": sanitize_payload(args),
            "run_id": str(run_id),
        },
        agent_id=agent_id,
    )


async def conversation_summary(session: AsyncSession, conv: Conversation) -> dict[str, Any]:
    """A summary that costs no model call: how long it was and how it began and ended. A written summary
    would be a model call on every close; receivers that want one can ask for the transcript."""
    count_stmt = select(func.count()).select_from(Message).where(Message.conversation_id == conv.id)
    count = int((await session.execute(count_stmt)).scalar_one())

    async def edge(role: str, order: Any) -> str | None:
        stmt = (
            select(Message.content)
            .where(Message.conversation_id == conv.id, Message.role == role)
            .order_by(order)
            .limit(1)
        )
        text = (await session.execute(stmt)).scalar_one_or_none()
        return text[:_SNIPPET] if isinstance(text, str) else None

    return {
        "message_count": count,
        "first_user_message": await edge("user", Message.created_at.asc()),
        "last_assistant_message": await edge("assistant", Message.created_at.desc()),
    }


async def emit_conversation_ended(session: AsyncSession, conv: Conversation, *, reason: str) -> None:
    """`conversation.ended`: summary + the variables captured during it (empty until conversation variables ship)."""
    await emit_event(
        session,
        conv.organization_id,
        "conversation.ended",
        {
            "agent_id": str(conv.agent_id),
            "conversation_id": str(conv.id),
            "channel": conv.channel,
            "contact_id": str(conv.contact_id) if conv.contact_id else None,
            "reason": reason,
            "summary": await conversation_summary(session, conv),
            "variables": sanitize_payload(getattr(conv, "variables", None) or {}),
        },
        agent_id=conv.agent_id,
    )


async def emit_lead_captured(session: AsyncSession, conv: Conversation, person: CrmContact, *, created: bool) -> None:
    """`lead.captured`: the CRM learned a person (new, or newly enriched) from this conversation."""
    await emit_event(
        session,
        conv.organization_id,
        "lead.captured",
        {
            "agent_id": str(conv.agent_id),
            "conversation_id": str(conv.id),
            "crm_contact_id": str(person.id),
            "created": created,
            "name": person.display_name,
            "email": person.email,
            "phone": person.phone,
        },
        agent_id=conv.agent_id,
    )
