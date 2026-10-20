"""Connection routes under /v1/connections (docs/28 §8)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.connections import schemas, service
from app.modules.orgs.deps import OrgContext, current_org

router = APIRouter(prefix="/v1/connections", tags=["connections"])


@router.get("", response_model=list[schemas.ConnectionOut])
async def list_connections(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> list[schemas.ConnectionOut]:
    return await service.list_connections(session, ctx)


@router.post("", response_model=schemas.ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    data: schemas.CreateConnectionRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ConnectionOut:
    return await service.create_connection(session, ctx, data)


@router.get("/{connection_id}", response_model=schemas.ConnectionOut)
async def get_connection(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ConnectionOut:
    return await service.get_connection(session, ctx, connection_id)


@router.patch("/{connection_id}", response_model=schemas.ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID,
    data: schemas.UpdateConnectionRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ConnectionOut:
    return await service.update_connection(session, ctx, connection_id, data)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> None:
    await service.delete_connection(session, ctx, connection_id)


@router.post("/{connection_id}/test", response_model=schemas.ConnectionTestResponse)
async def test_connection(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ConnectionTestResponse:
    return await service.test_connection(session, ctx, connection_id)
