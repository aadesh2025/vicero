"""Handoff triggering — pauses the bot and records a handoff (docs/08 §13)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Handoff
from app.realtime.hub import hub, inbox_topic
from app.webhooks.dispatch import emit_event

#: `Handoff.reason` for a conversation routed to the owner because the plan stopped the bot from
#: answering (trial over / messages spent — docs/18 §8). Kept apart from the reasons a person or the
#: model asks for ("keyword", "distress:crisis", …) so the inbox can label it differently.
PLAN_LIMIT_REASON = "plan_limit"

# Keyword triggers checked against the user's message when `features.handoff_enabled`.
HANDOFF_KEYWORDS = (
    "human",
    "real person",
    "real agent",
    "live agent",
    "speak to someone",
    "talk to a person",
    "talk to someone",
    "customer service",
    "representative",
    "agent please",
)


def wants_handoff(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in HANDOFF_KEYWORDS)


async def trigger_handoff(
    session: AsyncSession, conversation: Conversation, *, requested_by: str, reason: str | None
) -> Handoff:
    """Pause the bot on this conversation and open a handoff record (idempotent-ish)."""
    conversation.status = "handoff"
    handoff = Handoff(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        requested_by=requested_by,
        reason=reason,
        status="open",
    )
    session.add(handoff)
    await session.flush()
    await hub.publish(
        inbox_topic(conversation.organization_id),
        {
            "type": "handoff.requested",
            "conversation_id": str(conversation.id),
            "handoff_id": str(handoff.id),
            "reason": reason,
            "requested_by": requested_by,
        },
    )
    await emit_event(
        session,
        conversation.organization_id,
        "handoff.requested",
        {"conversation_id": str(conversation.id), "handoff_id": str(handoff.id), "reason": reason},
    )
    return handoff


async def has_open_plan_limit_handoff(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """True while a plan-limit handoff is still waiting for the owner (not yet taken over).

    Once the owner takes the conversation over (`assigned`) their replies are the answer, so
    the visitor's next message is no longer "unanswered".
    """
    stmt = (
        select(Handoff.id)
        .where(
            Handoff.conversation_id == conversation_id,
            Handoff.reason == PLAN_LIMIT_REASON,
            Handoff.status == "open",
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None
