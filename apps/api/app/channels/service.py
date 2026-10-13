"""Channel CRUD, secret handling, and inbound-message processing."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.channels import schemas
from app.channels.base import BaseChannel, InboundMessage, get_channel
from app.chat.inbound import InboundTurn
from app.contacts import resolve_contact
from app.core import rbac
from app.core.config import settings
from app.core.crypto import encrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Agent, AgentVersion, Channel, Contact, Conversation
from app.modules.orgs.deps import OrgContext

log = get_logger("channels.service")

_WEBHOOK_PATH = {
    "telegram": "webhook",
    "whatsapp": "webhook",
    "instagram": "webhook",
    "facebook": "webhook",
    "slack": "events",
    "discord": "interactions",
}


def _webhook_url(channel: Channel) -> str:
    path = _WEBHOOK_PATH.get(channel.type, "webhook")
    return f"{settings.api_base_url.rstrip('/')}/v1/channels/{channel.type}/{channel.id}/{path}"


def _mask(adapter: BaseChannel, config: dict[str, Any]) -> dict[str, Any]:
    masked = dict(config)
    for field_name in adapter.secret_fields:
        if masked.get(field_name):
            masked[field_name] = "••••set"
    return masked


def _encrypt_secrets(adapter: BaseChannel, config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config)
    for field_name in adapter.secret_fields:
        val = out.get(field_name)
        if val and not str(val).startswith("••••"):
            out[field_name] = encrypt(str(val))
    return out


#: Config key holding the provider-side id each Meta channel receives for (ADR-113).
_EXTERNAL_ID_KEY = {"whatsapp": "phone_number_id", "facebook": "page_id", "instagram": "ig_user_id"}


def external_id_from_config(channel_type: str, config: dict[str, Any]) -> str | None:
    key = _EXTERNAL_ID_KEY.get(channel_type)
    value = str(config.get(key) or "").strip()[:255] if key else ""
    return value or None


async def ensure_external_id_free(
    session: AsyncSession, channel_type: str, external_id: str, *, exclude_id: uuid.UUID | None = None
) -> None:
    """One phone number / page / Instagram account belongs to one channel (unique index). Say so as a 409
    instead of letting the index raise, and never reveal which workspace holds it."""
    stmt = select(Channel.id).where(Channel.type == channel_type, Channel.external_id == external_id)
    if exclude_id is not None:
        stmt = stmt.where(Channel.id != exclude_id)
    if (await session.execute(stmt.limit(1))).first() is not None:
        raise AppError(
            "channels.already_connected",
            "This account is already connected to another channel. Disconnect it there first, "
            "or contact support if it is yours.",
            409,
        )


def _channel_out(channel: Channel, adapter: BaseChannel) -> schemas.ChannelOut:
    return schemas.ChannelOut(
        id=channel.id,
        agent_id=channel.agent_id,
        type=channel.type,
        name=channel.name,
        enabled=channel.enabled,
        config=_mask(adapter, channel.config),
        webhook_url=_webhook_url(channel),
        webhook_secret=channel.webhook_secret,
        created_at=channel.created_at,
        connection_source=channel.connection_source,
        status=channel.status,
        external_id=channel.external_id,
        external_parent_id=channel.external_parent_id,
        token_expires_at=channel.token_expires_at,
        last_health_check_at=channel.last_health_check_at,
    )


async def _get_channel(session: AsyncSession, ctx: OrgContext, channel_id: uuid.UUID) -> Channel:
    channel = await session.get(Channel, channel_id)
    if channel is None or channel.organization_id != ctx.org.id:
        raise AppError("channels.not_found", "Channel not found.", 404)
    return channel


async def create_channel(
    session: AsyncSession, ctx: OrgContext, data: schemas.CreateChannelRequest
) -> schemas.ChannelOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    adapter = get_channel(data.type)
    if adapter is None:
        raise AppError("channels.unknown_type", f"Unknown channel type '{data.type}'.", 400)
    await usage.require_channel_allowed(session, ctx.org, data.type)
    agent = await session.get(Agent, data.agent_id)
    if agent is None or agent.organization_id != ctx.org.id or agent.deleted_at is not None:
        raise AppError("agents.not_found", "Agent not found.", 404)

    external_id = external_id_from_config(data.type, data.config)
    if external_id:
        await ensure_external_id_free(session, data.type, external_id)
    channel = Channel(
        organization_id=ctx.org.id,
        agent_id=data.agent_id,
        type=data.type,
        name=data.name or data.type.title(),
        enabled=False,
        external_id=external_id,
        connection_source="manual",
        status="active",
        config=_encrypt_secrets(adapter, data.config),
        webhook_secret=secrets.token_urlsafe(24),
        created_by=ctx.user.id,
    )
    session.add(channel)
    await session.flush()
    return _channel_out(channel, adapter)


async def list_channels(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID | None
) -> list[schemas.ChannelOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(Channel).where(Channel.organization_id == ctx.org.id).order_by(Channel.created_at.desc())
    if agent_id is not None:
        stmt = stmt.where(Channel.agent_id == agent_id)
    channels = (await session.execute(stmt)).scalars().all()
    return [_channel_out(c, get_channel(c.type) or BaseChannel()) for c in channels]


async def get_channel_out(session: AsyncSession, ctx: OrgContext, channel_id: uuid.UUID) -> schemas.ChannelOut:
    rbac.require_permission(ctx.role, rbac.READ)
    channel = await _get_channel(session, ctx, channel_id)
    return _channel_out(channel, get_channel(channel.type) or BaseChannel())


async def update_channel(
    session: AsyncSession, ctx: OrgContext, channel_id: uuid.UUID, data: schemas.UpdateChannelRequest
) -> schemas.ChannelOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    channel = await _get_channel(session, ctx, channel_id)
    if data.enabled:
        usage.require_verified_email_to_go_live(ctx.org, ctx.user)
        await usage.require_channel_allowed(session, ctx.org, channel.type)
    adapter = get_channel(channel.type) or BaseChannel()
    if data.name is not None:
        channel.name = data.name
    if data.enabled is not None:
        channel.enabled = data.enabled
    if data.config is not None:
        # Merge: keep untouched (masked) secrets, encrypt any newly provided ones.
        merged = dict(channel.config)
        for k, v in data.config.items():
            if k in adapter.secret_fields and isinstance(v, str) and v.startswith("••••"):
                continue  # unchanged masked secret
            merged[k] = v
        if channel.connection_source == "manual":
            external_id = external_id_from_config(channel.type, merged)
            if external_id != channel.external_id:
                if external_id:
                    await ensure_external_id_free(session, channel.type, external_id, exclude_id=channel.id)
                channel.external_id = external_id
        channel.config = _encrypt_secrets(adapter, merged)
    return _channel_out(channel, adapter)


async def delete_channel(session: AsyncSession, ctx: OrgContext, channel_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    channel = await _get_channel(session, ctx, channel_id)
    await session.delete(channel)


async def set_enabled(
    session: AsyncSession, ctx: OrgContext, channel_id: uuid.UUID, enabled: bool
) -> schemas.ChannelOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    channel = await _get_channel(session, ctx, channel_id)
    if enabled:
        usage.require_verified_email_to_go_live(ctx.org, ctx.user)
        await usage.require_channel_allowed(session, ctx.org, channel.type)
    channel.enabled = enabled
    adapter = get_channel(channel.type) or BaseChannel()
    if enabled:
        try:
            await adapter.on_enable(channel, api_base=settings.api_base_url)
        except Exception as exc:  # best-effort provider registration
            log.warning("channel_on_enable_failed", channel=str(channel.id), error=str(exc))
    return _channel_out(channel, adapter)


# ── Inbound processing (shared by all webhook endpoints) ────────────────────────────
async def get_channel_by_id(session: AsyncSession, channel_id: uuid.UUID, channel_type: str) -> Channel | None:
    channel = await session.get(Channel, channel_id)
    if channel is None or channel.type != channel_type:
        return None
    return channel


async def _live_version(session: AsyncSession, agent: Agent) -> AgentVersion | None:
    if agent.current_version_id is not None:
        v = await session.get(AgentVersion, agent.current_version_id)
        if v is not None:
            return v
    stmt = (
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _get_or_create_conversation(
    session: AsyncSession, channel: Channel, external_user_id: str, contact: Contact | None
) -> Conversation:
    stmt = (
        select(Conversation)
        .where(
            Conversation.organization_id == channel.organization_id,
            Conversation.agent_id == channel.agent_id,
            Conversation.channel == channel.type,
            Conversation.channel_user_id == external_user_id,
            Conversation.status != "closed",
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conv = (await session.execute(stmt)).scalar_one_or_none()
    if conv is not None:
        # Backfill: threads that started before this channel had contact resolution.
        if contact is not None and conv.contact_id is None:
            conv.contact_id = contact.id
        return conv
    conv = Conversation(
        organization_id=channel.organization_id,
        agent_id=channel.agent_id,
        channel=channel.type,
        channel_user_id=external_user_id,
        contact_id=contact.id if contact else None,
        status="active",
    )
    session.add(conv)
    await session.flush()
    return conv


async def process_inbound(
    session: AsyncSession, channel: Channel, adapter: BaseChannel, payload: dict[str, Any]
) -> tuple[InboundMessage | None, str | None]:
    """Parse → run the bot turn → return (message, reply_text|None). Caller delivers the reply."""
    msg = adapter.parse_inbound(channel, payload)
    if msg is None:
        return None, None
    if not channel.enabled:
        log.info("channel_inbound_ignored_disabled", channel=str(channel.id))
        return msg, None
    agent = await session.get(Agent, channel.agent_id)
    if agent is None or agent.deleted_at is not None:
        return msg, None
    version = await _live_version(session, agent)
    if version is None:
        return msg, None
    contact = await resolve_contact(session, channel, adapter, msg.external_user_id, msg.profile)
    conv = await _get_or_create_conversation(session, channel, msg.external_user_id, contact)
    turn = InboundTurn(session, agent, version, conv, msg.text)
    await turn.run()
    reply = None if turn.handed_off else ((turn.result.content or "").strip() or None)
    return msg, reply


async def run_and_send(session: AsyncSession, channel: Channel, adapter: BaseChannel, payload: dict[str, Any]) -> None:
    """process_inbound, then deliver the reply. Shared by every webhook entry point."""
    msg, reply = await process_inbound(session, channel, adapter, payload)
    if msg is not None and reply:
        try:
            await adapter.send(channel, msg.external_user_id, reply)
        except Exception as exc:
            # The inbound is already persisted; a provider-delivery failure must not
            # 500 the webhook (that makes Telegram/Meta re-deliver → duplicate turns).
            log.warning("channel_delivery_failed", channel_type=channel.type, error=str(exc))
