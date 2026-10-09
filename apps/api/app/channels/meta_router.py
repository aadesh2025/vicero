"""Meta one-click connect: authenticated endpoints (customers) and the public shared webhook (Meta)."""

from __future__ import annotations

import hmac
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import data_deletion, get_channel, meta_connect, meta_webhook, schemas
from app.channels.meta_signature import verify_signature
from app.core import rbac
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import limiter, rate_limit
from app.db.session import get_session
from app.modules.orgs.deps import OrgContext, current_org
from app.worker.tasks import enqueue_meta_inbound

log = get_logger("channels.meta_router")

# ── customers (authenticated, org-scoped) ──────────────────────────────────────────
connect_router = APIRouter(prefix="/v1/channels", tags=["channels"])


@connect_router.get("/meta/config", response_model=meta_connect.MetaConfigOut)
async def meta_config(ctx: OrgContext = Depends(current_org)) -> meta_connect.MetaConfigOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return meta_connect.public_config()


@connect_router.post("/meta/whatsapp/connect", response_model=schemas.ChannelOut)
async def connect_whatsapp(
    data: meta_connect.WhatsAppConnectRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ChannelOut:
    return await meta_connect.connect_whatsapp(session, ctx, data)


@connect_router.post("/meta/facebook/pages", response_model=meta_connect.PagesOut)
async def facebook_pages(
    data: meta_connect.PagesRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> meta_connect.PagesOut:
    return await meta_connect.list_pages(session, ctx, data)


@connect_router.post("/meta/facebook/connect", response_model=list[schemas.ChannelOut])
async def facebook_connect(
    data: meta_connect.FacebookConnectRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.ChannelOut]:
    return await meta_connect.connect_pages(session, ctx, data)


@connect_router.post("/{channel_id}/meta/disconnect", response_model=schemas.ChannelOut)
async def meta_disconnect(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ChannelOut:
    return await meta_connect.disconnect(session, ctx, channel_id)


# ── Meta (public; every request is signature-verified) ─────────────────────────────
router = APIRouter(prefix="/api/meta", tags=["channels"])

# One shared URL means every tenant's traffic comes from Meta's few IPs, so this is far above the
# 120/min per-channel webhooks.
_webhook_rl = Depends(rate_limit("meta_webhook", limit=1200, window=60))
_callback_rl = Depends(rate_limit("meta_callback", limit=60, window=60))

_DEDUPE_WINDOW = 24 * 3600


@router.get("/webhook", dependencies=[_webhook_rl])
async def webhook_verify(request: Request) -> PlainTextResponse:
    """Meta's subscription handshake, checked against the single app-wide verify token."""
    expected = settings.meta_verify_token
    q = request.query_params
    given = q.get("hub.verify_token", "")
    if not expected or q.get("hub.mode") != "subscribe" or not hmac.compare_digest(expected, given):
        raise AppError("meta.verify_failed", "Verification token mismatch.", 403)
    return PlainTextResponse(q.get("hub.challenge", ""))


@router.post("/webhook", dependencies=[_webhook_rl])
async def webhook_receive(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    if not settings.meta_app_secret:
        raise AppError("meta.not_configured", "Meta webhook is not configured.", 503)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    # `verify_signature` with a non-empty secret is strict: a missing or wrong header fails.
    if not verify_signature(settings.meta_app_secret, headers, body):
        raise AppError("meta.bad_signature", "Signature verification failed.", 401)
    try:
        payload = json.loads(body)
    except ValueError:
        return {"ok": True}
    if not isinstance(payload, dict):
        return {"ok": True}

    for event in meta_webhook.split_events(payload):
        channel = await meta_webhook.find_channel(session, event.channel_type, event.external_id)
        if channel is None:
            continue  # not ours (yet): always 200, a 4xx/5xx would make Meta retry and eventually disable the app
        adapter = get_channel(channel.type)
        if adapter is None or adapter.parse_inbound(channel, event.payload) is None:
            continue  # echo, receipt, delivery status, non-text
        if event.message_id:
            key = f"meta:dedupe:{event.channel_type}:{event.message_id}"
            first, _ = await limiter.hit(key, 1, _DEDUPE_WINDOW)
            if not first:
                continue  # Meta retried a delivery we already took
        await _dispatch(session, channel.id, event.payload)
    return {"ok": True}


async def _dispatch(session: AsyncSession, channel_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Hand the turn to the worker so Meta gets its 200 fast; run it here if the broker is down."""
    try:
        enqueue_meta_inbound(str(channel_id), payload)
        return
    except Exception:
        log.exception("meta_inbound_enqueue_failed", channel=str(channel_id))
    try:
        await meta_webhook.handle_inbound(session, channel_id, payload)
    except Exception:
        log.exception("meta_inbound_inline_failed", channel=str(channel_id))


@router.post("/deauthorize", dependencies=[_callback_rl])
async def deauthorize(
    signed_request: Annotated[str, Form()], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Meta's Deauthorize callback: the user removed the app, so their connected channels go dead."""
    if not settings.meta_app_secret:
        raise AppError("meta.not_configured", "Deauthorize callback is not configured.", 503)
    payload = data_deletion.parse_signed_request(signed_request, settings.meta_app_secret)
    user_id = str(payload.get("user_id", "")) if payload else ""
    if not user_id:
        raise AppError("meta.bad_signature", "Invalid signed_request.", 400)
    affected = await meta_connect.handle_deauthorize(session, user_id)
    log.info("meta_deauthorized", channels=affected)
    return {"ok": True}
