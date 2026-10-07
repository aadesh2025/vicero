"""Client-visible automations: requests, registered workflows, and the run log (docs/26).

Revision ID: 0031_automations
Revises: 0030_billing_currency

Three new tables, no change to existing ones. Forward-only like every migration here; the downgrade only
drops the three tables (never run against production: back up first, restore instead of downgrading).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_automations"
down_revision: str | None = "0030_billing_currency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def _id_and_times() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "automation_requests",
        *_id_and_times(),
        sa.Column("organization_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("requested_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="requested"),
        sa.Column("staff_note", sa.String(500)),
        sa.CheckConstraint(
            "status IN ('requested','building','active','paused','rejected')", name="ck_automation_requests_status"
        ),
    )
    op.create_index("ix_automation_requests_organization_id", "automation_requests", ["organization_id"])

    op.create_table(
        "automations",
        *_id_and_times(),
        sa.Column("organization_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("request_id", _UUID, sa.ForeignKey("automation_requests.id", ondelete="SET NULL")),
        sa.Column("n8n_workflow_id", sa.String(64), nullable=False),
        sa.Column("webhook_path", sa.String(255)),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("config_encrypted", sa.Text()),
        sa.CheckConstraint("status IN ('active','paused')", name="ck_automations_status"),
        sa.UniqueConstraint("n8n_workflow_id", name="uq_automations_n8n_workflow_id"),
    )
    op.create_index("ix_automations_organization_id", "automations", ["organization_id"])
    op.create_index("ix_automations_webhook_path", "automations", ["webhook_path"])

    op.create_table(
        "automation_runs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("organization_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("automation_id", _UUID, sa.ForeignKey("automations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("n8n_execution_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("error_summary", sa.String(500)),
        sa.Column("input_summary", sa.String(2000)),
        sa.Column("output_summary", sa.String(2000)),
        sa.Column("over_cap", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('success','failed','running')", name="ck_automation_runs_status"),
        sa.UniqueConstraint("automation_id", "n8n_execution_id", name="uq_automation_runs_execution"),
    )
    op.create_index("ix_automation_runs_organization_id", "automation_runs", ["organization_id"])
    op.create_index("ix_automation_runs_org_started", "automation_runs", ["organization_id", "started_at"])
    op.create_index("ix_automation_runs_automation_started", "automation_runs", ["automation_id", "started_at"])


def downgrade() -> None:
    op.drop_table("automation_runs")
    op.drop_table("automations")
    op.drop_table("automation_requests")
