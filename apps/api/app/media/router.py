"""Image bytes over HTTP: authenticated for the dashboard, signed for Meta (docs/26 §6, §8)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session
from app.media import service, urls
from app.media.store import get_store
from app.models import MediaAsset
from app.modules.orgs.deps import OrgContext, current_org

router = APIRouter(prefix="/v1/media", tags=["media"])

_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}
# The dashboard (another origin) shows product photos in plain <img> tags through the signed link, and the
# API's default `Cross-Origin-Resource-Policy: same-origin` would block that. Signed links are meant to be embedded.
_PUBLIC_HEADERS = {**_HEADERS, "Cross-Origin-Resource-Policy": "cross-origin"}


async def _serve(asset: MediaAsset | None, *, public: bool = False) -> Response:
    if asset is None:
        raise AppError("media.not_found", "Image not found.", 404)
    try:
        data = await get_store().open(asset.storage_key)
    except (FileNotFoundError, ValueError):
        raise AppError("media.not_found", "Image not found.", 404) from None
    return Response(content=data, media_type=asset.mime, headers=_PUBLIC_HEADERS if public else _HEADERS)


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
    return await _serve(await session.get(MediaAsset, media_id), public=True)


@router.get("/{media_id}")
async def get_media(
    media_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> Response:
    return await _serve(await service.get_org_asset(session, ctx.org.id, media_id))
