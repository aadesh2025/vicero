"""The attention queue — ask a human to look, without pausing the bot (docs/11 §L6, Phase E).

**`attention` and `handoff` are different states and the distinction is the whole feature.**

    handoff    the bot is PAUSED and a human owns the conversation
    attention  the bot is STILL ANSWERING and a human is asked to look

The operator's requirement is explicit and it is the right call: on `elevated` the AI keeps
working while a person decides whether to step in. A queue that silences the bot every time
someone is angry replaces a bad reply with no reply, and the customer notices the second one
more. Only `crisis` suppresses generation — and even then it emits a written holding message
rather than nothing, because silence in a crisis is the worst outcome of all.

Publishing reuses `trigger_handoff()`'s pattern — the same realtime hub topic and the same
webhook dispatcher — rather than inventing a second channel, so an inbox already subscribed
receives attention events with no extra wiring.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.policy_guard import PolicyVerdict
from app.core.logging import get_logger
from app.models import Conversation, ConversationFlag
from app.realtime.hub import hub, inbox_topic
from app.webhooks.dispatch import emit_event

log = get_logger("chat.attention")

_ORDER = {"none": 0, "mild": 1, "elevated": 2, "crisis": 3}


def severity_rank(level: str | None) -> int:
    return _ORDER.get(level or "none", 0)


def escalates(current: str | None, incoming: str) -> bool:
    """Whether `incoming` raises the conversation's standing severity.

    Attention only ever ratchets **up** within a conversation. A customer who calms down after
    a crisis message has not stopped being someone a human should look at, and letting the
    level fall would drop them out of the queue before anyone opened it. Clearing is an
    operator action (`resolve_flags`), never an automatic one.
    """
    return severity_rank(incoming) > severity_rank(current)


async def raise_flag(
    session: AsyncSession,
    conversation: Conversation,
    *,
    kind: str,
    severity: str,
    signals: list[str] | None = None,
) -> ConversationFlag | None:
    """Record a flag and, if it escalates, publish it. Returns `None` for a no-op level.

    Idempotent per (conversation, kind, severity): re-flagging the same thing on every
    subsequent message would bury the queue in duplicates of one angry conversation.
    """
    if severity_rank(severity) == 0:
        return None

    existing = (
        await session.execute(
            select(ConversationFlag).where(
                ConversationFlag.conversation_id == conversation.id,
                ConversationFlag.kind == kind,
                ConversationFlag.severity == severity,
                ConversationFlag.resolved_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    flag = ConversationFlag(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        kind=kind,
        severity=severity,
        signals=list(signals or []),
    )
    session.add(flag)

    if escalates(conversation.attention_level, severity):
        conversation.attention_level = severity
    await session.flush()

    log.warning(
        "conversation_flagged",
        conversation_id=str(conversation.id),
        organization_id=str(conversation.organization_id),
        kind=kind,
        severity=severity,
        # Signals are short quoted spans, not the message. The raw text of a distressed
        # customer does not belong in a log that many people can read.
        signal_count=len(signals or []),
    )

    payload = {
        "conversation_id": str(conversation.id),
        "kind": kind,
        "severity": severity,
        "signals": list(signals or []),
    }
    # Same topic the inbox already listens on, so an open Attention tab updates live.
    await hub.publish(inbox_topic(conversation.organization_id), {"type": "attention", **payload})
    await emit_event(
        session, conversation.organization_id, "conversation.attention", payload, agent_id=conversation.agent_id
    )
    return flag


async def apply_policy_verdict(
    session: AsyncSession, conversation: Conversation, verdict: PolicyVerdict
) -> None:
    """Turn one graded message into flags. Never decides whether the bot answers.

    Suppression is the caller's call (`verdict.suppresses_generation`), kept there so the
    "who stops the bot" decision lives on the turn path where it is readable, rather than as a
    side effect of recording a flag.
    """
    if severity_rank(verdict.distress) > 0:
        await raise_flag(
            session, conversation, kind="distress", severity=verdict.distress, signals=verdict.signals
        )
    if verdict.abuse:
        await raise_flag(
            session,
            conversation,
            kind="abuse",
            severity="elevated",
            signals=[verdict.abuse_category] if verdict.abuse_category else [],
        )


async def resolve_flags(
    session: AsyncSession, conversation: Conversation, *, user_id: uuid.UUID
) -> int:
    """Clear the open flags on a conversation. The only way attention_level goes down."""
    rows = (
        (
            await session.execute(
                select(ConversationFlag).where(
                    ConversationFlag.conversation_id == conversation.id,
                    ConversationFlag.resolved_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = dt.datetime.now(tz=dt.UTC)
    for row in rows:
        row.resolved_at = now
        row.resolved_by = user_id
    conversation.attention_level = None
    await session.flush()
    await hub.publish(
        inbox_topic(conversation.organization_id),
        {"type": "attention_resolved", "conversation_id": str(conversation.id)},
    )
    return len(rows)
