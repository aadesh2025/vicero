"""Per-agent outbound webhooks and inbound trigger hooks (docs/28 T3.1).

Revision ID: 0040_inbound_hooks
Revises: 0039_mcp_per_agent

Additive. `webhook_endpoints.agent_id` is NULL for every existing endpoint, which keeps them org-wide.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.migration_helpers import drop_dependent_views, views_suspended

revision: str = "0040_inbound_hooks"
down_revision: str | None = "0039_mcp_per_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_endpoints",
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE")),
    )
    op.create_index("ix_webhook_endpoints_agent_id", "webhook_endpoints", ["agent_id"])

    op.create_table(
        "inbound_hooks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("public_id", sa.String(48), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("action_config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_mapping", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("auth_mode", sa.String(8), nullable=False),
        sa.Column("secret_enc", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_called_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_inbound_hooks_organization_id", "inbound_hooks", ["organization_id"])
    op.create_index("ix_inbound_hooks_agent_id", "inbound_hooks", ["agent_id"])

    op.create_table(
        "inbound_hook_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "inbound_hook_id", UUID(as_uuid=True), sa.ForeignKey("inbound_hooks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("http_status", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("payload_sanitized", JSONB),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_inbound_hook_events_inbound_hook_id", "inbound_hook_events", ["inbound_hook_id"])
    op.create_index("ix_inbound_hook_events_created_at", "inbound_hook_events", ["created_at"])


def downgrade() -> None:
    drop_dependent_views("inbound_hook_events", "inbound_hooks")
    op.drop_table("inbound_hook_events")
    op.drop_table("inbound_hooks")
    op.drop_index("ix_webhook_endpoints_agent_id", table_name="webhook_endpoints")
    with views_suspended("webhook_endpoints", best_effort=True):
        op.drop_column("webhook_endpoints", "agent_id")
