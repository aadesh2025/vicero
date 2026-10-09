"""Channel columns for Meta one-click connect (docs/26-META-ONE-CLICK-CONNECT.md, ADR-113).

Revision ID: 0033_meta_one_click_connect
Revises: 0032_data_deletion_requests

Adds provider-neutral columns to `channels` and a partial unique index on (type, external_id) so one
phone number / page / Instagram account belongs to one org. Existing Meta channels get `external_id`
backfilled from their config; where two rows share an id only the oldest is backfilled (the index would
otherwise fail), the rest stay NULL and keep working through their own per-channel webhooks.
Forward-only; the downgrade drops the index and the columns (restore a backup rather than downgrading).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_meta_one_click_connect"
down_revision: str | None = "0032_data_deletion_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("channels", sa.Column("external_id", sa.String(255)))
    op.add_column("channels", sa.Column("external_parent_id", sa.String(255)))
    op.add_column(
        "channels", sa.Column("connection_source", sa.String(16), nullable=False, server_default="manual")
    )
    op.add_column("channels", sa.Column("status", sa.String(24), nullable=False, server_default="active"))
    op.add_column("channels", sa.Column("token_expires_at", sa.DateTime(timezone=True)))
    op.add_column("channels", sa.Column("last_health_check_at", sa.DateTime(timezone=True)))

    # Backfill from the manual config, oldest row wins per (type, id).
    op.execute(
        """
        WITH cand AS (
            SELECT id, type,
                   NULLIF(BTRIM(CASE type
                        WHEN 'whatsapp' THEN config->>'phone_number_id'
                        WHEN 'facebook' THEN config->>'page_id'
                        WHEN 'instagram' THEN config->>'ig_user_id'
                   END), '') AS ext,
                   created_at
            FROM channels
            WHERE type IN ('whatsapp', 'facebook', 'instagram')
        ), ranked AS (
            SELECT id, ext,
                   ROW_NUMBER() OVER (PARTITION BY type, ext ORDER BY created_at, id) AS rn
            FROM cand WHERE ext IS NOT NULL
        )
        UPDATE channels c SET external_id = LEFT(r.ext, 255)
        FROM ranked r WHERE c.id = r.id AND r.rn = 1
        """
    )
    op.create_index(
        "uq_channels_type_external_id",
        "channels",
        ["type", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_channels_type_external_id", table_name="channels")
    for col in (
        "last_health_check_at",
        "token_expires_at",
        "status",
        "connection_source",
        "external_parent_id",
        "external_id",
    ):
        op.drop_column("channels", col)
