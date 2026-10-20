"""Per-agent MCP: server slug / connection / tools cache, and agent_mcp_bindings (docs/28 T2.1).

Revision ID: 0039_mcp_per_agent
Revises: 0038_connections_and_bindings

Additive. Data step: for every org that has the agentic runtime on (`organizations.agentic_loop_enabled`) the
existing MCP `tools` rows were the only way an MCP tool reached a turn, gated on that flag. Those rows become
"managed" (the per-agent binding materialises one tool row per enabled MCP tool), are renamed to
`<slug>__<tool>`, and each agent gets an `agent_mcp_bindings` row, so nothing those orgs use stops working.
Orgs with the flag off keep their dormant rows untouched: registration still does not imply usage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from app.db.migration_helpers import drop_dependent_views, views_suspended

revision: str = "0039_mcp_per_agent"
down_revision: str | None = "0038_connections_and_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "mcp"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}_{n}"
        n += 1
    taken.add(slug)
    return slug


def _tool_name(slug: str, tool: str) -> str:
    return f"{slug}__{re.sub(r'[^a-zA-Z0-9_-]+', '_', tool)}"[:64]


def upgrade() -> None:
    # Production has viewer.mcp_servers (ADR-115) over `transport`; Postgres refuses to retype a column a view
    # reads, which is what stopped the first deploy of this migration (rolled back, never applied in prod).
    with views_suspended("mcp_servers"):
        op.alter_column(
            "mcp_servers", "transport", existing_type=sa.String(16), type_=sa.String(24), existing_nullable=False
        )
    op.add_column("mcp_servers", sa.Column("slug", sa.String(32)))
    op.add_column(
        "mcp_servers",
        sa.Column("connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id", ondelete="RESTRICT")),
    )
    op.add_column("mcp_servers", sa.Column("tools_cache", JSONB))
    op.add_column("mcp_servers", sa.Column("tools_cached_at", sa.DateTime(timezone=True)))
    op.add_column("mcp_servers", sa.Column("last_error", sa.Text))

    op.create_table(
        "agent_mcp_bindings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "mcp_server_id", UUID(as_uuid=True), sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("enabled_tools", ARRAY(sa.String), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("tool_overrides", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "mcp_server_id", name="uq_agent_mcp_bindings_agent_server"),
    )
    op.create_index("ix_agent_mcp_bindings_organization_id", "agent_mcp_bindings", ["organization_id"])
    op.create_index("ix_agent_mcp_bindings_agent_id", "agent_mcp_bindings", ["agent_id"])
    op.create_index("ix_agent_mcp_bindings_mcp_server_id", "agent_mcp_bindings", ["mcp_server_id"])

    bind = op.get_bind()
    # Slugs for every existing server, unique per org, oldest first.
    servers = bind.execute(
        sa.text("SELECT id, organization_id, name FROM mcp_servers ORDER BY organization_id, created_at, id")
    ).all()
    taken: dict[object, set[str]] = {}
    slugs: dict[object, str] = {}
    for sid, org_id, name in servers:
        slugs[sid] = _slug(name, taken.setdefault(org_id, set()))
        bind.execute(sa.text("UPDATE mcp_servers SET slug = :s WHERE id = :i"), {"s": slugs[sid], "i": sid})
    op.create_index("uq_mcp_servers_org_slug", "mcp_servers", ["organization_id", "slug"], unique=True)

    # Orgs that were actually using MCP tools: the agentic runtime flag is on.
    rows = bind.execute(
        sa.text(
            """
            SELECT t.id, t.organization_id, t.agent_id, t.description, t.input_schema, t.config
            FROM tools t JOIN organizations o ON o.id = t.organization_id
            WHERE t.type = 'mcp' AND t.agent_id IS NOT NULL AND o.agentic_loop_enabled IS TRUE
            """
        )
    ).all()
    caches: dict[object, dict[str, dict]] = {}
    enabled: dict[tuple[object, object], list[str]] = {}
    for tid, org_id, agent_id, desc, schema, config in rows:
        cfg = config if isinstance(config, dict) else json.loads(config)
        sid, tool = cfg.get("server_id"), cfg.get("tool_name")
        if not sid or not tool:
            continue
        try:
            import uuid as _uuid

            server_uuid = _uuid.UUID(str(sid))
        except ValueError:
            continue
        if server_uuid not in slugs:
            continue
        caches.setdefault(server_uuid, {})[tool] = {
            "name": tool,
            "description": desc or "",
            "input_schema": schema if isinstance(schema, dict) else json.loads(schema or "{}"),
        }
        enabled.setdefault((agent_id, server_uuid), []).append(tool)
        bind.execute(
            sa.text("UPDATE tools SET name = :n, config = jsonb_set(config, '{managed}', 'true'::jsonb) WHERE id = :i"),
            {"n": _tool_name(slugs[server_uuid], tool), "i": tid},
        )
        org_of = org_id
        enabled.setdefault((agent_id, server_uuid), [])
        bind.execute(
            sa.text(
                "INSERT INTO agent_mcp_bindings (id, organization_id, agent_id, mcp_server_id, enabled_tools,"
                " tool_overrides, enabled) SELECT gen_random_uuid(), :o, :a, :s, ARRAY[]::varchar[], '{}'::jsonb, true"
                " WHERE NOT EXISTS (SELECT 1 FROM agent_mcp_bindings WHERE agent_id = :a AND mcp_server_id = :s)"
            ),
            {"o": org_of, "a": agent_id, "s": server_uuid},
        )
    for (agent_id, server_uuid), tools in enabled.items():
        bind.execute(
            sa.text(
                "UPDATE agent_mcp_bindings SET enabled_tools = CAST(:t AS varchar[]) "
                "WHERE agent_id = :a AND mcp_server_id = :s"
            ),
            {"t": sorted(set(tools)), "a": agent_id, "s": server_uuid},
        )
    for server_uuid, cache in caches.items():
        bind.execute(
            sa.text("UPDATE mcp_servers SET tools_cache = CAST(:c AS jsonb), tools_cached_at = now() WHERE id = :i"),
            {"c": json.dumps(list(cache.values())), "i": server_uuid},
        )


def downgrade() -> None:
    # Managed rows keep their `<slug>__<tool>` names and `managed` flag; as legacy rows they simply go back to
    # being gated on the agentic runtime flag. Nothing is deleted except the new structures.
    drop_dependent_views("agent_mcp_bindings")
    op.drop_table("agent_mcp_bindings")
    op.drop_index("uq_mcp_servers_org_slug", table_name="mcp_servers")
    with views_suspended("mcp_servers", best_effort=True):
        op.drop_column("mcp_servers", "last_error")
        op.drop_column("mcp_servers", "tools_cached_at")
        op.drop_column("mcp_servers", "tools_cache")
        op.drop_column("mcp_servers", "connection_id")
        op.drop_column("mcp_servers", "slug")
        op.alter_column(
            "mcp_servers", "transport", existing_type=sa.String(24), type_=sa.String(16), existing_nullable=False
        )
