"""One-click connect for WhatsApp, Messenger and Instagram (ADR-113, docs/26-META-ONE-CLICK-CONNECT.md).

The browser only ever holds a short-lived authorization *code* (WhatsApp) or a short-lived *user token*
(Facebook login); everything long-lived stays here. Nothing the browser says about *what* it is connecting
(waba_id, phone_number_id, page ids) is trusted: each is re-checked against Graph with the token we obtained
ourselves. All conflict checks run before the first Graph write, so a refused connect changes nothing.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.channels import meta_graph as graph
from app.channels import meta_session, schemas
from app.channels.base import BaseChannel, get_channel
from app.channels.meta_graph import MetaGraphError
from app.channels.service import _channel_out, _get_channel
from app.core import rbac
from app.core.audit import write_audit
from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Agent, Channel
from app.modules.orgs.deps import OrgContext
from app.webhooks.dispatch import emit_event

log = get_logger("channels.meta_connect")

#: Page subscription fields. The adapters act on `messages`; postbacks are subscribed so a later button
#: feature needs no re-connect (they are ignored today, like read receipts and echoes).
PAGE_SUBSCRIBED_FIELDS = "messages,messaging_postbacks"

_SECRET_CONFIG_KEYS = ("access_token", "page_access_token", "registration_pin")


# ── config ────────────────────────────────────────────────────────────────────────
def meta_enabled() -> bool:
    return bool(settings.meta_app_id and settings.meta_app_secret and settings.meta_verify_token)


def whatsapp_enabled() -> bool:
    return meta_enabled() and bool(settings.meta_embedded_signup_config_id)


class MetaConfigOut(BaseModel):
    enabled: bool
    whatsapp_enabled: bool
    app_id: str | None
    config_id: str | None
    graph_version: str
    #: False until Meta approves the app: the UI then warns that only listed testers can connect.
    app_live: bool


def public_config() -> MetaConfigOut:
    on = meta_enabled()
    return MetaConfigOut(
        enabled=on,
        whatsapp_enabled=whatsapp_enabled(),
        app_id=settings.meta_app_id if on else None,
        config_id=settings.meta_embedded_signup_config_id if whatsapp_enabled() else None,
        graph_version=settings.meta_graph_version,
        app_live=settings.meta_app_live,
    )


# ── request / response models ─────────────────────────────────────────────────────
class WhatsAppConnectRequest(BaseModel):
    agent_id: uuid.UUID
    code: str = Field(min_length=1, max_length=2048)
    waba_id: str = Field(min_length=1, max_length=64, pattern=r"^\d+$")
    phone_number_id: str = Field(min_length=1, max_length=64, pattern=r"^\d+$")


class PagesRequest(BaseModel):
    user_access_token: str = Field(min_length=1, max_length=4096)


class PageInstagram(BaseModel):
    id: str
    username: str | None = None


class PageOut(BaseModel):
    page_id: str
    name: str
    picture: str | None = None
    instagram: PageInstagram | None = None


class PagesOut(BaseModel):
    session_id: str
    pages: list[PageOut]


class FacebookConnectRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    agent_id: uuid.UUID
    page_ids: list[str] = Field(min_length=1, max_length=25)
    kinds: list[str] = Field(min_length=1, max_length=2)


# ── shared steps ──────────────────────────────────────────────────────────────────
async def _preflight(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, types: list[str]) -> Agent:
    """Permission, feature flag, plan/email gates and agent ownership — all before talking to Meta."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    if not meta_enabled():
        raise AppError("meta.not_configured", "One-click connect is not set up on this server.", 503)
    usage.require_verified_email_to_go_live(ctx.org, ctx.user)
    for channel_type in types:
        await usage.require_channel_allowed(session, ctx.org, channel_type)
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.organization_id != ctx.org.id or agent.deleted_at is not None:
        raise AppError("agents.not_found", "Agent not found.", 404)
    return agent


async def _existing(session: AsyncSession, ctx: OrgContext, channel_type: str, external_id: str) -> Channel | None:
    """The channel already holding this account: ours (reconnect/update) or a 409 if another workspace's."""
    row = (
        await session.execute(select(Channel).where(Channel.type == channel_type, Channel.external_id == external_id))
    ).scalar_one_or_none()
    if row is not None and row.organization_id != ctx.org.id:
        raise AppError(
            "channels.already_connected",
            "This account is already connected to another Vicero workspace. Disconnect it there first.",
            409,
        )
    return row


def _graph_failure(exc: MetaGraphError) -> AppError:
    log.warning("meta_graph_failed", status=exc.status, meta_code=exc.code, message=exc.message[:120])
    return exc.to_app_error()


async def _upsert(
    session: AsyncSession,
    ctx: OrgContext,
    *,
    existing: Channel | None,
    agent_id: uuid.UUID,
    channel_type: str,
    external_id: str,
    parent_id: str | None,
    name: str,
    config: dict[str, Any],
    token_expires_at: dt.datetime | None,
) -> Channel:
    if existing is None:
        # A disconnected channel of this agent/type is what the "Reconnect" button means: reuse it.
        existing = (
            await session.execute(
                select(Channel)
                .where(
                    Channel.organization_id == ctx.org.id,
                    Channel.agent_id == agent_id,
                    Channel.type == channel_type,
                    Channel.connection_source == "meta_oauth",
                    Channel.status == "disconnected",
                )
                .order_by(Channel.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    channel = existing
    if channel is None:
        channel = Channel(
            organization_id=ctx.org.id,
            agent_id=agent_id,
            type=channel_type,
            config=config,
            webhook_secret=secrets.token_urlsafe(24),
            created_by=ctx.user.id,
        )
        session.add(channel)
    else:
        channel.agent_id = agent_id
        channel.config = {**channel.config, **config}  # keeps operator-set keys such as `templates`
    channel.name = name[:255]
    channel.external_id = external_id
    channel.external_parent_id = parent_id
    channel.connection_source = "meta_oauth"
    channel.status = "active"
    channel.enabled = True
    channel.token_expires_at = token_expires_at
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:  # lost a race against another workspace connecting the same account
        raise AppError("channels.already_connected", "This account was just connected somewhere else.", 409) from exc
    return channel


def _out(channel: Channel) -> schemas.ChannelOut:
    return _channel_out(channel, get_channel(channel.type) or BaseChannel())


def _token_expiry(*candidates: Any) -> dt.datetime | None:
    """First usable of: seconds-from-now (`expires_in`) or a unix timestamp (`expires_at`); 0/None = never."""
    now = dt.datetime.now(tz=dt.UTC)
    for kind, value in candidates:
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        return now + dt.timedelta(seconds=value) if kind == "in" else dt.datetime.fromtimestamp(value, tz=dt.UTC)
    return None


# ── WhatsApp ──────────────────────────────────────────────────────────────────────
def _check_granular_scopes(info: dict[str, Any], waba_id: str) -> None:
    """If Meta lists which WABAs the token covers, ours must be one of them."""
    targets: set[str] = set()
    for scope in info.get("granular_scopes") or []:
        if isinstance(scope, dict) and str(scope.get("scope", "")).startswith("whatsapp_business"):
            targets.update(str(t) for t in scope.get("target_ids") or [])
    if targets and waba_id not in targets:
        raise AppError("meta.waba_not_authorized", "That WhatsApp Business Account was not authorized.", 403)


async def connect_whatsapp(session: AsyncSession, ctx: OrgContext, data: WhatsAppConnectRequest) -> schemas.ChannelOut:
    await _preflight(session, ctx, data.agent_id, ["whatsapp"])
    if not whatsapp_enabled():
        raise AppError("meta.not_configured", "WhatsApp sign-up is not set up on this server.", 503)
    existing = await _existing(session, ctx, "whatsapp", data.phone_number_id)

    pin = ""
    try:
        token = str((await graph.exchange_code(data.code)).get("access_token") or "")
        if not token:
            raise AppError("meta.exchange_failed", "Meta did not return an access token.", 400)
        info = await graph.debug_token(token)
        if info.get("is_valid") is False or str(info.get("app_id") or settings.meta_app_id) != settings.meta_app_id:
            raise AppError("meta.token_invalid", "Meta did not accept this authorization. Try again.", 400)
        _check_granular_scopes(info, data.waba_id)

        # The browser said "this number is on this WABA". Ask Meta, with the token we hold.
        numbers = await graph.call(
            "GET",
            f"/{data.waba_id}/phone_numbers",
            token=token,
            params={"fields": "id,display_phone_number,verified_name,status", "limit": 200},
        )
        match = next((n for n in numbers.get("data") or [] if str(n.get("id")) == data.phone_number_id), None)
        if match is None:
            raise AppError(
                "meta.phone_not_in_waba",
                "That phone number is not part of the WhatsApp Business Account you authorized.",
                403,
            )

        await graph.call("POST", f"/{data.waba_id}/subscribed_apps", token=token)

        if existing is not None and existing.config.get("registration_pin"):
            try:
                pin = decrypt(str(existing.config["registration_pin"]))
            except Exception:
                pin = ""
        if str(match.get("status") or "").upper() != "CONNECTED":
            pin = pin or f"{secrets.randbelow(10**6):06d}"
            await graph.call(
                "POST",
                f"/{data.phone_number_id}/register",
                token=token,
                data={"messaging_product": "whatsapp", "pin": pin},
            )
    except MetaGraphError as exc:
        raise _graph_failure(exc) from exc

    display = str(match.get("display_phone_number") or "")
    verified = str(match.get("verified_name") or "")
    config: dict[str, Any] = {
        "phone_number_id": data.phone_number_id,
        "waba_id": data.waba_id,
        "access_token": encrypt(token),
        "display_phone_number": display,
        "verified_name": verified,
    }
    if pin:
        config["registration_pin"] = encrypt(pin)
    if info.get("user_id"):
        config["meta_user_id"] = str(info["user_id"])
    channel = await _upsert(
        session,
        ctx,
        existing=existing,
        agent_id=data.agent_id,
        channel_type="whatsapp",
        external_id=data.phone_number_id,
        parent_id=data.waba_id,
        name=verified or display or "WhatsApp",
        config=config,
        token_expires_at=_token_expiry(("at", info.get("expires_at"))),
    )
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "channel.meta_connected",
        target_type="channel",
        target_id=str(channel.id),
        meta={"type": "whatsapp", "external_id": data.phone_number_id},
    )
    return _out(channel)


# ── Messenger + Instagram ─────────────────────────────────────────────────────────
async def list_pages(session: AsyncSession, ctx: OrgContext, data: PagesRequest) -> PagesOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    if not meta_enabled():
        raise AppError("meta.not_configured", "One-click connect is not set up on this server.", 503)
    try:
        long_lived = str((await graph.exchange_long_lived(data.user_access_token)).get("access_token") or "")
        if not long_lived:
            raise AppError("meta.exchange_failed", "Meta did not return an access token.", 400)
        me = await graph.call("GET", "/me", token=long_lived, params={"fields": "id"})
        stored: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(5):  # 500 pages is far beyond any real customer
            params: dict[str, Any] = {
                "fields": "id,name,access_token,picture{url},instagram_business_account{id,username}",
                "limit": 100,
            }
            if after:
                params["after"] = after
            body = await graph.call("GET", "/me/accounts", token=long_lived, params=params)
            for p in body.get("data") or []:
                if not isinstance(p, dict) or not p.get("id") or not p.get("access_token"):
                    continue
                ig = p.get("instagram_business_account")
                pic = p.get("picture")
                picture = ((pic.get("data") or {}).get("url")) if isinstance(pic, dict) else None
                stored.append(
                    {
                        "page_id": str(p["id"]),
                        "name": str(p.get("name") or p["id"]),
                        "picture": picture,
                        "instagram": {"id": str(ig["id"]), "username": ig.get("username")}
                        if isinstance(ig, dict) and ig.get("id")
                        else None,
                        "access_token": str(p["access_token"]),
                    }
                )
            raw_paging = body.get("paging")
            paging: dict[str, Any] = raw_paging if isinstance(raw_paging, dict) else {}
            after = ((paging.get("cursors") or {}).get("after")) if paging.get("next") else None
            if not after:
                break
    except MetaGraphError as exc:
        raise _graph_failure(exc) from exc

    session_id = await meta_session.create(ctx.org.id, ctx.user.id, stored, str(me.get("id") or ""))
    await write_audit(session, ctx.org.id, ctx.user.id, "channel.meta_pages_listed", meta={"pages": len(stored)})
    return PagesOut(
        session_id=session_id,
        pages=[
            PageOut(
                page_id=p["page_id"],
                name=p["name"],
                picture=p["picture"],
                instagram=PageInstagram(**p["instagram"]) if p["instagram"] else None,
            )
            for p in stored
        ],
    )


async def connect_pages(
    session: AsyncSession, ctx: OrgContext, data: FacebookConnectRequest
) -> list[schemas.ChannelOut]:
    if bad := [k for k in data.kinds if k not in ("messenger", "instagram")]:
        raise AppError("validation_error", f"Unknown kind: {bad[0]}", 422)
    types = sorted({"facebook" if k == "messenger" else "instagram" for k in data.kinds})
    await _preflight(session, ctx, data.agent_id, types)

    stored = await meta_session.load(data.session_id, ctx.org.id, ctx.user.id)
    if stored is None:
        raise AppError("meta.session_expired", "That Facebook login expired. Please connect again.", 410)
    by_id = {p["page_id"]: p for p in stored["pages"]}

    # Plan + conflict checks first: nothing is written to Meta or the DB if any item is refused.
    plan: list[tuple[dict[str, Any], str, str, Channel | None]] = []
    for page_id in dict.fromkeys(data.page_ids):
        page = by_id.get(page_id)
        if page is None:
            raise AppError("meta.page_not_available", "One of the selected Pages is not available to you.", 400)
        for kind in dict.fromkeys(data.kinds):
            if kind == "messenger":
                ctype, ext = "facebook", page_id
            else:
                if not page.get("instagram"):
                    raise AppError(
                        "meta.no_instagram",
                        f"“{page['name']}” has no Instagram professional account linked to it.",
                        400,
                    )
                ctype, ext = "instagram", str(page["instagram"]["id"])
            plan.append((page, ctype, ext, await _existing(session, ctx, ctype, ext)))

    try:
        for page in {p["page_id"]: p for p, *_ in plan}.values():
            await graph.call(
                "POST",
                f"/{page['page_id']}/subscribed_apps",
                token=page["access_token"],
                data={"subscribed_fields": PAGE_SUBSCRIBED_FIELDS},
            )
    except MetaGraphError as exc:
        raise _graph_failure(exc) from exc

    channels: list[Channel] = []
    for page, ctype, ext, existing in plan:
        config: dict[str, Any] = {
            "page_id": page["page_id"],
            "page_name": page["name"],
            "page_access_token": encrypt(page["access_token"]),
        }
        if stored.get("meta_user_id"):
            config["meta_user_id"] = stored["meta_user_id"]
        name = page["name"]
        if ctype == "instagram":
            config["ig_user_id"] = ext
            username = page["instagram"].get("username")
            if username:
                config["instagram_username"] = username
                name = f"@{username}"
        channel = await _upsert(
            session,
            ctx,
            existing=existing,
            agent_id=data.agent_id,
            channel_type=ctype,
            external_id=ext,
            parent_id=page["page_id"],
            name=name,
            config=config,
            token_expires_at=None,
        )
        channels.append(channel)
        await write_audit(
            session,
            ctx.org.id,
            ctx.user.id,
            "channel.meta_connected",
            target_type="channel",
            target_id=str(channel.id),
            meta={"type": ctype, "external_id": ext},
        )
    await meta_session.discard(data.session_id)
    return [_out(c) for c in channels]


# ── disconnect / deauthorize ──────────────────────────────────────────────────────
def wipe_credentials(channel: Channel) -> None:
    channel.config = {k: v for k, v in channel.config.items() if k not in _SECRET_CONFIG_KEYS}
    channel.token_expires_at = None


async def disconnect(session: AsyncSession, ctx: OrgContext, channel_id: uuid.UUID) -> schemas.ChannelOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    channel = await _get_channel(session, ctx, channel_id)
    if channel.connection_source != "meta_oauth":
        raise AppError(
            "channels.not_meta_connected", "This channel was not connected with one click. Delete it instead.", 400
        )
    adapter = get_channel(channel.type) or BaseChannel()
    parent = channel.external_parent_id
    if parent and channel.status == "active":
        # The app is subscribed per Page / per WABA, not per channel: keep it while a sibling still needs it.
        sibling = (
            await session.execute(
                select(Channel.id)
                .where(
                    Channel.external_parent_id == parent,
                    Channel.id != channel.id,
                    Channel.connection_source == "meta_oauth",
                    Channel.status == "active",
                )
                .limit(1)
            )
        ).first()
        token_field = "access_token" if channel.type == "whatsapp" else "page_access_token"
        token = adapter.secret(channel, token_field)
        if sibling is None and token:
            try:
                await graph.call("DELETE", f"/{parent}/subscribed_apps", token=token)
            except MetaGraphError as exc:  # token may already be dead; the local disconnect still stands
                log.warning("meta_unsubscribe_failed", channel=str(channel.id), status=exc.status, meta_code=exc.code)
    wipe_credentials(channel)
    channel.status = "disconnected"
    channel.enabled = False
    channel.external_id = None  # frees the account for a reconnect, here or in another workspace
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "channel.meta_disconnected",
        target_type="channel",
        target_id=str(channel.id),
        meta={"type": channel.type},
    )
    return _out(channel)


async def mark_needs_reconnect(session: AsyncSession, channel: Channel, reason: str) -> bool:
    """Active -> needs_reconnect, exactly once per transition. Returns whether it transitioned."""
    if channel.status != "active":
        return False
    if reason == "deauthorized":
        wipe_credentials(channel)
        channel.enabled = False
    channel.status = "needs_reconnect"
    await write_audit(
        session,
        channel.organization_id,
        None,
        "channel.meta_needs_reconnect",
        target_type="channel",
        target_id=str(channel.id),
        meta={"type": channel.type, "reason": reason},
    )
    await emit_event(
        session,
        channel.organization_id,
        "channel.needs_reconnect",
        {"channel_id": str(channel.id), "type": channel.type, "reason": reason},
    )
    return True


async def handle_deauthorize(session: AsyncSession, meta_user_id: str) -> int:
    """Meta's Deauthorize callback: this Facebook user removed the app, so every channel they connected is dead."""
    channels = (
        (
            await session.execute(
                select(Channel).where(
                    Channel.connection_source == "meta_oauth", Channel.config["meta_user_id"].astext == meta_user_id
                )
            )
        )
        .scalars()
        .all()
    )
    return sum([await mark_needs_reconnect(session, c, "deauthorized") for c in channels])
