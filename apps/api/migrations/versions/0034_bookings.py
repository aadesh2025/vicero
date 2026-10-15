"""Bookings made by the agent's book_appointment tool (ADR-120).

Revision ID: 0034_bookings
Revises: 0033_meta_one_click_connect

One new table, no change to existing ones. `(organization_id, idempotency_key)` is unique so a retried webhook cannot
double-book. Forward-only; the downgrade drops the table (restore a backup rather than downgrading).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0034_bookings"
down_revision: str | None = "0033_meta_one_click_connect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("channel", sa.String(16)),
        sa.Column("customer_name", sa.String(120), nullable=False),
        sa.Column("customer_contact", sa.String(255), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="30"),
        sa.Column("notes", sa.Text),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("external_event_id", sa.String(255)),
        sa.Column("confirmation_sent", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_bookings_org_idempotency"),
    )
    op.create_index("ix_bookings_organization_id", "bookings", ["organization_id"])
    op.create_index("ix_bookings_org_agent_start", "bookings", ["organization_id", "agent_id", "start_at"])


def downgrade() -> None:
    op.drop_index("ix_bookings_org_agent_start", table_name="bookings")
    op.drop_index("ix_bookings_organization_id", table_name="bookings")
    op.drop_table("bookings")
