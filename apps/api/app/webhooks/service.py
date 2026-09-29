"""Webhook endpoint CRUD, delivery log, and test-send."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.core import rbac
from app.core.audit import write_audit
from app.core.errors import AppError
from app.models import WebhookDelivery, WebhookEndpoint
from app.modules.orgs.deps import OrgContext
from app.webhooks import schemas
from app.webhooks.dispatch import deliver_delivery


def _out(ep: WebhookEndpoint, *, reveal_secret: bool = False) -> schemas.WebhookOut:
    return schemas.WebhookOut(
        id=ep.id,
        url=ep.url,
        events=ep.events,
        enabled=ep.enabled,
        secret=ep.secret if reveal_secret else ("••••set" if ep.secret else None),
        created_at=ep.created_at,
    )


async def _get(session: AsyncSession, ctx: OrgContext, endpoint_id: uuid.UUID) -> WebhookEndpoint:
    ep = await session.get(WebhookEndpoint, endpoint_id)
    if ep is None or ep.organization_id != ctx.org.id:
        raise AppError("webhooks.not_found", "Webhook endpoint not found.", 404)
    return ep


async def create_webhook(
    session: AsyncSession, ctx: OrgContext, data: schemas.CreateWebhookRequest
) -> schemas.WebhookOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_webhook_slot(session, ctx.org)
    ep = WebhookEndpoint(
        organization_id=ctx.org.id,
        url=data.url,
        events=data.events,
        secret=data.secret or secrets.token_urlsafe(24),
        enabled=True,
    )
    session.add(ep)
    await session.flush()
    await write_audit(session, ctx.org.id, ctx.user.id, "webhook.created", target_type="webhook", target_id=str(ep.id))
    return _out(ep, reveal_secret=True)  # secret shown once


async def list_webhooks(session: AsyncSession, ctx: OrgContext) -> list[schemas.WebhookOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.organization_id == ctx.org.id).order_by(
        WebhookEndpoint.created_at.desc()
    )
    return [_out(ep) for ep in (await session.execute(stmt)).scalars().all()]


async def update_webhook(
    session: AsyncSession, ctx: OrgContext, endpoint_id: uuid.UUID, data: schemas.UpdateWebhookRequest
) -> schemas.WebhookOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    ep = await _get(session, ctx, endpoint_id)
    if data.url is not None:
        ep.url = data.url
    if data.events is not None:
        ep.events = data.events
    if data.enabled is not None:
        ep.enabled = data.enabled
    if data.secret is not None and not data.secret.startswith("••••"):
        ep.secret = data.secret
    return _out(ep)


async def delete_webhook(session: AsyncSession, ctx: OrgContext, endpoint_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    ep = await _get(session, ctx, endpoint_id)
    await write_audit(session, ctx.org.id, ctx.user.id, "webhook.deleted", target_type="webhook", target_id=str(ep.id))
    await session.delete(ep)


async def list_deliveries(
    session: AsyncSession, ctx: OrgContext, endpoint_id: uuid.UUID
) -> list[schemas.WebhookDeliveryOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get(session, ctx, endpoint_id)
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(100)
    )
    return [
        schemas.WebhookDeliveryOut(
            id=d.id,
            event=d.event,
            status=d.status,
            attempts=d.attempts,
            response_status=d.response_status,
            next_retry_at=d.next_retry_at,
            created_at=d.created_at,
        )
        for d in (await session.execute(stmt)).scalars().all()
    ]


async def test_webhook(
    session: AsyncSession, ctx: OrgContext, endpoint_id: uuid.UUID
) -> schemas.WebhookDeliveryOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    ep = await _get(session, ctx, endpoint_id)
    delivery = WebhookDelivery(
        webhook_endpoint_id=ep.id,
        event="webhook.test",
        payload={"event": "webhook.test", "org_id": str(ctx.org.id), "data": {"ok": True}},
        status="pending",
    )
    session.add(delivery)
    await session.flush()
    await deliver_delivery(session, delivery.id)  # deliver inline for immediate feedback
    return schemas.WebhookDeliveryOut(
        id=delivery.id,
        event=delivery.event,
        status=delivery.status,
        attempts=delivery.attempts,
        response_status=delivery.response_status,
        next_retry_at=delivery.next_retry_at,
        created_at=delivery.created_at,
    )
