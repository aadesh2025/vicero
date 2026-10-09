"""Meta Data Deletion Request Callback log.

Revision ID: 0032_data_deletion_requests
Revises: 0031_automations

One new table, no change to existing ones. Platform-level (no organization_id): Meta names an app-scoped user,
not a tenant. Forward-only like every migration here; the downgrade only drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_data_deletion_requests"
down_revision: str | None = "0031_automations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_deletion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmation_code", sa.String(64), nullable=False),
        sa.Column("user_id_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("records_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("confirmation_code", name="uq_data_deletion_requests_confirmation_code"),
    )


def downgrade() -> None:
    op.drop_table("data_deletion_requests")
