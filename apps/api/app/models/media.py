"""Images stored for customers' photos and the product catalog (docs/26, ADR-124)."""

from __future__ import annotations

import datetime as dt
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.knowledge import EMBEDDING_DIM


class MediaAsset(Base, UUIDPrimaryKey):
    """One sanitised image on the media store. `kind` decides retention and billing:
    `inbound` (customer photo, expires, not billed), `product` (kept, counts toward storage), `operator`."""

    __tablename__ = "media_assets"
    __table_args__ = (Index("ix_media_assets_org_sha", "organization_id", "sha256"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # inbound | product | operator
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Vision output for an inbound photo. Plain text, treated as untrusted like any visitor message.
    description: Mapped[str | None] = mapped_column(Text)
    source_channel: Mapped[str | None] = mapped_column(String(16))
    #: The contact who sent an inbound photo, so a data-deletion request can find it.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Product(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("organization_id", "sku", name="uq_products_org_sku"),
        Index("ix_products_search_vector", "search_vector", postgresql_using="gin"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100))
    price_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    attributes: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(name,'') || ' ' || coalesce(sku,'') || ' ' || coalesce(description,''))",
            persisted=True,
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


class ProductImage(Base, UUIDPrimaryKey):
    __tablename__ = "product_images"

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    media_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0", nullable=False)
    alt_text: Mapped[str | None] = mapped_column(String(200))
