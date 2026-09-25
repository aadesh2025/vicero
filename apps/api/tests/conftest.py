"""Shared pytest fixtures.

DB-backed tests run against the real Postgres (migrated) inside a transaction that is
rolled back after each test, so nothing persists and tests stay isolated.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.email import get_email_backend
from app.db.session import get_session
from app.main import create_app

# Keep rate limiting out of the way of functional tests; it's unit-tested separately.
settings.auth_rate_limit = 100_000
# L2 calls a real model over the network. Off for the suite by default so no test makes a
# live Groq call (slow, costs tokens, and fails in CI where there is no key); the tests that
# exercise it turn it on and inject a mock transport, the same way the LLM tests do.
settings.guard_injection_enabled = False
# Same for L3 (policy/distress) — also a live model call on every turn.
settings.guard_policy_enabled = False
settings.guard_distress_enabled = False
# And stage-4 reranking, which is a live HTTP call on every retrieval. Off here means every
# existing retrieval test keeps asserting RRF's ordering, so the reranker cannot change a result
# nobody asked it to; `test_rerank.py` turns it on with a mock transport.
settings.rerank_enabled = False
# And Docling, which is an HTTP call to a container on every file ingest. Off here means every
# existing ingest test keeps exercising the legacy extractor it was written against;
# `test_converters.py` turns it on with a mock transport.
settings.docling_enabled = False
# And the embedding-endpoint probe, which is one HTTP GET on app startup. Every test that builds
# the app would otherwise reach for a real Ollama — succeeding on a dev machine that happens to
# run one and burning the timeout in CI, which is a test suite whose result depends on the host.
# `test_embedding_probe.py` turns it on with a mock transport.
settings.embedding_probe_enabled = False


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session  # shared across the test; no commit, rolled back at teardown

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _allow_self_serve_orgs() -> AsyncIterator[None]:
    """Let tests bootstrap a tenant through the public API.

    Production is staff-only with no first-org exception (`create_org`), but nearly every test
    file starts by signing up a throwaway user and POSTing /v1/orgs to get an isolated org.
    Making each of those users staff would be both a large edit and a lie about what the test
    is exercising. `tests/test_org_creation_gate.py` turns this back off and asserts the real
    production rule, so the gate is still covered.
    """
    from app.core.config import settings

    previous = settings.allow_self_serve_orgs
    settings.allow_self_serve_orgs = True
    yield
    settings.allow_self_serve_orgs = previous


@pytest.fixture(autouse=True)
def _self_serve_off_by_default() -> AsyncIterator[None]:
    """Keep the suite on the staff-provisioned model it was written against.

    Production defaults `SELF_SERVE_ENABLED` on (docs/18): signup then provisions a metered
    trial workspace. Nearly every test file signs up a throwaway user and POSTs /v1/orgs for an
    unmetered tenant, and would otherwise get a second, trial-limited workspace. The self-serve
    behaviour has its own files (`test_self_serve_*.py`) that turn this on explicitly.
    """
    from app.core.config import settings

    previous = settings.self_serve_enabled
    settings.self_serve_enabled = False
    yield
    settings.self_serve_enabled = previous


@pytest.fixture
def self_serve() -> AsyncIterator[None]:
    """Opt a test into production behaviour: self-serve signup on, the test bootstrap off.

    Requested by name, so it runs after the autouse defaults above and overrides them.
    """
    from app.core.config import settings

    saved = (
        settings.self_serve_enabled,
        settings.allow_self_serve_orgs,
        settings.signups_per_ip_per_day,
        settings.org_chat_rate_limit,
    )
    settings.self_serve_enabled = True
    settings.allow_self_serve_orgs = False
    settings.signups_per_ip_per_day = 100_000  # tests sign up dozens of users from one "IP"
    settings.org_chat_rate_limit = 100_000
    yield
    (
        settings.self_serve_enabled,
        settings.allow_self_serve_orgs,
        settings.signups_per_ip_per_day,
        settings.org_chat_rate_limit,
    ) = saved


@pytest.fixture(autouse=True)
def _clear_email_outbox() -> AsyncIterator[None]:
    get_email_backend().outbox.clear()
    yield
    get_email_backend().outbox.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Reset the rate limiter between tests.

    Clears the in-memory buckets and drops any cached async Redis client so it rebinds to the
    current test's event loop (the sync-TestClient WS test closes the previous one).
    """
    from app.core.ratelimit import limiter

    limiter._mem.clear()
    limiter._redis = None
    # In-memory only. Per-email/per-IP limits and login lockouts now key on real addresses, and a
    # shared Redis would carry their counters from one test run into the next (a lockout in run
    # N failing a login in run N+1). The Redis path is unit-tested in test_ratelimit.py.
    limiter._checked = True
