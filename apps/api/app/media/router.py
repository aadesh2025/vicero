"""Image bytes over HTTP: authenticated for the dashboard, signed for Meta (docs/26 §6, §8)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session
from app.media import urls
from app.media.store import get_store
from app.models import MediaAsset
from app.modules.orgs.deps import OrgContext, current_org

router = APIRouter(prefix="/v1/media", tags=["media"])

_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}


async def _serve(asset: MediaAsset | None) -> Response:
    if asset is None:
        raise AppError("media.not_found", "Image not found.", 404)
    try:
        data = await get_store().open(asset.storage_key)
    except (FileNotFoundError, ValueError):
        raise AppError("media.not_found", "Image not found.", 404) from None
    return Response(content=data, media_type=asset.mime, headers=_HEADERS)


# Declared first: `/public/{id}` must not be shadowed by `/{id}`.
@router.get("/public/{media_id}")
async def get_public_media(
    media_id: uuid.UUID,
    exp: int = Query(),
    sig: str = Query(),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not urls.verify(media_id, exp, sig):
        raise AppError("media.not_found", "Image not found.", 404)
    return await _serve(await session.get(MediaAsset, media_id))


@router.get("/{media_id}")
async def get_media(
    media_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> Response:
    asset = (
        await session.execute(
            select(MediaAsset).where(MediaAsset.id == media_id, MediaAsset.organization_id == ctx.org.id)
        )
    ).scalar_one_or_none()
    return await _serve(asset)
