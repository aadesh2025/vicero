"""Channel routes: authenticated CRUD + public, signature-verified inbound webhooks."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import schemas, service
from app.channels.base import get_channel
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limit
from app.db.session import get_session
from app.modules.orgs.deps import OrgContext, current_org

log = get_logger("channels.router")

router = APIRouter(prefix="/v1/channels", tags=["channels"])

# Public inbound webhooks are unauthenticated (signature-verified): throttle per source IP.
_webhook_rl = Depends(rate_limit("channel_webhook", limit=120, window=60))


# ── Authenticated CRUD ──────────────────────────────────────────────────────────
@router.post("", response_model=schemas.ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(
    data: schemas.CreateChannelRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ChannelOut:
    return await service.create_channel(session, ctx, data)


@router.get("", response_model=list[schemas.ChannelOut])
async def list_channels(
    agent_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.ChannelOut]:
    return await service.list_channels(session, ctx, agent_id)


@router.get("/{channel_id}", response_model=schemas.ChannelOut)
async def get_channel_detail(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ChannelOut:
    return await service.get_channel_out(session, ctx, channel_id)


@router.patch("/{channel_id}", response_model=schemas.ChannelOut)
async def update_channel(
    channel_id: uuid.UUID,
    data: schemas.UpdateChannelRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ChannelOut:
    return await service.update_channel(session, ctx, channel_id, data)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> None:
    await service.delete_channel(session, ctx, channel_id)


@router.post("/{channel_id}/enable", response_model=schemas.ChannelOut)
async def enable_channel(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ChannelOut:
    return await service.set_enabled(session, ctx, channel_id, True)


@router.post("/{channel_id}/disable", response_model=schemas.ChannelOut)
async def disable_channel(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ChannelOut:
    return await service.set_enabled(session, ctx, channel_id, False)


# ── Public inbound webhooks (no dashboard auth; signature-verified) ────────────────
async def _load_verified(
    session: AsyncSession, channel_type: str, channel_id: uuid.UUID, request: Request
) -> tuple[Any, Any, dict[str, str], bytes, dict[str, Any]]:
    channel = await service.get_channel_by_id(session, channel_id, channel_type)
    if channel is None:
        raise AppError("channels.not_found", "Channel not found.", 404)
    adapter = get_channel(channel_type)
    if adapter is None:
        raise AppError("channels.unknown_type", "Unknown channel type.", 400)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    query = dict(request.query_params)
    if not await adapter.verify(channel, headers, body, query):
        raise AppError("channels.bad_signature", "Signature verification failed.", 401)
    payload: dict[str, Any] = json.loads(body) if body else {}
    return channel, adapter, headers, body, payload


async def _run_and_send(session: AsyncSession, channel: Any, adapter: Any, payload: dict[str, Any]) -> None:
    await service.run_and_send(session, channel, adapter, payload)


@router.post("/telegram/{channel_id}/webhook", dependencies=[_webhook_rl])
async def telegram_webhook(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    channel, adapter, _h, _b, payload = await _load_verified(session, "telegram", channel_id, request)
    await _run_and_send(session, channel, adapter, payload)
    return {"ok": True}


async def _meta_challenge(
    session: AsyncSession, channel_type: str, channel_id: uuid.UUID, request: Request
) -> PlainTextResponse:
    """Meta's GET subscription handshake — identical for WhatsApp, Messenger, Instagram."""
    channel = await service.get_channel_by_id(session, channel_id, channel_type)
    if channel is None:
        raise AppError("channels.not_found", "Channel not found.", 404)
    adapter = get_channel(channel_type)
    if adapter is None or not hasattr(adapter, "verify_challenge"):
        raise AppError("channels.unknown_type", "Unknown channel type.", 400)
    challenge = adapter.verify_challenge(channel, dict(request.query_params))
    if challenge is None:
        raise AppError("channels.verify_failed", "Verification token mismatch.", 403)
    return PlainTextResponse(challenge)


@router.get("/whatsapp/{channel_id}/webhook", dependencies=[_webhook_rl])
async def whatsapp_verify(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    return await _meta_challenge(session, "whatsapp", channel_id, request)


@router.post("/whatsapp/{channel_id}/webhook", dependencies=[_webhook_rl])
async def whatsapp_webhook(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    channel, adapter, _h, _b, payload = await _load_verified(session, "whatsapp", channel_id, request)
    await _run_and_send(session, channel, adapter, payload)
    return {"ok": True}


# Messenger + Instagram DMs: same Meta app, same envelope, different `object` type.
@router.get("/facebook/{channel_id}/webhook", dependencies=[_webhook_rl])
async def facebook_verify(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    return await _meta_challenge(session, "facebook", channel_id, request)


@router.post("/facebook/{channel_id}/webhook", dependencies=[_webhook_rl])
async def facebook_webhook(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    channel, adapter, _h, _b, payload = await _load_verified(session, "facebook", channel_id, request)
    await _run_and_send(session, channel, adapter, payload)
    return {"ok": True}


@router.get("/instagram/{channel_id}/webhook", dependencies=[_webhook_rl])
async def instagram_verify(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    return await _meta_challenge(session, "instagram", channel_id, request)


@router.post("/instagram/{channel_id}/webhook", dependencies=[_webhook_rl])
async def instagram_webhook(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    channel, adapter, _h, _b, payload = await _load_verified(session, "instagram", channel_id, request)
    await _run_and_send(session, channel, adapter, payload)
    return {"ok": True}


@router.post("/slack/{channel_id}/events", dependencies=[_webhook_rl])
async def slack_events(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    channel, adapter, _h, _b, payload = await _load_verified(session, "slack", channel_id, request)
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    await _run_and_send(session, channel, adapter, payload)
    return {"ok": True}


@router.post("/discord/{channel_id}/interactions", dependencies=[_webhook_rl])
async def discord_interactions(
    channel_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    channel, adapter, _h, _b, payload = await _load_verified(session, "discord", channel_id, request)
    if payload.get("type") == 1:  # PING
        return {"type": 1}
    _msg, reply = await service.process_inbound(session, channel, adapter, payload)
    # Discord replies inline in the interaction response (type 4 = CHANNEL_MESSAGE_WITH_SOURCE).
    content = reply or "…"
    return {"type": 4, "data": {"content": content}}
