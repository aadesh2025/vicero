"""Media assets, product catalog, message attachments (docs/26, ADR-124).

Revision ID: 0036_media_and_products
Revises: 0035_google_calendar

Additive only: three new tables and one defaulted column. Safe to run with the API up.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0036_media_and_products"
down_revision: str | None = "0035_google_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 768  # matches chunks.embedding


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("bytes", sa.Integer, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("source_channel", sa.String(16)),
        sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_media_assets_organization_id", "media_assets", ["organization_id"])
    op.create_index("ix_media_assets_expires_at", "media_assets", ["expires_at"])
    op.create_index("ix_media_assets_org_sha", "media_assets", ["organization_id", "sha256"])

    op.create_table(
        "products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sku", sa.String(100)),
        sa.Column("price_minor", sa.BigInteger),
        sa.Column("currency", sa.String(3)),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("attributes", JSONB, nullable=False, server_default="{}"),
        sa.Column("in_stock", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("url", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "search_vector",
            sa.dialects.postgresql.TSVECTOR,
            sa.Computed(
                "to_tsvector('simple', coalesce(name,'') || ' ' || coalesce(sku,'') || ' ' || coalesce(description,''))",
                persisted=True,
            ),
        ),
        sa.Column("embedding", Vector(_EMBEDDING_DIM)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "sku", name="uq_products_org_sku"),
    )
    op.create_index("ix_products_organization_id", "products", ["organization_id"])
    op.create_index("ix_products_search_vector", "products", ["search_vector"], postgresql_using="gin")

    op.create_table(
        "product_images",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id", UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "media_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("alt_text", sa.String(200)),
    )
    op.create_index("ix_product_images_product_id", "product_images", ["product_id"])

    op.add_column("messages", sa.Column("attachments", JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("messages", "attachments")
    op.drop_index("ix_product_images_product_id", table_name="product_images")
    op.drop_table("product_images")
    op.drop_index("ix_products_search_vector", table_name="products")
    op.drop_index("ix_products_organization_id", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_media_assets_org_sha", table_name="media_assets")
    op.drop_index("ix_media_assets_expires_at", table_name="media_assets")
    op.drop_index("ix_media_assets_organization_id", table_name="media_assets")
    op.drop_table("media_assets")
