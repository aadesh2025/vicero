"""Inbound hook routes (docs/28 section 8): management under the agent, plus the public receiver."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.inbound_hooks import schemas, service
from app.modules.orgs.deps import OrgContext, current_org

router = APIRouter(tags=["inbound-hooks"])


@router.post(
    "/v1/agents/{agent_id}/inbound-hooks", response_model=schemas.InboundHookOut, status_code=status.HTTP_201_CREATED
)
async def create_hook(
    agent_id: uuid.UUID,
    data: schemas.CreateInboundHookRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.InboundHookOut:
    return await service.create_hook(session, ctx, agent_id, data)


@router.get("/v1/agents/{agent_id}/inbound-hooks", response_model=list[schemas.InboundHookOut])
async def list_hooks(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.InboundHookOut]:
    return await service.list_hooks(session, ctx, agent_id)


@router.patch("/v1/inbound-hooks/{hook_id}", response_model=schemas.InboundHookOut)
async def update_hook(
    hook_id: uuid.UUID,
    data: schemas.UpdateInboundHookRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.InboundHookOut:
    return await service.update_hook(session, ctx, hook_id, data)


@router.delete("/v1/inbound-hooks/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hook(
    hook_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> None:
    await service.delete_hook(session, ctx, hook_id)


@router.post("/v1/inbound-hooks/{hook_id}/rotate-secret", response_model=schemas.InboundHookOut)
async def rotate_secret(
    hook_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.InboundHookOut:
    return await service.rotate_secret(session, ctx, hook_id)


@router.get("/v1/inbound-hooks/{hook_id}/events", response_model=list[schemas.InboundHookEventOut])
async def list_events(
    hook_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.InboundHookEventOut]:
    return await service.list_events(session, ctx, hook_id, limit)


@router.post("/v1/hooks/in/{public_id}")
async def receive(public_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Public. Authenticated by the hook's own secret (HMAC + timestamp, or bearer), not by a user."""
    return await service.receive(session, request, public_id)
