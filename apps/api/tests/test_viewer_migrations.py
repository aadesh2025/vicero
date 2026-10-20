"""Migrations must run on a database that has the production `viewer` views (ADR-115, docs/25 section 13).

Postgres refuses to retype or drop a column a view reads. Production has `viewer.*` views over most tables; dev
and the test database do not, so migration 0039 passed every test and then failed on the first live deploy
(2026-10-20, rolled back, never applied). This builds a scratch database at revision 0037, creates the viewer
views with the SAME SQL production runs (`ops/viewer-grants.sql`), and migrates it up to head, down and up again.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from app.core.config import settings

API_DIR = Path(__file__).resolve().parents[1]
GRANTS_SQL = (API_DIR.parents[1] / "ops" / "viewer-grants.sql").read_text(encoding="utf-8")
BASE = "0037_message_blocks"

# Same pattern as the SQL file's `secret_col`; the explicit pairs are asserted by name below as well.
SECRET_COL = re.compile(r"(_enc|_encrypted|password|token_hash|key_hash|secret|access_token|refresh_token)")
HIDDEN = {
    "channels": {"config"},
    "tools": {"config"},
    "connections": {"default_headers", "secrets_enc"},
    "mcp_servers": {"url_or_command", "auth_config_enc"},
    "inbound_hooks": {"public_id", "secret_enc"},
    "handoffs": {"payload"},
}


def _dsn(database: str) -> str:
    base = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    return re.sub(r"/[^/?]+(\?|$)", rf"/{database}\1", base, count=1)


def _alembic(database: str, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": _dsn(database).replace("postgresql://", "postgresql+asyncpg://")}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=env, capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout[-2000:]}\n{done.stderr[-3000:]}"


async def _apply_viewer_sql(conn: asyncpg.Connection, database: str) -> None:
    await conn.execute(f'GRANT CONNECT ON DATABASE "{database}" TO viewer')
    await conn.execute(GRANTS_SQL)


async def _views(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT schemaname || '.' || viewname AS name, definition FROM pg_views WHERE schemaname = 'viewer'"
    )
    return {r["name"]: re.sub(r"\s+", " ", r["definition"]) for r in rows}


@pytest.fixture
async def scratch() -> AsyncIterator[tuple[str, asyncpg.Connection]]:
    name = f"vicero_viewmig_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(_dsn("postgres"))
    created_role = False
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'viewer'"):
            await admin.execute("CREATE ROLE viewer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE")
            created_role = True
        await admin.execute(f'CREATE DATABASE "{name}"')
        conn = await asyncpg.connect(_dsn(name))
        try:
            yield name, conn
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if created_role:
            await admin.execute("DROP ROLE IF EXISTS viewer")
        await admin.close()


async def test_migrations_run_over_the_production_viewer_views(scratch: tuple[str, asyncpg.Connection]) -> None:
    name, conn = scratch
    _alembic(name, "upgrade", BASE)
    await _apply_viewer_sql(conn, name)
    before = await _views(conn)
    assert "viewer.mcp_servers" in before and "viewer.tools" in before  # both are views over a column 0038/0039 retype

    # The bug as it happened: without the helper this exact statement is what Postgres refuses.
    tx = conn.transaction()
    await tx.start()
    with pytest.raises(asyncpg.FeatureNotSupportedError, match="used by a view"):
        await conn.execute("ALTER TABLE mcp_servers ALTER COLUMN transport TYPE VARCHAR(24)")
    await tx.rollback()

    _alembic(name, "upgrade", "head")
    after = await _views(conn)
    for view, definition in before.items():
        assert view in after, f"{view} was lost by the migrations"
        assert after[view] == definition, f"{view} was recreated differently"
    assert await conn.fetchval("SELECT has_table_privilege('viewer', 'viewer.mcp_servers', 'SELECT')")

    # deploy.sh then re-runs the same SQL: the new tables appear, and nothing secret does.
    await _apply_viewer_sql(conn, name)
    rebuilt = await _views(conn)
    for new in ("viewer.connections", "viewer.inbound_hooks", "viewer.mcp_servers"):
        assert new in rebuilt, f"{new} missing after the viewer rebuild"
    await _assert_no_secrets(conn)

    # Down and up again (never run in production; it must still work, and must not strand the views).
    _alembic(name, "downgrade", BASE)
    assert await conn.fetchval("SELECT to_regclass('public.connections')") is None
    await _apply_viewer_sql(conn, name)
    _alembic(name, "upgrade", "head")
    assert "viewer.mcp_servers" in await _views(conn)


async def _assert_no_secrets(conn: asyncpg.Connection) -> None:
    """Every column the viewer can read, through a view or a direct grant, is checked against the secret rules."""
    rows = await conn.fetch(
        """
        SELECT table_schema, table_name, column_name FROM information_schema.columns
         WHERE table_schema IN ('public', 'viewer')
           AND has_table_privilege('viewer', format('%I.%I', table_schema, table_name), 'SELECT')
        """
    )
    assert rows, "the viewer can read nothing at all"
    seen: dict[str, set[str]] = {}
    for r in rows:
        seen.setdefault(r["table_name"], set()).add(r["column_name"])
        assert not SECRET_COL.search(r["column_name"]), f"viewer can read {r['table_name']}.{r['column_name']}"
    for table, hidden in HIDDEN.items():
        assert not (seen.get(table, set()) & hidden), f"viewer can read {hidden & seen[table]} on {table}"
    # The new docs/28 tables: which ones the viewer gets, and in what shape.
    assert "secrets_enc" not in seen.get("connections", set()) and "connections" in seen
    assert "secret_enc" not in seen.get("inbound_hooks", set()) and "inbound_hooks" in seen
    assert "auth_config_enc" not in seen.get("mcp_servers", set()) and "mcp_servers" in seen
    for plain in ("agent_tool_bindings", "agent_mcp_bindings", "inbound_hook_events"):
        assert plain in seen, f"{plain} should be readable (no secret columns)"
    # No raw grant on a table that has a hidden column.
    for table in HIDDEN:
        assert not await conn.fetchval("SELECT has_table_privilege('viewer', $1, 'SELECT')", f"public.{table}")
