"""Conversation, message, and chat schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1)
    stream: bool = True


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str | None
    tool_calls: dict[str, Any] | None
    tool_call_id: str | None
    citations: list[Any]
    provider: str | None
    model: str | None
    tokens_prompt: int
    tokens_completion: int
    cost_micros: int
    latency_ms: int | None
    error: str | None
    created_at: dt.datetime


class ContactOut(BaseModel):
    """The human on the other end: what the inbox and conversation list need to render a recognizable row."""

    id: uuid.UUID
    display_name: str | None
    avatar_url: str | None
    #: The platform handle without the "@" (Instagram), from `contacts.extra`. Null where there is none.
    username: str | None = None
    #: Set once this handle has been matched to a CRM person, so the UI can link there.
    crm_contact_id: uuid.UUID | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    channel: str
    status: str
    title: str | None
    message_count: int
    last_message_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    #: Null for widget/playground chats and threads that predate contact resolution.
    contact: ContactOut | None = None
    channel_user_id: str | None = None


class ConversationDetail(ConversationOut):
    memory_summary: str | None
    messages: list[MessageOut]


class UpdateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    status: str | None = Field(default=None, pattern="^(active|closed|handoff)$")
