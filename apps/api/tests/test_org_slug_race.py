"""Concurrent organization creation with the same name must not 500 on the slug (regression).

`_unique_slug` is check-then-insert: two simultaneous requests with the same name both saw "acme" free and one
died on `ix_organizations_slug` with a 500. Found by firing 200 signup-then-create-org runs at a real server with
8 workers (28 of 200 failed). These tests give every request its own committing connection, like production.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import get_session
from app.main import create_app
from app.models import Organization, User
from tests.dbconn import new_engine

WORKERS = 10


@pytest.fixture
async def committed():
    engine = new_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    app = create_app()

    async def _per_request_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:  # same commit/rollback contract as app.db.session.get_session
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _per_request_session
    created_emails: list[str] = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, maker, created_emails
    finally:
        app.dependency_overrides.clear()
        async with maker() as s:
            ids = (await s.execute(select(User.id).where(User.email.in_(created_emails)))).scalars().all()
            await s.execute(delete(Organization).where(Organization.created_by.in_(ids)))
            await s.execute(delete(User).where(User.id.in_(ids)))
            await s.commit()
        await engine.dispose()


async def test_same_name_created_concurrently_gets_distinct_slugs(committed, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "allow_self_serve_orgs", True)
    client, maker, emails = committed
    tag = uuid.uuid4().hex[:8]
    name = f"Slug Race {tag}"

    tokens = []
    for i in range(WORKERS):
        email = f"slug.{tag}.{i}@example.com"
        emails.append(email)
        r = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
        assert r.status_code == 200, r.text
        tokens.append(r.json()["access_token"])

    results = await asyncio.gather(
        *[client.post("/v1/orgs", json={"name": name}, headers={"Authorization": f"Bearer {t}"}) for t in tokens]
    )
    assert [r.status_code for r in results] == [201] * WORKERS, [(r.status_code, r.text[:80]) for r in results]

    slugs = [r.json()["slug"] for r in results]
    assert len(set(slugs)) == WORKERS, slugs
    assert all(s.startswith(f"slug-race-{tag}") for s in slugs)
    async with maker() as s:
        stored = (await s.execute(select(Organization.slug).where(Organization.slug.in_(slugs)))).scalars().all()
    assert len(stored) == WORKERS


async def test_a_lone_name_keeps_its_plain_slug(committed, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "allow_self_serve_orgs", True)
    client, _maker, emails = committed
    tag = uuid.uuid4().hex[:8]
    email = f"slug.solo.{tag}@example.com"
    emails.append(email)
    token = (await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})).json()[
        "access_token"
    ]
    r = await client.post("/v1/orgs", json={"name": f"Solo {tag}"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201 and r.json()["slug"] == f"solo-{tag}"
