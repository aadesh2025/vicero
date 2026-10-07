"""The request's DB work is committed BEFORE the response is sent (regression for the 401-after-signup race).

`get_session` commits in the cleanup of a request-scoped yield dependency, which FastAPI runs after the response
has gone out. A client that fires its next call the moment it sees the 200 (signup, then POST /v1/orgs with the
new token) could therefore reach the API before the commit and get `401 auth.invalid_token` for a token it had
just been given. It never showed in the in-process test client (that waits for the whole app call), so this test
drives the app at the ASGI level and records the ORDER of the two events.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import get_session
from app.main import create_app
from app.models import User
from tests.dbconn import new_engine


async def _post(app, path: str, payload: dict[str, object], events: list[str]) -> int:  # type: ignore[no-untyped-def]
    body = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    sent = False
    status = 0

    async def receive() -> dict[str, object]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            events.append("response_start")
            status = int(message["status"])  # type: ignore[call-overload]

    await app(scope, receive, send)
    return status


async def test_signup_is_committed_before_the_response_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = new_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    events: list[str] = []

    real_commit = AsyncSession.commit

    async def spy(self: AsyncSession, *a: object, **k: object) -> None:
        events.append("commit")
        await real_commit(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(AsyncSession, "commit", spy)

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
    email = f"order.{uuid.uuid4().hex[:12]}@example.com"
    try:
        status = await _post(app, "/v1/auth/signup", {"email": email, "password": "password123"}, events)
        assert status == 200
        assert "response_start" in events and "commit" in events, events
        # The first commit must precede the first byte of the response.
        assert events.index("commit") < events.index("response_start"), events
    finally:
        app.dependency_overrides.clear()
        async with maker() as s:
            await s.execute(delete(User).where(User.email == email))
            await s.commit()
        await engine.dispose()
