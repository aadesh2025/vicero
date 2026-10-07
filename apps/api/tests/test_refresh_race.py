"""Refresh-token rotation under real concurrency (live incident 2026-10-07).

The shared `client` fixture runs every request on ONE rolled-back DB session, which serialises them and so
can never show a race. These tests give each request its own real, committing connection, like production.
They create one throwaway user and delete it (sessions cascade) when done.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app.core.security import generate_opaque_token, hash_token
from app.db.session import get_session
from app.main import create_app
from app.models import Session, User
from tests.dbconn import new_engine

CONCURRENCY = 12


@pytest.fixture
async def committed():
    """(client, sessionmaker, user_id, refresh_token) with every request on its own committing session."""
    engine = new_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    token = generate_opaque_token()
    user_id = uuid.uuid4()
    async with maker() as s:
        s.add(
            User(
                id=user_id,
                email=f"race.{user_id.hex[:12]}@example.com",
                email_normalized=f"race.{user_id.hex[:12]}@example.com",
                email_verified_at=dt.datetime.now(tz=dt.UTC),
                is_active=True,
            )
        )
        await s.flush()
        s.add(
            Session(
                user_id=user_id,
                refresh_token_hash=hash_token(token),
                expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=30),
            )
        )
        await s.commit()

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
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, maker, user_id, token
    finally:
        app.dependency_overrides.clear()
        async with maker() as s:
            await s.execute(delete(User).where(User.id == user_id))  # sessions cascade
            await s.commit()
        await engine.dispose()


def _code(resp) -> str:
    return resp.json()["error"]["code"]


async def test_concurrent_refresh_exactly_one_succeeds(committed) -> None:
    client, maker, user_id, token = committed

    results = await asyncio.gather(
        *[client.post("/v1/auth/refresh", json={"refresh_token": token}) for _ in range(CONCURRENCY)]
    )

    ok = [r for r in results if r.status_code == 200]
    losers = [r for r in results if r.status_code != 200]
    assert len(ok) == 1, [r.status_code for r in results]
    assert len(losers) == CONCURRENCY - 1
    assert all(r.status_code == 401 for r in losers)
    # Every loser is told it lost a race (keep your cookie), not that the token is dead.
    assert {_code(r) for r in losers} == {"auth.refresh_rotated"}

    # No fork: exactly one live session remains, and it is the winner's new one.
    async with maker() as s:
        live = (
            await s.execute(
                select(func.count())
                .select_from(Session)
                .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            )
        ).scalar_one()
    assert live == 1
    # The winner's new refresh token keeps working: losing a race never burns the survivor.
    again = await client.post("/v1/auth/refresh", json={"refresh_token": ok[0].json()["refresh_token"]})
    assert again.status_code == 200


async def _session_count(maker, user_id) -> int:
    async with maker() as s:
        return (
            await s.execute(select(func.count()).select_from(Session).where(Session.user_id == user_id))
        ).scalar_one()


async def test_rotated_vs_dead_error_codes(committed) -> None:
    client, maker, user_id, token = committed

    assert (await client.post("/v1/auth/refresh", json={"refresh_token": token})).status_code == 200

    # Just rotated: a duplicate is a race, not a logout.
    dup = await client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert dup.status_code == 401
    assert _code(dup) == "auth.refresh_rotated"
    # Inside the grace window a replay gets an error only: no tokens, and no new session is minted.
    assert "access_token" not in dup.text and "refresh_token" not in dup.text
    assert await _session_count(maker, user_id) == 2  # the rotated-away row + the winner's one

    # Rotated long ago: genuinely dead, and the reuse leaves a security trail (ids only).
    async with maker() as s:
        await s.execute(
            update(Session)
            .where(Session.refresh_token_hash == hash_token(token))
            .values(revoked_at=dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=5))
        )
        await s.commit()
    with capture_logs() as logs:
        dead = await client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert dead.status_code == 401
    assert _code(dead) == "auth.invalid_token"
    assert "access_token" not in dead.text and "refresh_token" not in dead.text
    assert await _session_count(maker, user_id) == 2  # still no new session
    reuse = [e for e in logs if e["event"] == "refresh_token_reuse"]
    assert len(reuse) == 1 and reuse[0]["user_id"] == str(user_id)
    assert token not in str(logs) and hash_token(token) not in str(logs)

    # Never existed: dead too.
    unknown = await client.post("/v1/auth/refresh", json={"refresh_token": generate_opaque_token()})
    assert unknown.status_code == 401
    assert _code(unknown) == "auth.invalid_token"


async def test_expired_session_is_dead_not_rotated(committed) -> None:
    client, maker, _user_id, token = committed
    async with maker() as s:
        await s.execute(
            update(Session)
            .where(Session.refresh_token_hash == hash_token(token))
            .values(expires_at=dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1))
        )
        await s.commit()
    resp = await client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
    assert _code(resp) == "auth.invalid_token"
