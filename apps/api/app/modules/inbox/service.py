"""Inbox: handoff queue, operator actions, and operator-reply delivery."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel, whatsapp
from app.channels.base import ChannelAuthRejected
from app.channels.reconnect import flag_token_rejected
from app.chat import attention
from app.chat.handoff import trigger_handoff
from app.core import rbac
from app.core.audit import write_audit
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import (
    PLAYGROUND_CHANNEL,
    Channel,
    Contact,
    Conversation,
    ConversationFlag,
    CrmContact,
    Handoff,
    Message,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.modules.conversations.schemas import MessageOut
from app.modules.conversations.service import _message_out, contact_out
from app.modules.inbox import schemas
from app.modules.orgs.deps import OrgContext
from app.realtime.hub import conv_topic, hub, inbox_topic
from app.webhooks.dispatch import emit_event
from app.webhooks.events import emit_conversation_ended
from app.workflows.schemas import ResumeWorkflowRequest
from app.workflows.service import resume_workflow_run_unchecked

log = get_logger("inbox")

_CHANNEL_TYPES = ("telegram", "whatsapp", "instagram", "facebook", "slack", "discord")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


async def _get_conversation(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> Conversation:
    conv = await session.get(Conversation, cid)
    if conv is None or conv.organization_id != ctx.org.id:
        raise AppError("inbox.not_found", "Conversation not found.", 404)
    return conv


async def _latest_handoff(session: AsyncSession, conversation_id: uuid.UUID) -> Handoff | None:
    stmt = (
        select(Handoff)
        .where(Handoff.conversation_id == conversation_id, Handoff.requested_by != "tool")
        .order_by(Handoff.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _handoff_out(handoff: Handoff | None) -> schemas.HandoffOut | None:
    if handoff is None:
        return None
    return schemas.HandoffOut(
        id=handoff.id,
        status=handoff.status,
        requested_by=handoff.requested_by,
        reason=handoff.reason,
        assigned_to=handoff.assigned_to,
        notes=handoff.notes,
        tags=handoff.tags,
        created_at=handoff.created_at,
        resolved_at=handoff.resolved_at,
    )


async def _message_count(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    return int((await session.execute(stmt)).scalar_one())


def _contact_out(
    contact: Contact | None, person: CrmContact | None = None
) -> schemas.ContactOut | None:
    """The name to show. A CRM link wins: a customer recognised by phone on a channel
    they've never used before should still appear as themselves, not as a raw handle."""
    return contact_out(contact, person)


async def _contacts_by_id(
    session: AsyncSession, convs: Sequence[Conversation]
) -> dict[uuid.UUID, Contact]:
    """One query for the whole page's contacts, rather than one per row."""
    ids = {c.contact_id for c in convs if c.contact_id is not None}
    if not ids:
        return {}
    stmt = select(Contact).where(Contact.id.in_(ids))
    return {c.id: c for c in (await session.execute(stmt)).scalars().all()}


async def _item_out(
    session: AsyncSession, conv: Conversation, contact: Contact | None = None
) -> schemas.InboxItemOut:
    if contact is None and conv.contact_id is not None:
        contact = await session.get(Contact, conv.contact_id)
    person = (
        await session.get(CrmContact, contact.crm_contact_id)
        if contact is not None and contact.crm_contact_id is not None
        else None
    )
    return schemas.InboxItemOut(
        attention_level=conv.attention_level,
        id=conv.id,
        agent_id=conv.agent_id,
        channel=conv.channel,
        status=conv.status,
        title=conv.title,
        channel_user_id=conv.channel_user_id,
        message_count=await _message_count(session, conv.id),
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
        handoff=_handoff_out(await _latest_handoff(session, conv.id)),
        contact=_contact_out(contact, person),
    )


# ── Queue + detail ───────────────────────────────────────────────────────────────
async def list_conversations(
    session: AsyncSession,
    ctx: OrgContext,
    status_filter: str | None,
    channel_filter: str | None = None,
    include_bot: bool = False,
) -> list[schemas.InboxItemOut]:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    # The queue is conversations that have at least one handoff record — i.e. where the bot
    # is paused and a human is expected — **plus** playground sessions.
    #
    # Playground is the exception because it can never satisfy the handoff rule: a test chat
    # is never escalated, so it would be permanently invisible here while showing up under
    # Conversations, which reads as the inbox dropping messages. It is the operator's own
    # transcript and nobody is waiting on it, so it is listed for reading, not for triage.
    #
    # `include_bot` (the "All chats" toggle) drops the handoff rule so bot-handled chats show up too, for
    # triage. It never widens the org scope: the same INBOX_HANDLE gate and organization filter apply.
    # A tool-approval request (requested_by "tool") is not a visitor asking for a person: it has its own list.
    handoff_ids = select(Handoff.conversation_id).where(
        Handoff.organization_id == ctx.org.id, Handoff.requested_by != "tool"
    )
    stmt = (
        select(Conversation)
        .where(Conversation.organization_id == ctx.org.id)
        .order_by(func.coalesce(Conversation.last_message_at, Conversation.created_at).desc())
    )
    if not include_bot:
        stmt = stmt.where(
            or_(
                Conversation.id.in_(handoff_ids),
                Conversation.channel == PLAYGROUND_CHANNEL,
            )
        )
    if status_filter:
        stmt = stmt.where(Conversation.status == status_filter)
    if channel_filter:
        # Server-side so the channel tabs page independently instead of slicing one
        # client-held list.
        stmt = stmt.where(Conversation.channel == channel_filter)
    convs = (await session.execute(stmt)).scalars().all()
    contacts = await _contacts_by_id(session, convs)
    return [await _item_out(session, c, contacts.get(c.contact_id) if c.contact_id else None) for c in convs]


async def get_detail(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> schemas.InboxDetail:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    base = await _item_out(session, conv)
    stmt = select(Message).where(Message.conversation_id == cid).order_by(Message.created_at.asc())
    msgs = list((await session.execute(stmt)).scalars().all())
    return schemas.InboxDetail(
        **base.model_dump(),
        messages=[_message_out(m) for m in msgs],
        send_window=await send_window(session, conv),
    )


# ── Operator actions ─────────────────────────────────────────────────────────────
async def _require_open_handoff(session: AsyncSession, conv: Conversation) -> Handoff:
    handoff = await _latest_handoff(session, conv.id)
    if handoff is None:
        raise AppError("inbox.no_handoff", "This conversation has no handoff.", 400)
    return handoff


async def _publish_inbox(session: AsyncSession, ctx: OrgContext, conv: Conversation, event_type: str) -> None:
    await hub.publish(
        inbox_topic(ctx.org.id),
        {"type": event_type, "conversation_id": str(conv.id), "status": conv.status},
    )


async def takeover(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> schemas.InboxItemOut:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    handoff = await _latest_handoff(session, conv.id)
    if handoff is None or handoff.status == "resolved":
        # A bot-handled chat opened from "All chats": taking it over opens the handoff record itself,
        # which also pauses the bot (trigger_handoff sets status=handoff).
        handoff = await trigger_handoff(session, conv, requested_by="operator", reason="operator_takeover")
    handoff.status = "assigned"
    handoff.assigned_to = ctx.user.id
    conv.assigned_to = ctx.user.id
    conv.status = "handoff"
    await session.flush()
    await _publish_inbox(session, ctx, conv, "handoff.assigned")
    return await _item_out(session, conv)


async def handback(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> schemas.InboxItemOut:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    handoff = await _require_open_handoff(session, conv)
    handoff.status = "resolved"
    handoff.resolved_at = _now()
    conv.status = "active"  # bot resumes
    conv.assigned_to = None
    await session.flush()
    await _publish_inbox(session, ctx, conv, "handoff.resolved")
    await emit_event(
        session, ctx.org.id, "handoff.resolved", {"conversation_id": str(conv.id)}, agent_id=conv.agent_id
    )
    await hub.publish(conv_topic(conv.id), {"type": "handback", "content": "You're back with the assistant."})
    return await _item_out(session, conv)


async def close(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> schemas.InboxItemOut:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    handoff = await _latest_handoff(session, conv.id)
    if handoff is not None and handoff.status != "resolved":
        handoff.status = "resolved"
        handoff.resolved_at = _now()
    conv.status = "closed"
    await session.flush()
    await _publish_inbox(session, ctx, conv, "conversation.closed")
    await emit_event(
        session, ctx.org.id, "conversation.closed", {"conversation_id": str(conv.id)}, agent_id=conv.agent_id
    )
    await emit_conversation_ended(session, conv, reason="closed_by_operator")
    if handoff is not None:
        await emit_event(
            session, ctx.org.id, "handoff.resolved", {"conversation_id": str(conv.id)}, agent_id=conv.agent_id
        )
    return await _item_out(session, conv)


async def assign(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID, user_id: uuid.UUID) -> schemas.InboxItemOut:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    handoff = await _require_open_handoff(session, conv)
    handoff.assigned_to = user_id
    handoff.status = "assigned"
    conv.assigned_to = user_id
    await session.flush()
    await _publish_inbox(session, ctx, conv, "handoff.assigned")
    return await _item_out(session, conv)


async def add_note(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID, text: str) -> schemas.HandoffOut:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    handoff = await _require_open_handoff(session, conv)
    handoff.notes = [*handoff.notes, {"by": str(ctx.user.id), "text": text, "at": _now().isoformat()}]
    await session.flush()
    return _handoff_out(handoff)  # type: ignore[return-value]


async def set_tags(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID, tags: list[str]) -> schemas.HandoffOut:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    handoff = await _require_open_handoff(session, conv)
    handoff.tags = tags
    await session.flush()
    return _handoff_out(handoff)  # type: ignore[return-value]


async def get_tags(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> list[str]:
    """Current tags — so callers that *add* one (macros) don't clobber the rest."""
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    handoff = await _require_open_handoff(session, conv)
    return list(handoff.tags)


async def _channel_row(session: AsyncSession, conv: Conversation) -> Channel | None:
    stmt = (
        select(Channel)
        .where(
            Channel.organization_id == conv.organization_id,
            Channel.agent_id == conv.agent_id,
            Channel.type == conv.channel,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def send_window(session: AsyncSession, conv: Conversation) -> schemas.SendWindowOut | None:
    """Platform-imposed limits on replying right now — null when there are none.

    Only WhatsApp has one today, but the shape is generic so the UI doesn't need to know
    which platforms are special.
    """
    if conv.channel != "whatsapp":
        return None
    channel = await _channel_row(session, conv)
    return schemas.SendWindowOut(
        open=whatsapp.window_open(conv.last_inbound_at),
        closes_at=whatsapp.window_closes_at(conv.last_inbound_at),
        templates=whatsapp.configured_templates(channel) if channel else [],
    )


async def operator_reply(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID, text: str) -> MessageOut:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    # Checked *before* persisting: a message we know can't be delivered shouldn't sit in
    # the transcript looking sent. Raises a typed error the UI turns into a template prompt.
    await _check_can_send(session, conv)
    msg = Message(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        role="assistant",
        content=text,
        provider="operator",
    )
    session.add(msg)
    conv.last_message_at = _now()
    await session.flush()

    await _deliver_to_user(session, conv, text)
    await hub.publish(
        conv_topic(conv.id),
        {"type": "operator_message", "content": text, "message_id": str(msg.id)},
    )
    await _publish_inbox(session, ctx, conv, "message.created")
    return _message_out(msg)


async def operator_image(
    session: AsyncSession, ctx: OrgContext, cid: uuid.UUID, raw: bytes, caption: str | None
) -> MessageOut:
    """The operator sends one photo into a conversation they have taken over (docs/26 M5)."""
    from app.media import service as media_service
    from app.media import urls as media_urls

    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    if conv.channel not in _CHANNEL_TYPES or not conv.channel_user_id:
        raise AppError("inbox.images_unsupported", "Photos can only be sent on WhatsApp, Instagram and Messenger.", 400)
    adapter = get_channel(conv.channel)
    channel = await _channel_row(session, conv)
    if adapter is None or channel is None or not adapter.supports_images:
        raise AppError("inbox.images_unsupported", "This channel cannot send photos.", 400)
    await _check_can_send(session, conv)
    asset = await media_service.save_asset(session, ctx.org.id, "operator", raw)
    try:
        delivered = await adapter.send_image(
            channel, conv.channel_user_id, media_urls.public_url(asset.id), (caption or "").strip() or None
        )
    except ChannelAuthRejected:
        await flag_token_rejected(session, channel)
        await session.commit()  # keep the flag: the AppError below rolls the request back
        raise AppError(
            "channel_needs_reconnect", "This channel's access token was rejected. Reconnect it, then send again.", 409
        ) from None
    if not delivered:
        raise AppError("inbox.image_rejected", "The platform refused that photo. Try another image.", 502)
    msg = Message(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        role="assistant",
        content=(caption or "").strip() or "[Photo]",
        provider="operator",
        attachments=[{"media_id": str(asset.id), "kind": "image", "direction": "out", "description": None}],
    )
    session.add(msg)
    conv.last_message_at = _now()
    await session.flush()
    await _publish_inbox(session, ctx, conv, "message.created")
    return _message_out(msg)


async def _check_can_send(session: AsyncSession, conv: Conversation) -> None:
    """Ask the adapter whether a free-form send is allowed before we commit to one."""
    if conv.channel not in _CHANNEL_TYPES or not conv.channel_user_id:
        return
    adapter = get_channel(conv.channel)
    channel = await _channel_row(session, conv)
    if adapter is None or channel is None:
        return
    adapter.check_can_send(channel, last_inbound_at=conv.last_inbound_at)


async def _deliver_to_user(session: AsyncSession, conv: Conversation, text: str) -> None:
    """Push the operator's reply to the end user's channel (widget receives it via the hub)."""
    if conv.channel not in _CHANNEL_TYPES or not conv.channel_user_id:
        return
    channel = await _channel_row(session, conv)
    adapter = get_channel(conv.channel)
    if channel is None or adapter is None:
        return
    try:
        await adapter.send(channel, conv.channel_user_id, text)
    except ChannelAuthRejected:
        await flag_token_rejected(session, channel)
    except AppError:
        # A typed refusal (e.g. Meta closed the window under us) is the operator's
        # problem to see, not a transport hiccup to log and forget.
        raise
    except Exception as exc:  # delivery failure shouldn't fail the operator's action
        log.warning("operator_reply_delivery_failed", conversation=str(conv.id), error=str(exc))


async def send_template(
    session: AsyncSession,
    ctx: OrgContext,
    cid: uuid.UUID,
    template: str,
    params: list[str],
) -> MessageOut:
    """Re-open a WhatsApp conversation with a pre-approved template."""
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    msg = await deliver_template(session, conv, template, params)
    await _publish_inbox(session, ctx, conv, "message.created")
    return _message_out(msg)


async def deliver_template(session: AsyncSession, conv: Conversation, template: str, params: list[str]) -> Message:
    """Send a pre-approved WhatsApp template into `conv` and record it. Used by the operator action above and by
    inbound hooks, which have no signed-in operator; each caller does its own permission check."""
    if conv.channel != "whatsapp":
        raise AppError("inbox.templates_unsupported", "Templates are a WhatsApp feature.", 400)
    channel = await _channel_row(session, conv)
    adapter = get_channel("whatsapp")
    if channel is None or not isinstance(adapter, whatsapp.WhatsAppChannel):
        raise AppError("inbox.channel_missing", "This agent has no WhatsApp channel.", 400)
    approved = whatsapp.configured_templates(channel)
    if approved and template not in approved:
        # Meta rejects unregistered names anyway; failing here says why.
        raise AppError(
            "inbox.template_not_approved",
            f"'{template}' isn't one of this channel's approved templates.",
            400,
            details={"approved": approved},
        )
    if not conv.channel_user_id:
        raise AppError("inbox.no_recipient", "This conversation has no WhatsApp recipient.", 400)

    try:
        await adapter.send_template(channel, conv.channel_user_id, template, params)
    except ChannelAuthRejected as exc:
        await flag_token_rejected(session, channel)
        await session.commit()  # keep the flag: the AppError below rolls the request back
        raise AppError(
            "channel_needs_reconnect",
            "WhatsApp rejected this channel's access token. Save a new token on the Channels tab, then send again.",
            409,
        ) from exc

    # Recorded as an operator message so the transcript shows what the customer received.
    body = f"[template: {template}]" + (f" {' · '.join(params)}" if params else "")
    msg = Message(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        role="assistant",
        content=body,
        provider="operator",
    )
    session.add(msg)
    conv.last_message_at = _now()
    await session.flush()
    return msg


async def push_to_visitor(session: AsyncSession, conv: Conversation, text: str, message_id: uuid.UUID) -> None:
    """Deliver an assistant message that is already saved (an action finished after the turn) to the visitor: out
    through the channel, and live to an open widget. A closed WhatsApp window is logged and skipped; the message
    stays in the transcript either way."""
    try:
        await _check_can_send(session, conv)
        await _deliver_to_user(session, conv, text)
    except AppError as exc:
        log.warning("push_to_visitor_skipped", conversation=str(conv.id), reason=exc.code)
    await hub.publish(conv_topic(conv.id), {"type": "operator_message", "content": text, "message_id": str(message_id)})


async def send_proactive_message(session: AsyncSession, conv: Conversation, text: str, *, provider: str) -> Message:
    """An assistant message nobody typed at the keyboard (an inbound hook, an automation). The channel adapter's
    own `check_can_send` runs first, so WhatsApp's 24-hour window is respected exactly as for an operator reply."""
    await _check_can_send(session, conv)
    msg = Message(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        role="assistant",
        content=text,
        provider=provider,
    )
    session.add(msg)
    conv.last_message_at = _now()
    await session.flush()
    await _deliver_to_user(session, conv, text)
    await hub.publish(conv_topic(conv.id), {"type": "operator_message", "content": text, "message_id": str(msg.id)})
    return msg


# ── Realtime ───────────────────────────────────────────────────────────────────────
async def inbox_stream(org_id: uuid.UUID) -> Any:
    """Subscribe to this org's inbox topic; yields events until cancelled."""
    return hub.subscribe(inbox_topic(org_id))


# ── Attention queue (docs/11 §L6, Phase E) ───────────────────────────────────────────────


async def attention_queue(
    session: AsyncSession, ctx: OrgContext, *, include_resolved: bool = False
) -> list[schemas.AttentionItemOut]:
    """Conversations a human has been asked to look at.

    **A separate query from `list_conversations`, not a filter on it.** That one is the handoff
    queue — it selects conversations that have a `Handoff` row, i.e. where the bot is paused.
    An attention conversation usually has no handoff at all, because the whole point is that
    the bot is still answering while someone decides whether to step in. Filtering the handoff
    queue by severity would therefore return an empty list on exactly the conversations this
    feature exists for.

    Ordered by severity then age, so `crisis` pins to the top and the oldest unattended
    conversation is next. An operator working top-down is working the right order.
    """
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    stmt = select(Conversation).where(
        Conversation.organization_id == ctx.org.id,
        Conversation.attention_level.is_not(None),
    )
    convs = list((await session.execute(stmt)).scalars().all())
    if not convs:
        return []

    flag_rows = (
        (
            await session.execute(
                select(ConversationFlag)
                .where(ConversationFlag.conversation_id.in_([c.id for c in convs]))
                .order_by(ConversationFlag.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    by_conv: dict[uuid.UUID, list[ConversationFlag]] = {}
    for row in flag_rows:
        if row.resolved_at is None or include_resolved:
            by_conv.setdefault(row.conversation_id, []).append(row)

    contacts = await _contacts_by_id(session, convs)
    items: list[schemas.AttentionItemOut] = []
    for conv in convs:
        base = await _item_out(session, conv, contacts.get(conv.contact_id) if conv.contact_id else None)
        recent = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.desc())
                    .limit(6)
                )
            )
            .scalars()
            .all()
        )
        items.append(
            schemas.AttentionItemOut(
                **base.model_dump(),
                flags=[
                    schemas.ConversationFlagOut(
                        id=f.id,
                        kind=f.kind,
                        severity=f.severity,
                        signals=list(f.signals or []),
                        created_at=f.created_at,
                        resolved_at=f.resolved_at,
                    )
                    for f in by_conv.get(conv.id, [])
                ],
                recent_messages=[MessageOut.model_validate(m, from_attributes=True) for m in reversed(recent)],
                # A handoff means a human owns it; until then the AI is still replying and the
                # UI has to say so unambiguously.
                bot_still_answering=conv.status != "handoff",
            )
        )

    items.sort(
        key=lambda i: (
            -attention.severity_rank(i.attention_level),
            i.last_message_at or i.created_at,
        )
    )
    return items


async def resolve_attention(
    session: AsyncSession, ctx: OrgContext, cid: uuid.UUID
) -> schemas.InboxItemOut:
    """Clear the flags on a conversation — the only way its severity goes down."""
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    conv = await _get_conversation(session, ctx, cid)
    await attention.resolve_flags(session, conv, user_id=ctx.user.id)
    return await _item_out(session, conv)


# ── Workflow approvals (docs/17 Phase 2 item 4) ─────────────────────────────────────────────
# A workflow paused on an Approval node is a `Handoff` row with `workflow_run_id` set instead
# of `conversation_id` (see `app.workflows.service`, which owns creating/resolving these). This
# is a separate query and pair of endpoints from the conversation-shaped inbox above — an
# `InboxItemOut` assumes a `Conversation` (channel, message_count, contact...), which a
# standalone workflow run may not have at all.


async def _workflow_approval_out(
    session: AsyncSession, handoff: Handoff, run: WorkflowRun
) -> schemas.WorkflowApprovalOut | None:
    version = await session.get(WorkflowVersion, run.workflow_version_id)
    workflow = await session.get(Workflow, version.workflow_id) if version is not None else None
    if workflow is None:
        # Data inconsistency (a workflow/version deleted out from under a live run) — skip
        # rather than surface a row the UI can't usefully act on.
        return None
    return schemas.WorkflowApprovalOut(
        handoff_id=handoff.id, workflow_run_id=run.id, workflow_id=workflow.id,
        workflow_name=workflow.name, message=handoff.reason, run_status=run.status,
        assigned_to=handoff.assigned_to, created_at=handoff.created_at,
    )


async def list_workflow_approvals(
    session: AsyncSession, ctx: OrgContext
) -> list[schemas.WorkflowApprovalOut]:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    stmt = (
        select(Handoff)
        .where(
            Handoff.organization_id == ctx.org.id,
            Handoff.workflow_run_id.is_not(None),
            Handoff.status != "resolved",
        )
        .order_by(Handoff.created_at.asc())
    )
    handoffs = (await session.execute(stmt)).scalars().all()
    if not handoffs:
        return []
    run_ids = [h.workflow_run_id for h in handoffs if h.workflow_run_id is not None]
    runs = {
        r.id: r
        for r in (
            await session.execute(select(WorkflowRun).where(WorkflowRun.id.in_(run_ids)))
        ).scalars().all()
    }
    items: list[schemas.WorkflowApprovalOut] = []
    for h in handoffs:
        run = runs.get(h.workflow_run_id) if h.workflow_run_id is not None else None
        if run is None:
            continue
        item = await _workflow_approval_out(session, h, run)
        if item is not None:
            items.append(item)
    return items


# A tool call that needs a person's yes is a `Handoff` with `requested_by="tool"` (docs/28 T4.4).


async def list_tool_approvals(
    session: AsyncSession, ctx: OrgContext, *, include_resolved: bool = False
) -> list[schemas.ToolApprovalOut]:
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    stmt = select(Handoff).where(Handoff.organization_id == ctx.org.id, Handoff.requested_by == "tool")
    if not include_resolved:
        stmt = stmt.where(Handoff.status != "resolved")
    handoffs = (await session.execute(stmt.order_by(Handoff.created_at.asc()).limit(200))).scalars().all()
    return [_tool_approval_out(h) for h in handoffs]


def _tool_approval_out(h: Handoff) -> schemas.ToolApprovalOut:
    p = h.payload or {}
    return schemas.ToolApprovalOut(
        handoff_id=h.id,
        conversation_id=h.conversation_id,
        tool=str(p.get("tool", "")),
        args=dict(p.get("args") or {}),
        status=h.status,
        decision=(h.notes[-1].get("decision") if h.notes and isinstance(h.notes[-1], dict) else None),
        created_at=h.created_at,
        resolved_at=h.resolved_at,
    )


async def decide_tool_approval(
    session: AsyncSession, ctx: OrgContext, handoff_id: uuid.UUID, decision: str
) -> schemas.ToolApprovalOut:
    """Approve (the stored call runs now) or reject (it never does). The agent then tells the customer how it went."""
    from app.tools import deferred

    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    handoff = await deferred.get_open_approval(session, ctx.org.id, handoff_id)
    handoff, run = await deferred.decide(session, handoff, approve=decision == "approved", decided_by=ctx.user.id)
    await write_audit(
        session, ctx.org.id, ctx.user.id, f"tool_approval.{decision}",
        target_type="handoff", target_id=str(handoff.id), meta={"tool": (handoff.payload or {}).get("tool")},
    )
    if run is not None:
        await session.commit()  # the outcome must be durable before a worker reads it
        deferred.enqueue_followup(run.id)
    return _tool_approval_out(handoff)


async def decide_workflow_approval(
    session: AsyncSession, ctx: OrgContext, handoff_id: uuid.UUID, decision: str
) -> schemas.WorkflowApprovalOut:
    """Approve or reject a paused workflow run. Requires `INBOX_HANDLE` — an operational inbox
    action, deliberately NOT `WORKFLOWS_PUBLISH` — and drives the exact same
    `resume_workflow_run_unchecked` the direct `/v1/workflow-runs/{id}/resume` API uses, so
    there is exactly one place that knows how to resume a paused run.
    """
    rbac.require_permission(ctx.role, rbac.INBOX_HANDLE)
    handoff = await session.get(Handoff, handoff_id)
    if handoff is None or handoff.organization_id != ctx.org.id or handoff.workflow_run_id is None:
        raise AppError("inbox.workflow_approval_not_found", "Workflow approval not found.", 404)
    if handoff.status == "resolved":
        raise AppError("inbox.workflow_approval_already_resolved", "This approval was already handled.", 400)
    run = await session.get(WorkflowRun, handoff.workflow_run_id)
    if run is None:
        raise AppError("inbox.workflow_approval_not_found", "Workflow approval not found.", 404)

    await resume_workflow_run_unchecked(session, run, ResumeWorkflowRequest(decision=decision))
    item = await _workflow_approval_out(session, handoff, run)
    assert item is not None  # the workflow/version existed a moment ago to get this far
    return item
