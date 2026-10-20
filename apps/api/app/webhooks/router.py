"""Outbound webhook routes under /v1/webhooks (docs/04 §Webhooks)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.orgs.deps import OrgContext, current_org
from app.webhooks import EVENT_CATALOG, schemas, service

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.get("/events", response_model=list[str])
async def event_catalog() -> list[str]:
    return list(EVENT_CATALOG)


@router.post("", response_model=schemas.WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    data: schemas.CreateWebhookRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.WebhookOut:
    return await service.create_webhook(session, ctx, data)


@router.get("", response_model=list[schemas.WebhookOut])
async def list_webhooks(
    agent_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.WebhookOut]:
    return await service.list_webhooks(session, ctx, agent_id)


@router.patch("/{endpoint_id}", response_model=schemas.WebhookOut)
async def update_webhook(
    endpoint_id: uuid.UUID,
    data: schemas.UpdateWebhookRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.WebhookOut:
    return await service.update_webhook(session, ctx, endpoint_id, data)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    endpoint_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> None:
    await service.delete_webhook(session, ctx, endpoint_id)


@router.get("/{endpoint_id}/deliveries", response_model=list[schemas.WebhookDeliveryOut])
async def list_deliveries(
    endpoint_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.WebhookDeliveryOut]:
    return await service.list_deliveries(session, ctx, endpoint_id)


@router.post("/{endpoint_id}/test", response_model=schemas.WebhookDeliveryOut)
async def test_webhook(
    endpoint_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.WebhookDeliveryOut:
    return await service.test_webhook(session, ctx, endpoint_id)
