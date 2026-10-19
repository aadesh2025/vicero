"""Inbound hook management and the public receiver (docs/28 section 5.5, ADR-134)."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.core import rbac
from app.core.audit import write_audit
from app.core.clientip import resolve_client
from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.errors import AppError
from app.core.idempotency import claim
from app.core.logging import get_logger
from app.core.ratelimit import limiter
from app.models import Agent, InboundHook, InboundHookEvent, Organization
from app.modules.inbound_hooks import actions, schemas
from app.modules.orgs.deps import OrgContext
from app.webhooks.dispatch import sanitize_payload

log = get_logger("inbound_hooks")

MAX_BODY_BYTES = 256 * 1024
SIGNATURE_WINDOW_S = 300
IDEMPOTENCY_TTL_S = 24 * 3600
EVENT_RETENTION = dt.timedelta(days=30)
_MAX_STORED_PAYLOAD = 4096


# ── Management ───────────────────────────────────────────────────────────────────
def _out(hook: InboundHook, *, secret: str | None = None) -> schemas.InboundHookOut:
    return schemas.InboundHookOut(
        id=hook.id,
        agent_id=hook.agent_id,
        name=hook.name,
        action=hook.action,
        action_config=hook.action_config,
        payload_mapping=hook.payload_mapping,
        auth_mode=hook.auth_mode,
        enabled=hook.enabled,
        url_path=f"/v1/hooks/in/{hook.public_id}",
        last_called_at=hook.last_called_at,
        created_at=hook.created_at,
        secret=secret,
    )


async def _get_hook(session: AsyncSession, ctx: OrgContext, hook_id: uuid.UUID) -> InboundHook:
    hook = await session.get(InboundHook, hook_id)
    if hook is None or hook.organization_id != ctx.org.id:
        raise AppError("hooks.not_found", "Inbound hook not found.", 404)
    return hook


async def _get_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.organization_id != ctx.org.id:
        raise AppError("agents.not_found", "Agent not found.", 404)
    return agent


async def create_hook(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.CreateInboundHookRequest
) -> schemas.InboundHookOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_inbound_hook_slot(session, ctx.org)
    await _get_agent(session, ctx, agent_id)
    await actions.validate_config(session, ctx.org.id, data.action, data.action_config, data.payload_mapping)
    secret = secrets.token_urlsafe(32)
    hook = InboundHook(
        organization_id=ctx.org.id,
        agent_id=agent_id,
        public_id=secrets.token_urlsafe(24),
        name=data.name.strip(),
        action=data.action,
        action_config=data.action_config,
        payload_mapping=data.payload_mapping,
        auth_mode=data.auth_mode,
        secret_enc=encrypt(secret),
        enabled=data.enabled,
        created_by=ctx.user.id,
    )
    session.add(hook)
    await session.flush()
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "inbound_hook.created",
        target_type="inbound_hook",
        target_id=str(hook.id),
        meta={"action": data.action, "auth_mode": data.auth_mode},
    )
    return _out(hook, secret=secret)


async def list_hooks(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> list[schemas.InboundHookOut]:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await _get_agent(session, ctx, agent_id)
    stmt = (
        select(InboundHook)
        .where(InboundHook.organization_id == ctx.org.id, InboundHook.agent_id == agent_id)
        .order_by(InboundHook.created_at.desc())
    )
    return [_out(h) for h in (await session.execute(stmt)).scalars().all()]


async def update_hook(
    session: AsyncSession, ctx: OrgContext, hook_id: uuid.UUID, data: schemas.UpdateInboundHookRequest
) -> schemas.InboundHookOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    hook = await _get_hook(session, ctx, hook_id)
    config = data.action_config if data.action_config is not None else hook.action_config
    mapping = data.payload_mapping if data.payload_mapping is not None else hook.payload_mapping
    if data.action_config is not None or data.payload_mapping is not None:
        await actions.validate_config(session, ctx.org.id, hook.action, config, mapping)
        hook.action_config, hook.payload_mapping = config, mapping
    if data.name is not None:
        hook.name = data.name.strip()
    if data.enabled is not None:
        hook.enabled = data.enabled
    await session.flush()
    await write_audit(
        session, ctx.org.id, ctx.user.id, "inbound_hook.updated", target_type="inbound_hook", target_id=str(hook.id)
    )
    return _out(hook)


async def delete_hook(session: AsyncSession, ctx: OrgContext, hook_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    hook = await _get_hook(session, ctx, hook_id)
    await write_audit(
        session, ctx.org.id, ctx.user.id, "inbound_hook.deleted", target_type="inbound_hook", target_id=str(hook.id)
    )
    await session.delete(hook)


async def rotate_secret(session: AsyncSession, ctx: OrgContext, hook_id: uuid.UUID) -> schemas.InboundHookOut:
    """A new secret, returned once. The old one stops working immediately."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    hook = await _get_hook(session, ctx, hook_id)
    secret = secrets.token_urlsafe(32)
    hook.secret_enc = encrypt(secret)
    await session.flush()
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "inbound_hook.secret_rotated",
        target_type="inbound_hook",
        target_id=str(hook.id),
    )
    return _out(hook, secret=secret)


async def list_events(
    session: AsyncSession, ctx: OrgContext, hook_id: uuid.UUID, limit: int = 20
) -> list[schemas.InboundHookEventOut]:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    hook = await _get_hook(session, ctx, hook_id)
    stmt = (
        select(InboundHookEvent)
        .where(InboundHookEvent.inbound_hook_id == hook.id)
        .order_by(InboundHookEvent.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return [
        schemas.InboundHookEventOut(
            id=e.id,
            status=e.status,
            http_status=e.http_status,
            latency_ms=e.latency_ms,
            payload_sanitized=e.payload_sanitized,
            error=e.error,
            created_at=e.created_at,
        )
        for e in (await session.execute(stmt)).scalars().all()
    ]


async def purge_old_events(session: AsyncSession) -> int:
    cutoff = dt.datetime.now(tz=dt.UTC) - EVENT_RETENTION
    result = await session.execute(delete(InboundHookEvent).where(InboundHookEvent.created_at < cutoff))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ── The public receiver ─────────────────────────────────────────────────────────
def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": None}}, status_code=status, headers=headers
    )


async def _read_body(request: Request) -> bytes | None:
    """The body, or None when it is over the cap. Never buffers more than the cap plus one chunk."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def sign_for_test(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    """The headers a caller sends (also how the docs and tests build a valid request)."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Vicero-Timestamp": ts, "X-Vicero-Signature": mac}


async def _authenticate(hook: InboundHook, request: Request, body: bytes) -> str | None:
    """None when the caller is who the hook expects, else a short reason (logged, never shown in detail)."""
    secret = decrypt(hook.secret_enc)
    if hook.auth_mode == "bearer":
        header = request.headers.get("authorization", "")
        presented = header[7:] if header.lower().startswith("bearer ") else ""
        return None if hmac.compare_digest(presented.encode(), secret.encode()) else "bad_bearer"
    ts_raw = request.headers.get("x-vicero-timestamp", "")
    signature = request.headers.get("x-vicero-signature", "").removeprefix("sha256=").strip().lower()
    if not ts_raw.isdigit() or not signature:
        return "missing_signature"
    if abs(time.time() - int(ts_raw)) > SIGNATURE_WINDOW_S:
        return "stale_timestamp"
    expected = hmac.new(secret.encode(), f"{ts_raw}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return "bad_signature"
    # A captured request replayed inside the window is still a replay.
    if not await claim(f"hooksig:{hook.id}:{signature}", SIGNATURE_WINDOW_S):
        return "replayed"
    return None


def _stored_payload(payload: Any) -> dict[str, Any]:
    clean = sanitize_payload(payload)
    text = json.dumps(clean, default=str)
    if len(text) <= _MAX_STORED_PAYLOAD:
        return clean if isinstance(clean, dict) else {"value": clean}
    keys = list(payload) if isinstance(payload, dict) else []
    return {"_truncated": True, "bytes": len(text), "keys": keys[:50]}


async def receive(session: AsyncSession, request: Request, public_id: str) -> JSONResponse:
    """POST /v1/hooks/in/{public_id}. Always answers with JSON; never raises, so the event row is committed."""
    started = time.perf_counter()
    hook = (await session.execute(select(InboundHook).where(InboundHook.public_id == public_id))).scalars().first()
    if hook is None or not hook.enabled:
        return _error(404, "hooks.not_found", "Not found.")

    ip = resolve_client(request).ip
    ok, retry = await limiter.hit(f"hookip:{hook.id}:{ip}", settings.inbound_hook_unauth_rate_per_minute, 60)
    if not ok:
        return _error(429, "rate_limited", "Too many requests.", {"Retry-After": str(retry)})

    body = await _read_body(request)
    if body is None:
        return _error(413, "hooks.too_large", f"The body is over {MAX_BODY_BYTES // 1024} KB.")

    reason = await _authenticate(hook, request, body)
    if reason is not None:
        log.warning("inbound_hook_rejected", hook=str(hook.id), reason=reason)
        return _error(401, "hooks.unauthorized", "Invalid credentials.")

    ok, retry = await limiter.hit(f"hook:{hook.id}", settings.inbound_hook_rate_per_minute, 60)
    if not ok:
        return _error(429, "rate_limited", "This hook is receiving too many requests.", {"Retry-After": str(retry)})

    org = await session.get(Organization, hook.organization_id)
    if org is None or not usage.inbound_hooks_allowed(org):
        return _error(402, "plan_limit", "Inbound hooks are not included in this plan.")

    async def record(status: str, http_status: int, payload: Any, error: str | None = None) -> None:
        hook.last_called_at = dt.datetime.now(tz=dt.UTC)
        session.add(
            InboundHookEvent(
                inbound_hook_id=hook.id,
                status=status,
                http_status=http_status,
                latency_ms=int((time.perf_counter() - started) * 1000),
                payload_sanitized=_stored_payload(payload) if payload is not None else None,
                error=error,
            )
        )
        await session.flush()

    key = request.headers.get("idempotency-key", "").strip()[:128]
    if key and not await claim(f"hookidem:{hook.id}:{key}", IDEMPOTENCY_TTL_S):
        await record("duplicate", 200, None)
        return JSONResponse({"status": "duplicate"}, status_code=200)

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        await record("error", 400, None, "body is not valid JSON")
        return _error(400, "hooks.bad_json", "The body must be JSON.")
    if not isinstance(payload, dict):
        await record("error", 400, None, "body is not a JSON object")
        return _error(400, "hooks.bad_json", "The body must be a JSON object.")

    try:
        mapped = actions.map_payload(payload, hook.payload_mapping)
        async with session.begin_nested():  # a failing action leaves nothing half-done
            result = await actions.run_action(session, hook, org, mapped)
    except actions.HookError as exc:
        await record("error", exc.status, payload, exc.code)
        return _error(exc.status, exc.code, exc.message)
    except Exception as exc:  # the caller gets a generic answer; the cause goes to the log
        log.exception("inbound_hook_failed", hook=str(hook.id), error=str(exc))
        await record("error", 500, payload, "internal_error")
        return _error(500, "internal_error", "Something went wrong.")
    await record("ok", 200, payload)
    return JSONResponse({"status": "ok", **result}, status_code=200)
