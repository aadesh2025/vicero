"""Create and delete media assets (docs/26 §5, §3.4).

Product photos are billed against the plan's storage cap; customer photos are not, and expire.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.core.config import settings
from app.core.logging import get_logger
from app.media.sanitize import sanitize_image
from app.media.store import get_store, make_key
from app.models import MediaAsset, Organization, ProductImage

log = get_logger("media")


async def save_asset(
    session: AsyncSession,
    org: Organization | uuid.UUID,
    kind: str,
    raw: bytes,
    *,
    source_channel: str | None = None,
    description: str | None = None,
    contact_id: uuid.UUID | None = None,
) -> MediaAsset:
    """Sanitise, de-duplicate on `(org, kind, sha256)`, store, and record a row."""
    org_id = org if isinstance(org, uuid.UUID) else org.id
    img = sanitize_image(raw)

    existing = (
        (
            await session.execute(
                select(MediaAsset).where(
                    MediaAsset.organization_id == org_id, MediaAsset.kind == kind, MediaAsset.sha256 == img.sha256
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None and kind != "product":
        # A Meta redelivery of the same photo. (Product photos are never shared between rows, so that
        # deleting one product's picture can never pull it from under another's.)
        if description and not existing.description:
            existing.description = description
        return existing

    if kind == "product":
        if isinstance(org, Organization):
            await usage.require_storage_bytes(session, org, len(img.data))
        else:  # pragma: no cover - callers always hold the Organization for product uploads
            raise ValueError("product assets need the Organization for the storage check")

    now = dt.datetime.now(dt.UTC)
    key = make_key(org_id, now.year, now.month, "png" if img.mime == "image/png" else "jpg")
    await get_store().put(img.data, key)
    asset = MediaAsset(
        organization_id=org_id,
        kind=kind,
        storage_key=key,
        mime=img.mime,
        bytes=len(img.data),
        width=img.width,
        height=img.height,
        sha256=img.sha256,
        description=description,
        source_channel=source_channel,
        contact_id=contact_id,
        expires_at=(now + dt.timedelta(days=settings.inbound_media_retention_days)) if kind == "inbound" else None,
    )
    session.add(asset)
    await session.flush()
    if kind == "product":
        await usage.adjust_storage_bytes(session, org_id, asset.bytes)
    return asset


async def get_org_asset(session: AsyncSession, org_id: uuid.UUID, media_id: uuid.UUID) -> MediaAsset | None:
    return (
        await session.execute(select(MediaAsset).where(MediaAsset.id == media_id, MediaAsset.organization_id == org_id))
    ).scalar_one_or_none()


async def delete_asset(session: AsyncSession, asset: MediaAsset) -> None:
    """Remove the file, then the row; give product bytes back to the org's storage counter."""
    try:
        await get_store().delete(asset.storage_key)
    except OSError:
        log.warning("media_delete_failed", media_id=str(asset.id))
    if asset.kind == "product":
        await usage.adjust_storage_bytes(session, asset.organization_id, -asset.bytes)
    await session.execute(delete(ProductImage).where(ProductImage.media_asset_id == asset.id))
    await session.delete(asset)


async def purge_expired(session: AsyncSession, now: dt.datetime | None = None) -> int:
    """Delete inbound photos past their retention (file first, then row). Never touches product assets."""
    now = now or dt.datetime.now(dt.UTC)
    rows = (
        (
            await session.execute(
                select(MediaAsset).where(MediaAsset.kind == "inbound", MediaAsset.expires_at < now).limit(1000)
            )
        )
        .scalars()
        .all()
    )
    for asset in rows:
        await delete_asset(session, asset)
    return len(rows)


async def delete_contact_media(session: AsyncSession, contact_ids: list[uuid.UUID]) -> int:
    """Data-deletion: remove every inbound photo these contacts sent (across orgs: Meta ids are app-scoped)."""
    if not contact_ids:
        return 0
    rows = (
        (
            await session.execute(
                select(MediaAsset).where(MediaAsset.kind == "inbound", MediaAsset.contact_id.in_(contact_ids))
            )
        )
        .scalars()
        .all()
    )
    for asset in rows:
        await delete_asset(session, asset)
    return len(rows)
