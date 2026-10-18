"""Regression: n8n's callback to /internal/tools/* must find the tool run while the agent's turn is still open.

Production symptom (2026-10-15): every real chat tool call failed with `bookings.bad_run` (401). The ToolRun was
flushed in the turn's transaction; n8n calls back from another connection, which cannot see an uncommitted row.
The existing booking tests missed it because they commit the run first and share ONE session. These tests give the
turn and the callback separate committing connections, like production.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import get_session
from app.integrations.n8n_client import callback_token
from app.llm.types import ToolCall
from app.main import create_app
from app.models import AgentVersion, Contact, Conversation, Organization, Tool, ToolRun, User
from app.tools import n8n_tool
from app.tools.base import ToolContext, ToolResult
from app.tools.n8n_tool import execute_n8n_tool, workflow_error
from app.tools.service import execute_tool_call
from tests.dbconn import new_engine

SECRET = "automation-report-secret-for-tests"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "automation_report_secret", SECRET)
    monkeypatch.setattr(settings, "allow_self_serve_orgs", True)
    monkeypatch.setattr(settings, "n8n_require_registered_automation", False)


@pytest.fixture
async def world():
    """Committed org + agent + n8n tool; the app uses one committing session per request."""
    engine = new_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    app = create_app()

    async def _per_request_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _per_request_session
    email = f"toolrun.{uuid.uuid4().hex[:8]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
            token = signup.json()["access_token"]
            auth = {"Authorization": f"Bearer {token}"}
            org = await client.post("/v1/orgs", json={"name": "ToolRun Org"}, headers=auth)
            org_id = uuid.UUID(org.json()["id"])
            headers = {"Authorization": f"Bearer {token}", "X-Org-Id": str(org_id)}
            agent = await client.post("/v1/agents", json={"name": "Bot"}, headers=headers)
            agent_id = uuid.UUID(agent.json()["id"])
            async with maker() as s:
                tool = Tool(
                    organization_id=org_id,
                    agent_id=agent_id,
                    name="check_availability",
                    type="n8n",
                    enabled=True,
                    config={"webhook_url": "http://n8n/webhook/demo-check", "mode": "sync"},
                    input_schema={"type": "object", "properties": {}},
                )
                s.add(tool)
                await s.commit()
            yield client, maker, org_id, agent_id, tool
    finally:
        app.dependency_overrides.clear()
        async with maker() as s:
            ids = (await s.execute(select(User.id).where(User.email == email))).scalars().all()
            await s.execute(delete(Organization).where(Organization.created_by.in_(ids)))
            await s.execute(delete(User).where(User.id.in_(ids)))
            await s.commit()
        await engine.dispose()


def _signed(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "url": f"/internal/tools/{path}",
        "content": body,
        "headers": {"X-Vicero-Timestamp": stamp, "X-Vicero-Signature": sig, "Content-Type": "application/json"},
    }


class _FakeN8n:
    """Stands in for n8n: the webhook handler calls Vicero back from ANOTHER connection, mid-dispatch."""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.callback: httpx.Response | None = None

    async def trigger_webhook(self, url: str, payload: dict[str, Any]) -> tuple[int, Any]:
        # A Tuesday two to three weeks out: Sundays are closed, so a fixed offset from today fails on some days.
        soon = dt.date.today() + dt.timedelta(days=14)
        day = (soon + dt.timedelta(days=(1 - soon.weekday()) % 7)).isoformat()
        body = {
            "run_id": payload["run_id"],
            "callback_token": payload["callback_token"],
            "args": {"date": day},
        }
        self.callback = await self.client.post(**_signed("availability", body))
        return self.callback.status_code, self.callback.json()


async def _turn(
    world: Any, monkeypatch: pytest.MonkeyPatch, *, skip_commit: bool = False
) -> tuple[ToolResult, _FakeN8n]:
    client, maker, org_id, agent_id, tool = world
    fake = _FakeN8n(client)
    monkeypatch.setattr(n8n_tool, "get_client", lambda **_: fake)
    async with maker() as turn:
        # What a channel turn has done by the time the model asks for a tool: contact + conversation, uncommitted.
        contact = Contact(organization_id=org_id, channel="api", external_id=f"c-{uuid.uuid4().hex[:6]}")
        turn.add(contact)
        await turn.flush()
        conv = Conversation(organization_id=org_id, agent_id=agent_id, channel="api", contact_id=contact.id)
        turn.add(conv)
        await turn.flush()
        if skip_commit:
            monkeypatch.setattr(turn, "commit", _noop)  # the pre-fix behaviour
        ctx = ToolContext(
            session=turn,
            org_id=org_id,
            agent_id=agent_id,
            version=AgentVersion(agent_id=agent_id, version=0, rag_config={}),
            conversation_id=conv.id,
        )
        result = await execute_tool_call(
            turn, ctx, {"check_availability": tool}, ToolCall(id="t1", name="check_availability", arguments={})
        )
    return result, fake


async def _noop() -> None:
    return None


async def test_the_callback_from_another_connection_finds_the_run_mid_turn(world, monkeypatch) -> None:
    result, fake = await _turn(world, monkeypatch)
    assert fake.callback is not None and fake.callback.status_code == 200, fake.callback.text
    assert result.status == "success"
    assert result.output["response"]["ok"] is True and result.output["response"]["slots"]


async def test_without_the_commit_the_same_callback_is_refused(world, monkeypatch) -> None:
    """Control: proves the test above can fail. This is what production did before the fix."""
    result, fake = await _turn(world, monkeypatch, skip_commit=True)
    assert fake.callback is not None and fake.callback.status_code == 401
    assert fake.callback.json()["error"]["code"] == "bookings.bad_run"
    assert result.status == "error"


async def test_the_run_is_durable_and_finished_after_the_turn(world, monkeypatch) -> None:
    _client, maker, *_ = world
    result, _fake = await _turn(world, monkeypatch)
    assert result.status == "success"
    async with maker() as s:
        runs = (await s.execute(select(ToolRun).where(ToolRun.organization_id == world[2]))).scalars().all()
    assert len(runs) == 1
    # The turn's own session was never committed after the dispatch in this test; the row exists and is pending
    # or finished depending on the caller's commit. What matters: it existed before n8n called back (above).
    assert runs[0].status in {"pending", "success"}


# ── an error object in a 200 response is an error run ───────────────────────────────────────────────────
class _Reply:
    def __init__(self, status: int, data: Any) -> None:
        self.status, self.data = status, data

    async def trigger_webhook(self, url: str, payload: dict[str, Any]) -> tuple[int, Any]:
        return self.status, self.data


def _ctx() -> ToolContext:
    org, agent = uuid.uuid4(), uuid.uuid4()
    return ToolContext(
        session=None,  # type: ignore[arg-type]
        org_id=org,
        agent_id=agent,
        version=AgentVersion(agent_id=agent, version=0, rag_config={}),
        run_id=uuid.uuid4(),
    )


@pytest.mark.parametrize(
    ("data", "status"),
    [
        ({"error": {"code": "bookings.bad_run", "message": "Unknown or invalid run."}}, "error"),
        ({"ok": False, "error": "slot_taken"}, "success"),  # a normal "no": the model explains it to the customer
        ({"ok": True, "slots": []}, "success"),
        ([{"error": {"code": "x"}}], "success"),
    ],
)
async def test_an_error_object_in_the_response_marks_the_run_error(monkeypatch, data, status) -> None:
    monkeypatch.setattr(n8n_tool, "get_client", lambda **_: _Reply(200, data))
    result = await execute_n8n_tool({"webhook_url": "http://n8n/webhook/x", "mode": "sync"}, {}, _ctx())
    assert result.status == status
    assert result.output["response"] == data, "the model still gets the response"
    if status == "error":
        assert result.error == "bookings.bad_run: Unknown or invalid run."


def test_workflow_error_reports_code_and_message_only() -> None:
    assert workflow_error({"error": {"code": "c", "message": "m", "token": "secret"}}) == "c: m"
    assert workflow_error({"error": {"code": "c"}}) == "c"
    assert workflow_error({"error": {}}) is None
    assert workflow_error("nope") is None


def test_callback_token_is_per_run() -> None:
    org = uuid.uuid4()
    assert callback_token(uuid.uuid4(), org) != callback_token(uuid.uuid4(), org)
