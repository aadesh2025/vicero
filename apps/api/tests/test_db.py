"""Phase 1: UUIDv7, model registration, and tenant-isolation tests."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import uuid7
from app.db.repository import BaseRepository

EXPECTED_TABLES = {
    "users", "oauth_accounts", "magic_link_tokens", "password_reset_tokens", "sessions",
    "email_verification_tokens", "organizations", "memberships", "invitations",
    "agents", "agent_versions", "provider_credentials",
    "knowledge_bases", "documents", "chunks",
    "canned_responses", "contacts", "crm_contacts", "conversations", "conversation_flags",
    "messages",
    "tools", "tool_runs", "channels", "handoffs", "macros",
    "api_keys", "webhook_endpoints", "webhook_deliveries",
    "audit_logs", "usage_records", "quotas", "subscriptions",
    "feature_flags", "help_articles", "campaigns", "widget_configs",
    # docs/17 Phase 1 (agentic runtime): the per-turn trace and org-scoped MCP registry.
    "agent_steps", "mcp_servers",
    # docs/22 Phase A1 (paid plans): admin grant history, the payment ledger, storage accounting.
    "plan_grants", "billing_cycles", "org_storage_usage",
    # docs/17 Phase 2 (visual workflow builder): draft/publish + execution-trace tables.
    "workflows", "workflow_versions", "workflow_runs", "workflow_steps",
    # docs/17 Phase 3 (agent testing): scenario definitions + per-run actual-vs-expected results.
    "agent_tests", "agent_test_runs",
    # docs/17 Phase 4 follow-up (workflow testing, ADR-081): the workflow-shaped twin of the
    # pair above, closing the WORKFLOWS_PUBLISH test-failure gate.
    "workflow_tests", "workflow_test_runs",
    # docs/18 (self-serve trial, ADR-088): the per-org message counter.
    "org_message_usage",
}


# ── UUIDv7 ────────────────────────────────────────────────────────────────────
def test_uuid7_version_and_variant() -> None:
    u = uuid7()
    assert u.version == 7
    assert (u.int >> 62) & 0b11 == 0b10  # RFC 9562 variant


def test_uuid7_is_time_ordered() -> None:
    a = uuid7()
    time.sleep(0.005)
    b = uuid7()
    assert a < b


def test_uuid7_unique() -> None:
    assert len({uuid7() for _ in range(2000)}) == 2000


# ── Model registration ───────────────────────────────────────────────────────
def test_all_models_registered() -> None:
    import app.models  # noqa: F401  -- registers tables
    from app.db.base import Base

    assert EXPECTED_TABLES <= set(Base.metadata.tables)
    assert len(Base.metadata.tables) == len(EXPECTED_TABLES)


# ── Tenant isolation (repository) ─────────────────────────────────────────────
class _TBase(DeclarativeBase):
    pass


class _Thing(_TBase):
    __tablename__ = "things"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)


class _ThingRepo(BaseRepository[_Thing]):  # type: ignore[type-var]
    model = _Thing


@pytest.fixture
async def sqlite_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_TBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_repository_scopes_to_organization(sqlite_session: AsyncSession) -> None:
    repo_a: _ThingRepo = _ThingRepo(sqlite_session, "org-a")  # type: ignore[arg-type]
    repo_b: _ThingRepo = _ThingRepo(sqlite_session, "org-b")  # type: ignore[arg-type]

    repo_a.add(_Thing(id="1", name="a1"))
    repo_a.add(_Thing(id="2", name="a2"))
    repo_b.add(_Thing(id="3", name="b1"))
    await sqlite_session.commit()

    page_a = await repo_a.list()
    assert page_a.total == 2
    assert {t.name for t in page_a.items} == {"a1", "a2"}

    # Cross-org access returns nothing.
    assert await repo_a.get("3") is None  # type: ignore[arg-type]
    assert await repo_b.count() == 1
    assert await repo_a.count() == 2
