"""Connections, per-agent tool bindings, and the tool_runs columns docs/28 needs (T1.1).

Revision ID: 0038_connections_and_bindings
Revises: 0037_message_blocks

Additive only. `agent_tool_bindings` is backfilled from every `tools.agent_id`, so an agent resolves
exactly the tool set it did before. `tools.agent_id` stays (it is the "owner" the bindings were seeded
from, and the admin console still joins on it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0038_connections_and_bindings"
down_revision: str | None = "0037_message_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("base_url", sa.Text, nullable=False),
        sa.Column("auth_type", sa.String(32), nullable=False, server_default="none"),
        sa.Column("secrets_enc", sa.Text),
        sa.Column("default_headers", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("allowed_hosts", ARRAY(sa.String), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("timeout_s", sa.Integer, nullable=False, server_default="15"),
        sa.Column("health_path", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "name", name="uq_connections_org_name"),
    )
    op.create_index("ix_connections_organization_id", "connections", ["organization_id"])

    op.alter_column("tools", "type", existing_type=sa.String(16), type_=sa.String(24), existing_nullable=False)
    op.add_column(
        "tools",
        sa.Column("connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id", ondelete="RESTRICT")),
    )
    op.add_column("tools", sa.Column("messages", JSONB))
    op.add_column("tools", sa.Column("approval_required", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("tools", sa.Column("rate_limit", JSONB))
    op.add_column("tools", sa.Column("mode", sa.String(8), nullable=False, server_default="sync"))
    op.create_index("ix_tools_connection_id", "tools", ["connection_id"])

    op.create_table(
        "agent_tool_bindings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_id", UUID(as_uuid=True), sa.ForeignKey("tools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("overrides", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool_bindings_agent_tool"),
    )
    op.create_index("ix_agent_tool_bindings_organization_id", "agent_tool_bindings", ["organization_id"])
    op.create_index("ix_agent_tool_bindings_agent_id", "agent_tool_bindings", ["agent_id"])
    op.create_index("ix_agent_tool_bindings_tool_id", "agent_tool_bindings", ["tool_id"])

    op.add_column(
        "tool_runs", sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL"))
    )
    op.add_column("tool_runs", sa.Column("provider", sa.String(16)))
    op.add_column("tool_runs", sa.Column("request_sanitized", JSONB))
    op.add_column("tool_runs", sa.Column("attempt", sa.Integer, nullable=False, server_default="1"))
    op.create_index(
        "ix_tool_runs_org_agent_created",
        "tool_runs",
        ["organization_id", "agent_id", sa.text("created_at DESC")],
    )

    # Nothing changes for a live agent: every tool that belonged to an agent is bound to it.
    op.execute(
        """
        INSERT INTO agent_tool_bindings (id, organization_id, agent_id, tool_id, enabled, overrides, position)
        SELECT gen_random_uuid(), t.organization_id, t.agent_id, t.id, true, '{}'::jsonb, 0
        FROM tools t
        WHERE t.agent_id IS NOT NULL
        ON CONFLICT (agent_id, tool_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tool_runs_org_agent_created", table_name="tool_runs")
    op.drop_column("tool_runs", "attempt")
    op.drop_column("tool_runs", "request_sanitized")
    op.drop_column("tool_runs", "provider")
    op.drop_column("tool_runs", "agent_id")
    op.drop_table("agent_tool_bindings")
    op.drop_index("ix_tools_connection_id", table_name="tools")
    op.drop_column("tools", "mode")
    op.drop_column("tools", "rate_limit")
    op.drop_column("tools", "approval_required")
    op.drop_column("tools", "messages")
    op.drop_column("tools", "connection_id")
    # Narrowing back is only safe while no row uses a longer type name; none of the new ones do (`api` is 3).
    op.alter_column("tools", "type", existing_type=sa.String(24), type_=sa.String(16), existing_nullable=False)
    op.drop_table("connections")
