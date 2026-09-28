"""paid plans, admin grants, payment ledger, storage accounting (docs/22, ADR-102)

- `organizations` gains `plan_source`/`plan_expires_at`/`plan_granted_at`/`plan_granted_by`/
  `plan_note` (docs/22 §5) — who granted the current plan, and until when. All nullable or
  server-defaulted: no table rewrite, no backfill needed (every existing org already reads as
  `plan_source='system'`, `plan_expires_at=NULL` — unchanged behaviour).
- `org_message_usage` gains the billing-period columns (docs/22 §6): `period_start`,
  `period_end` (`NULL` = never rolls — trial/legacy keep today's lifetime-counter behaviour),
  `extra_messages` (packs bought this period, docs/22 §7).
- New `plan_grants` (append-only billing evidence, docs/22 §5), `billing_cycles` (the payment
  ledger — one row per 30-day cycle, docs/22 §5.1) and `org_storage_usage` (per-org document
  storage accounting, docs/22 §3 rule 2).
- `org_storage_usage` is backfilled from `documents` in this same migration: real
  `size_bytes`/`COUNT(*)` per org, since `documents.size_bytes` already exists — storage
  enforcement starts from real numbers, not zero.

Reversible: `downgrade()` drops everything added.

Revision ID: 0029_paid_plans_admin_grants
Revises: 0028_self_serve_trial
Create Date: 2026-09-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_paid_plans_admin_grants"
down_revision: str | None = "0028_self_serve_trial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── organizations: who granted what, and until when (docs/22 §5) ────────────────────────
    op.add_column(
        "organizations",
        sa.Column("plan_source", sa.String(16), nullable=False, server_default="system"),
    )
    op.add_column("organizations", sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("plan_granted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("plan_granted_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("organizations", sa.Column("plan_note", sa.String(500), nullable=True))

    # ── org_message_usage: the billing window and packs (docs/22 §6) ────────────────────────
    op.add_column(
        "org_message_usage",
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("org_message_usage", sa.Column("period_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "org_message_usage",
        sa.Column("extra_messages", sa.Integer(), nullable=False, server_default="0"),
    )

    # ── grant history: append-only billing evidence (docs/22 §5) ────────────────────────────
    op.create_table(
        "plan_grants",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("from_plan", sa.String(32), nullable=True),
        sa.Column("to_plan", sa.String(32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra_messages", sa.Integer(), nullable=True),
        sa.Column("amount_usd_cents", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("actor_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("invoiced", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_plan_grants_org", "plan_grants", ["organization_id", "created_at"])
    op.create_index(
        op.f("ix_plan_grants_organization_id"), "plan_grants", ["organization_id"], unique=False
    )

    # ── payment ledger: one row per 30-day cycle (docs/22 §5.1) ─────────────────────────────
    op.create_table(
        "billing_cycles",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan", sa.String(32), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount_usd_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("method", sa.String(32), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("marked_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_billing_cycles_org", "billing_cycles", ["organization_id", "period_start"])
    op.create_index("ix_billing_cycles_status", "billing_cycles", ["status", "period_end"])
    op.create_index(
        op.f("ix_billing_cycles_organization_id"), "billing_cycles", ["organization_id"], unique=False
    )

    # ── storage accounting, same shape as org_message_usage ─────────────────────────────────
    op.create_table(
        "org_storage_usage",
        sa.Column(
            "organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bytes_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("documents_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Backfill from the real `documents` table — `size_bytes` already exists there (nullable;
    # SUM() ignores the NULLs a handful of pre-size_bytes rows may still carry, so a partially
    # unsized org isn't reported as zero), so storage enforcement starts from real numbers.
    op.execute(
        """
        INSERT INTO org_storage_usage (organization_id, bytes_used, documents_count)
        SELECT organization_id, COALESCE(SUM(size_bytes), 0), COUNT(*)
          FROM documents
         GROUP BY organization_id
        ON CONFLICT (organization_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("org_storage_usage")
    op.drop_index("ix_billing_cycles_organization_id", table_name="billing_cycles")
    op.drop_index("ix_billing_cycles_status", table_name="billing_cycles")
    op.drop_index("ix_billing_cycles_org", table_name="billing_cycles")
    op.drop_table("billing_cycles")
    op.drop_index(op.f("ix_plan_grants_organization_id"), table_name="plan_grants")
    op.drop_index("ix_plan_grants_org", table_name="plan_grants")
    op.drop_table("plan_grants")

    op.drop_column("org_message_usage", "extra_messages")
    op.drop_column("org_message_usage", "period_end")
    op.drop_column("org_message_usage", "period_start")

    op.drop_column("organizations", "plan_note")
    op.drop_column("organizations", "plan_granted_by")
    op.drop_column("organizations", "plan_granted_at")
    op.drop_column("organizations", "plan_expires_at")
    op.drop_column("organizations", "plan_source")
