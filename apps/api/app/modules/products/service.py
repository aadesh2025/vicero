"""Product catalog: CRUD, photos, and the search the agent's catalog tools use (docs/26 §3.3, §5, §6)."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rbac
from app.core.audit import write_audit
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import limiter
from app.llm.registry import build_embedding_provider
from app.media import service as media_service
from app.media import urls as media_urls
from app.models import MediaAsset, Product, ProductImage
from app.modules.orgs.deps import OrgContext
from app.modules.products import schemas

log = get_logger("products")

MAX_IMAGES_PER_PRODUCT = 5
_UPLOADS_PER_MINUTE = 30
_RRF_K = 60


# ── presentation ──────────────────────────────────────────────────────────────
async def _images_for(session: AsyncSession, product_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ProductImage]]:
    if not product_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(ProductImage)
                .where(ProductImage.product_id.in_(product_ids))
                .order_by(ProductImage.position, ProductImage.id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, list[ProductImage]] = {}
    for row in rows:
        out.setdefault(row.product_id, []).append(row)
    return out


def _out(product: Product, images: list[ProductImage]) -> schemas.ProductOut:
    return schemas.ProductOut(
        id=product.id,
        name=product.name,
        sku=product.sku,
        price_minor=product.price_minor,
        currency=product.currency,
        description=product.description,
        attributes=dict(product.attributes or {}),
        in_stock=product.in_stock,
        url=product.url,
        is_active=product.is_active,
        images=[
            schemas.ProductImageOut(
                id=i.id,
                media_id=i.media_asset_id,
                url=media_urls.public_url(i.media_asset_id),
                position=i.position,
                alt_text=i.alt_text,
            )
            for i in images
        ],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


async def _present(session: AsyncSession, product: Product) -> schemas.ProductOut:
    return _out(product, (await _images_for(session, [product.id])).get(product.id, []))


async def _get_product(session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    product = (
        await session.execute(select(Product).where(Product.id == product_id, Product.organization_id == org_id))
    ).scalar_one_or_none()
    if product is None:
        raise AppError("products.not_found", "Product not found.", 404)
    return product


# ── embeddings ────────────────────────────────────────────────────────────────
def embedding_text(product: Product) -> str:
    attrs = ", ".join(f"{k}: {v}" for k, v in (product.attributes or {}).items())
    return "\n".join(p for p in (product.name, product.sku or "", attrs, product.description) if p)


async def embed_product(session: AsyncSession, product_id: uuid.UUID) -> bool:
    """Compute and store the product's embedding. Runs in the worker; False when it could not."""
    product = await session.get(Product, product_id)
    if product is None:
        return False
    try:
        embedder = build_embedding_provider(settings.embedding_provider, settings.embedding_model)
        product.embedding = (await embedder.embed([embedding_text(product)]))[0]
    except Exception as exc:  # the catalog still works on keyword search without it
        log.warning("product_embedding_failed", product_id=str(product_id), error=type(exc).__name__)
        return False
    return True


def _schedule_embedding(product_id: uuid.UUID) -> None:
    if not settings.product_embedding_enabled:
        return
    try:
        from app.worker.tasks import embed_product_task

        embed_product_task.apply_async(args=[str(product_id)], retry=False)
    except Exception as exc:  # a dead broker must not fail the owner's save
        log.warning("product_embedding_enqueue_failed", error=type(exc).__name__)


# ── CRUD ──────────────────────────────────────────────────────────────────────
async def list_products(
    session: AsyncSession, ctx: OrgContext, *, query: str | None, limit: int, offset: int
) -> schemas.ProductListOut:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(Product).where(Product.organization_id == ctx.org.id, Product.is_active.is_(True))
    if query and query.strip():
        like = f"%{query.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like), Product.description.ilike(like)))
    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (
        (await session.execute(stmt.order_by(Product.updated_at.desc(), Product.id).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    images = await _images_for(session, [r.id for r in rows])
    return schemas.ProductListOut(
        items=[_out(r, images.get(r.id, [])) for r in rows], total=total, limit=limit, offset=offset
    )


async def get_product(session: AsyncSession, ctx: OrgContext, product_id: uuid.UUID) -> schemas.ProductOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return await _present(session, await _get_product(session, ctx.org.id, product_id))


def _sku_conflict() -> AppError:
    return AppError("products.sku_exists", "Another product already uses that SKU.", 409)


async def _ensure_sku_free(session: AsyncSession, org_id: uuid.UUID, sku: str, *, exclude: uuid.UUID | None) -> None:
    stmt = select(Product.id).where(Product.organization_id == org_id, Product.sku == sku)
    if exclude is not None:
        stmt = stmt.where(Product.id != exclude)
    if (await session.execute(stmt.limit(1))).first() is not None:
        raise _sku_conflict()


async def create_product(
    session: AsyncSession, ctx: OrgContext, data: schemas.CreateProductRequest
) -> schemas.ProductOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    if data.sku:
        await _ensure_sku_free(session, ctx.org.id, data.sku, exclude=None)
    product = Product(
        organization_id=ctx.org.id,
        name=data.name.strip(),
        sku=data.sku,
        price_minor=data.price_minor,
        currency=data.currency,
        description=data.description or "",
        attributes=data.attributes or {},
        in_stock=True if data.in_stock is None else data.in_stock,
        url=data.url,
    )
    session.add(product)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _sku_conflict() from exc
    await write_audit(
        session, ctx.org.id, ctx.user.id, "product.create", target_type="product", target_id=str(product.id)
    )
    _schedule_embedding(product.id)
    return await _present(session, product)


async def update_product(
    session: AsyncSession, ctx: OrgContext, product_id: uuid.UUID, data: schemas.UpdateProductRequest
) -> schemas.ProductOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    product = await _get_product(session, ctx.org.id, product_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("sku") and changes["sku"] != product.sku:
        await _ensure_sku_free(session, ctx.org.id, changes["sku"], exclude=product.id)
    for key, value in changes.items():
        if key == "name" and value is not None:
            value = value.strip()
        if key in ("description",) and value is None:
            value = ""
        if key in ("attributes",) and value is None:
            value = {}
        if key in ("name", "in_stock", "is_active") and value is None:
            continue
        setattr(product, key, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _sku_conflict() from exc
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "product.update",
        target_type="product",
        target_id=str(product.id),
        meta={"fields": sorted(changes)},
    )
    _schedule_embedding(product.id)
    return await _present(session, product)


async def delete_product(session: AsyncSession, ctx: OrgContext, product_id: uuid.UUID) -> None:
    """Soft delete: the row stays (conversations may mention it) but it leaves the catalog and search."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    product = await _get_product(session, ctx.org.id, product_id)
    product.is_active = False
    await write_audit(
        session, ctx.org.id, ctx.user.id, "product.delete", target_type="product", target_id=str(product.id)
    )


# ── photos ────────────────────────────────────────────────────────────────────
async def add_image(
    session: AsyncSession, ctx: OrgContext, product_id: uuid.UUID, raw: bytes, alt_text: str | None
) -> schemas.ProductOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    product = await _get_product(session, ctx.org.id, product_id)
    allowed, _ = await limiter.hit(f"rl:product-upload:{ctx.org.id}", _UPLOADS_PER_MINUTE, 60)
    if not allowed:
        raise AppError("products.upload_rate_limited", "Too many uploads. Wait a minute and try again.", 429)
    existing = (await _images_for(session, [product.id])).get(product.id, [])
    if len(existing) >= MAX_IMAGES_PER_PRODUCT:
        raise AppError("products.too_many_images", f"A product can have at most {MAX_IMAGES_PER_PRODUCT} photos.", 400)
    asset = await media_service.save_asset(session, ctx.org, "product", raw)
    session.add(
        ProductImage(
            product_id=product.id,
            media_asset_id=asset.id,
            position=(max((i.position for i in existing), default=-1) + 1),
            alt_text=(alt_text or "").strip()[:200] or None,
        )
    )
    await session.flush()
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "product.image_add",
        target_type="product",
        target_id=str(product.id),
    )
    return await _present(session, product)


async def remove_image(
    session: AsyncSession, ctx: OrgContext, product_id: uuid.UUID, image_id: uuid.UUID
) -> schemas.ProductOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    product = await _get_product(session, ctx.org.id, product_id)
    image = (
        await session.execute(
            select(ProductImage).where(ProductImage.id == image_id, ProductImage.product_id == product.id)
        )
    ).scalar_one_or_none()
    if image is None:
        raise AppError("products.image_not_found", "Photo not found.", 404)
    asset = await session.get(MediaAsset, image.media_asset_id)
    if asset is not None:
        await media_service.delete_asset(session, asset)  # also removes the ProductImage row
    else:
        await session.delete(image)
    await session.flush()
    await write_audit(
        session, ctx.org.id, ctx.user.id, "product.image_remove", target_type="product", target_id=str(product.id)
    )
    return await _present(session, product)


async def reorder_images(
    session: AsyncSession, ctx: OrgContext, product_id: uuid.UUID, image_ids: list[uuid.UUID]
) -> schemas.ProductOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    product = await _get_product(session, ctx.org.id, product_id)
    images = {i.id: i for i in (await _images_for(session, [product.id])).get(product.id, [])}
    if set(image_ids) != set(images) or len(image_ids) != len(images):
        raise AppError("products.bad_order", "Send every photo of this product exactly once.", 400)
    for position, image_id in enumerate(image_ids):
        images[image_id].position = position
    await session.flush()
    return await _present(session, product)


# ── search (the agent's catalog tools) ────────────────────────────────────────
def _tsquery(text: str) -> str | None:
    """Any-term, prefix-matching query built only from word characters, so it can never be a syntax error.

    Any-term (`|`) for the reason ADR-062 records: a customer types a sentence, and AND-ing every word
    matches nothing.
    """
    tokens = [t for t in re.findall(r"\w+", text.lower()) if len(t) >= 2][:12]
    if not tokens:
        return None
    return " | ".join(f"{t}:*" if len(t) >= 3 else t for t in tokens)


async def search_products(
    session: AsyncSession, org_id: uuid.UUID, query: str, limit: int = 5, *, use_vectors: bool = True
) -> list[Product]:
    """Hybrid keyword + vector search, active products of one org only. An exact SKU match always ranks first."""
    q = query.strip()
    if not q:
        return []
    base = (Product.organization_id == org_id) & (Product.is_active.is_(True))

    sku_hits = list(
        (await session.execute(select(Product).where(base, func.lower(Product.sku) == q.lower()).limit(limit)))
        .scalars()
        .all()
    )

    scores: dict[uuid.UUID, float] = {}
    by_id: dict[uuid.UUID, Product] = {}

    tsq_text = _tsquery(q)
    if tsq_text:
        tsq = func.to_tsquery(literal_column("'simple'"), tsq_text)
        rank = func.ts_rank(Product.search_vector, tsq)
        fts = (
            (
                await session.execute(
                    select(Product).where(base, Product.search_vector.op("@@")(tsq)).order_by(rank.desc()).limit(20)
                )
            )
            .scalars()
            .all()
        )
        for position, row in enumerate(fts):
            by_id[row.id] = row
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (_RRF_K + position + 1)

    if use_vectors:
        try:
            embedder = build_embedding_provider(settings.embedding_provider, settings.embedding_model)
            vec = (await embedder.embed([q]))[0]
            dense = (
                (
                    await session.execute(
                        select(Product)
                        .where(base, Product.embedding.is_not(None))
                        .order_by(Product.embedding.cosine_distance(vec))
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
            for position, row in enumerate(dense):
                by_id[row.id] = row
                scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (_RRF_K + position + 1)
        except Exception as exc:  # keyword results are still useful
            log.info("product_vector_search_unavailable", error=type(exc).__name__)

    ranked = [by_id[i] for i in sorted(scores, key=lambda k: scores[k], reverse=True)]
    result: list[Product] = []
    for product in [*sku_hits, *ranked]:
        if all(product.id != r.id for r in result):
            result.append(product)
    return result[:limit]


async def image_assets(session: AsyncSession, product_id: uuid.UUID) -> list[MediaAsset]:
    """The product's photos in display order."""
    rows = (
        (
            await session.execute(
                select(MediaAsset)
                .join(ProductImage, ProductImage.media_asset_id == MediaAsset.id)
                .where(ProductImage.product_id == product_id)
                .order_by(ProductImage.position, ProductImage.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def image_counts(session: AsyncSession, product_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not product_ids:
        return {}
    rows = (
        await session.execute(
            select(ProductImage.product_id, func.count())
            .where(ProductImage.product_id.in_(product_ids))
            .group_by(ProductImage.product_id)
        )
    ).all()
    return {pid: int(n) for pid, n in rows}


def summarize(product: Product, image_count: int) -> dict[str, Any]:
    """What the model is told about a product: enough to answer, never the whole description."""
    desc = (product.description or "").strip()
    return {
        "id": str(product.id),
        "name": product.name,
        "sku": product.sku,
        "price": None if product.price_minor is None else product.price_minor / 100,
        "currency": product.currency,
        "in_stock": product.in_stock,
        "summary": desc[:200],
        "attributes": dict(product.attributes or {}),
        "image_count": image_count,
        "url": product.url,
    }
