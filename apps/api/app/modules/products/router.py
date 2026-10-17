"""Product catalog routes under /v1/products (docs/26 §6)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import get_session
from app.modules.orgs.deps import OrgContext, current_org
from app.modules.products import schemas, service

router = APIRouter(prefix="/v1/products", tags=["products"])


@router.get("", response_model=schemas.ProductListOut)
async def list_products(
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ProductListOut:
    return await service.list_products(session, ctx, query=query, limit=limit, offset=offset)


@router.post("", response_model=schemas.ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: schemas.CreateProductRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ProductOut:
    return await service.create_product(session, ctx, data)


@router.get("/{product_id}", response_model=schemas.ProductOut)
async def get_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ProductOut:
    return await service.get_product(session, ctx, product_id)


@router.patch("/{product_id}", response_model=schemas.ProductOut)
async def update_product(
    product_id: uuid.UUID,
    data: schemas.UpdateProductRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ProductOut:
    return await service.update_product(session, ctx, product_id, data)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> None:
    await service.delete_product(session, ctx, product_id)


@router.post("/{product_id}/images", response_model=schemas.ProductOut, status_code=status.HTTP_201_CREATED)
async def upload_image(
    product_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None, max_length=200),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ProductOut:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.media_max_upload_bytes + 64 * 1024:
        raise AppError("media.too_large", "That image is too large.", 413)
    # Read one byte past the cap so an oversized body is rejected without buffering all of it.
    raw = await file.read(settings.media_max_upload_bytes + 1)
    return await service.add_image(session, ctx, product_id, raw, alt_text)


@router.put("/{product_id}/images/order", response_model=schemas.ProductOut)
async def reorder_images(
    product_id: uuid.UUID,
    data: schemas.ReorderImagesRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ProductOut:
    return await service.reorder_images(session, ctx, product_id, data.image_ids)


@router.delete("/{product_id}/images/{image_id}", response_model=schemas.ProductOut)
async def delete_image(
    product_id: uuid.UUID,
    image_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.ProductOut:
    return await service.remove_image(session, ctx, product_id, image_id)
