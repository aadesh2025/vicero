"""Conversations and messages."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

#: `Conversation.channel` for a turn taken in the builder's Playground.
#:
#: Lives beside the column rather than in either module that uses it: the agents service
#: writes it and the inbox service reads it, and a literal in both would be one rename away
#: from the inbox quietly listing nothing.
PLAYGROUND_CHANNEL = "playground"


class Conversation(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_agent_last_msg", "agent_id", "last_message_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # web|widget|api|telegram|...
    channel_user_id: Mapped[str | None] = mapped_column(String(255))
    # Who this thread is with. Nullable: conversations predate contacts, and an inbound
    # can arrive before we've resolved a profile for it.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    #: Severity of the open review flag on this conversation (docs/11 §L6, Phase E):
    #: `None` | "mild" | "elevated" | "crisis".
    #:
    #: A **separate axis from `status`, not a value of it** (ADR-057). `status` is a lifecycle
    #: — active → handoff → closed — while attention is a severity that coexists with any of
    #: them: the bot keeps answering an `elevated` conversation (status stays `active`), and a
    #: crisis that a human has taken over is still a crisis worth seeing (status `handoff`).
    #: Folding it into `status` would make taking over a conversation erase why it was flagged.
    attention_level: Mapped[str | None] = mapped_column(String(16))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(String(512))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    memory_summary: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Inbound only — `last_message_at` moves on our own outbound too, which would make a
    # bot reply look like customer activity and silently reopen WhatsApp's 24-hour
    # customer-service window. Kept separate so that check can't be fooled.
    last_inbound_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base, UUIDPrimaryKey):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant|system|tool
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tool_call_id: Mapped[str | None] = mapped_column(String(255))
    citations: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    #: Images on this message: `[{"media_id", "kind": "image", "direction": "in"|"out", "description"}]`.
    attachments: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    #: Interactive blocks this assistant message showed (docs/27 Phase 2): a list of validated block dicts, each
    #: with a plain-text `fallback_text`. NULL when none. `content` already carries the fallback text, so the
    #: inbox, transcripts and exports need no knowledge of this column.
    blocks: Mapped[list[Any] | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_micros: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConversationFlag(Base, UUIDPrimaryKey):
    """One reason a human was asked to look at a conversation (docs/11 §L6, Phase E).

    Append-only history rather than a single mutable field: a conversation that went
    `mild → elevated → crisis` over ten minutes tells an operator something a final-state
    column cannot, and the Attention tab renders that trajectory. `Conversation.attention_level`
    is the denormalised current maximum, kept for cheap sorting and filtering.

    `signals` holds short spans the classifier quoted as justification, so a row explains
    itself without a second call. Never the whole message: the flag list is read by more people
    than the inbox is, and a distressed customer's words are not decoration.
    """

    __tablename__ = "conversation_flags"
    __table_args__ = (Index("ix_conversation_flags_org_open", "organization_id", "resolved_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    #: distress | abuse | injection_attempt | prompt_leak | pii_egress | off_topic_repeat
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: none | mild | elevated | crisis — ordered, so "at least elevated" is a range query.
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    signals: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
