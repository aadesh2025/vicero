"""The tool-run log API: filters, cursor paging, the 7-day summary, and the sanitized test request (docs/28 T5.6)."""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolRun
from tests.conftest import HttpMock

SECRET = "sk-live-abcdef1234567890"


async def _org(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "RunsOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _setup(client: AsyncClient, headers: dict[str, str]) -> tuple[str, str, str]:
    agent = (await client.post("/v1/agents", json={"name": "Bot"}, headers=headers)).json()
    conn = (
        await client.post(
            "/v1/connections",
            json={
                "name": "shop",
                "base_url": "https://api.shop.example/v2",
                "auth_type": "bearer",
                "auth": {"token": SECRET},
            },
            headers=headers,
        )
    ).json()
    tool = (
        await client.post(
            "/v1/tools",
            json={
                "name": "get_order",
                "type": "api",
                "agent_id": agent["id"],
                "connection_id": conn["id"],
                "config": {"method": "GET", "path": "/orders/{{order_id}}"},
            },
            headers=headers,
        )
    ).json()
    return agent["id"], tool["id"], conn["id"]


async def _seed(db: AsyncSession, org_id: str, agent_id: str, tool_id: str, statuses: list[str]) -> None:
    now = dt.datetime.now(tz=dt.UTC)
    for i, status in enumerate(statuses):
        run = ToolRun(
            organization_id=uuid.UUID(org_id),
            tool_id=uuid.UUID(tool_id),
            agent_id=uuid.UUID(agent_id),
            provider="api",
            input={"i": i},
            status=status,
            latency_ms=10 + i,
        )
        db.add(run)
        await db.flush()
        run.created_at = now - dt.timedelta(minutes=i)
    await db.flush()


async def test_filters_and_cursor_paging(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await _org(client, "tr1@example.com")
    aid, tid, _ = await _setup(client, headers)
    await _seed(db_session, headers["X-Org-Id"], aid, tid, ["success", "error", "success", "success", "error"])

    page1 = (await client.get(f"/v1/tool-runs?agent_id={aid}&limit=2", headers=headers)).json()
    assert [r["input"]["i"] for r in page1["items"]] == [0, 1] and page1["next_cursor"]
    assert page1["items"][0]["tool_name"] == "get_order"
    page2 = (await client.get(f"/v1/tool-runs?limit=2&cursor={page1['next_cursor']}", headers=headers)).json()
    assert [r["input"]["i"] for r in page2["items"]] == [2, 3]
    page3 = (await client.get(f"/v1/tool-runs?limit=2&cursor={page2['next_cursor']}", headers=headers)).json()
    assert [r["input"]["i"] for r in page3["items"]] == [4] and page3["next_cursor"] is None

    errors = (await client.get(f"/v1/tool-runs?status=error&tool_id={tid}", headers=headers)).json()
    assert [r["input"]["i"] for r in errors["items"]] == [1, 4]
    none = (await client.get(f"/v1/tool-runs?agent_id={uuid.uuid4()}", headers=headers)).json()
    assert none["items"] == []
    bad = await client.get("/v1/tool-runs?cursor=garbage", headers=headers)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "tool_runs.bad_cursor"


async def test_summary_counts_the_last_seven_days_and_reports_the_latest_outcome(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _org(client, "tr2@example.com")
    aid, tid, _ = await _setup(client, headers)
    await _seed(db_session, headers["X-Org-Id"], aid, tid, ["error", "success", "success"])
    old = ToolRun(
        organization_id=uuid.UUID(headers["X-Org-Id"]),
        tool_id=uuid.UUID(tid),
        agent_id=uuid.UUID(aid),
        provider="api",
        input={},
        status="success",
    )
    db_session.add(old)
    await db_session.flush()
    old.created_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=9)
    await db_session.flush()
    rows = (await client.get(f"/v1/tool-runs/summary?agent_id={aid}", headers=headers)).json()
    assert len(rows) == 1 and rows[0]["tool_id"] == tid
    assert rows[0]["calls_7d"] == 3 and rows[0]["last_status"] == "error"


async def test_the_log_is_per_organization(client: AsyncClient, db_session: AsyncSession) -> None:
    a = await _org(client, "tr3a@example.com")
    b = await _org(client, "tr3b@example.com")
    aid, tid, _ = await _setup(client, a)
    await _seed(db_session, a["X-Org-Id"], aid, tid, ["success"])
    assert (await client.get("/v1/tool-runs", headers=b)).json()["items"] == []
    assert (await client.get("/v1/tool-runs/summary", headers=b)).json() == []
    assert len((await client.get("/v1/tool-runs", headers=a)).json()["items"]) == 1


async def test_the_test_endpoint_returns_the_sanitized_request(client: AsyncClient, mock_http: HttpMock) -> None:
    headers = await _org(client, "tr4@example.com")
    _aid, tid, _ = await _setup(client, headers)
    mock_http.handler = lambda r: httpx.Response(200, json={"ok": True, "echo": SECRET})
    r = (await client.post(f"/v1/tools/{tid}/test", json={"input": {"order_id": "A-1"}}, headers=headers)).json()
    assert r["status"] == "success" and r["request"]["method"] == "GET"
    assert r["request"]["url"].endswith("/v2/orders/A-1") and r["request"]["headers"]["Authorization"] == "••••"
    assert SECRET not in str(r)
