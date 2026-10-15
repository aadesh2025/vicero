"""Native Google Calendar connection per organisation (ADR-121).

Revision ID: 0035_google_calendar
Revises: 0034_bookings

One new table. Forward-only; the downgrade drops it (restore a backup rather than downgrading).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0035_google_calendar"
down_revision: str | None = "0034_bookings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_calendar_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connected_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("google_email", sa.String(320)),
        sa.Column("calendar_id", sa.String(255), nullable=False, server_default="primary"),
        sa.Column("refresh_token_encrypted", sa.Text, nullable=False),
        sa.Column("access_token_encrypted", sa.Text),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_google_calendar_connections_organization_id",
        "google_calendar_connections",
        ["organization_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_google_calendar_connections_organization_id", table_name="google_calendar_connections")
    op.drop_table("google_calendar_connections")
