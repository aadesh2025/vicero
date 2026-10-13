"""Inbox schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.modules.conversations.schemas import ContactOut as ContactOut
from app.modules.conversations.schemas import MessageOut


class HandoffOut(BaseModel):
    id: uuid.UUID
    status: str
    requested_by: str
    reason: str | None
    assigned_to: uuid.UUID | None
    notes: list[Any]
    tags: list[str]
    created_at: dt.datetime
    resolved_at: dt.datetime | None


class InboxItemOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    channel: str
    status: str
    title: str | None
    channel_user_id: str | None
    message_count: int
    last_message_at: dt.datetime | None
    created_at: dt.datetime
    handoff: HandoffOut | None
    # Null for conversations that predate contact resolution — the UI falls back to the
    # channel user id.
    contact: ContactOut | None = None
    #: Open review severity (docs/11 §L6): None | mild | elevated | crisis. Independent of
    #: `status` — an `elevated` conversation is still `active` and the bot is still answering.
    attention_level: str | None = None


class SendWindowOut(BaseModel):
    """Platform limits on replying right now. Null on channels that impose none."""

    open: bool
    closes_at: dt.datetime | None
    #: Pre-approved template names usable when the window is shut.
    templates: list[str] = []


class InboxDetail(InboxItemOut):
    messages: list[MessageOut]
    send_window: SendWindowOut | None = None


class TemplateRequest(BaseModel):
    template: str = Field(min_length=1, max_length=255)
    params: list[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    # The chat APIs take `message`, so integrators reasonably send that here too. Accept
    # both rather than 422 on a field name that differs only by history.
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1, max_length=8000, validation_alias=AliasChoices("text", "message"))


class AssignRequest(BaseModel):
    user_id: uuid.UUID


class NoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


class ConversationFlagOut(BaseModel):
    """One reason a human was asked to look."""

    id: uuid.UUID
    kind: str
    severity: str
    #: Short spans the classifier quoted as justification — never the whole message.
    signals: list[str]
    created_at: dt.datetime
    resolved_at: dt.datetime | None


class WorkflowApprovalOut(BaseModel):
    """A workflow paused on an Approval node, surfaced in the inbox (docs/17 Phase 2 item 4) —
    the same `Handoff` model a chat escalation uses, read through a workflow-shaped lens."""

    handoff_id: uuid.UUID
    workflow_run_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str
    #: The approval node's own message (`config.message`, rendered) — what a human is being
    #: asked to approve or reject. Falls back to a generic line if the node set none.
    message: str | None
    run_status: str
    assigned_to: uuid.UUID | None
    created_at: dt.datetime


class WorkflowApprovalDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")


class AttentionItemOut(InboxItemOut):
    """A row in the Attention queue.

    Carries the whole flag history rather than just the current level, because the trajectory
    is what an operator triages on: three flags in four minutes reads very differently from one
    flag an hour ago, and the current level alone cannot show that.
    """

    flags: list[ConversationFlagOut] = []
    #: The last few messages, so the queue is decidable without opening each conversation.
    recent_messages: list[MessageOut] = []
    #: False once a human takes over. Drives the "AI is still responding" indicator — it must
    #: never be ambiguous who is talking to the customer.
    bot_still_answering: bool = True
