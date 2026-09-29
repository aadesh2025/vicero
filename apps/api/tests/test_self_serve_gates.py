"""Plan gates on a trial workspace — API and runtime (docs/18 §5, §9, §13)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.plans import PLANS
from app.models import (
    Agent,
    AgentVersion,
    Organization,
    Tool,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.tools.service import build_tooling
from app.workflows import service as workflow_service
from tests.selfserve_helpers import (
    bearer,
    make_agent_public,
    org_headers,
    set_trial_end,
    signup,
    trial_org,
    unique_email,
    verify_email,
)

pytestmark = pytest.mark.usefixtures("self_serve")


def _assert_plan_limit(resp, feature: str) -> None:  # type: ignore[no-untyped-def]
    assert resp.status_code == 402, resp.text
    error = resp.json()["error"]
    assert error["code"] == "plan_limit"
    assert error["details"]["feature"] == feature
    assert error["message"]


# ── API gates ────────────────────────────────────────────────────────────────
async def test_the_first_agent_is_allowed_and_the_second_is_a_402(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    assert (await client.post("/v1/agents", json={"name": "One"}, headers=headers)).status_code == 201
    _assert_plan_limit(await client.post("/v1/agents", json={"name": "Two"}, headers=headers), "agents")


async def test_duplicating_the_agent_counts_as_a_second_agent(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    first = await client.post("/v1/agents", json={"name": "One"}, headers=headers)
    _assert_plan_limit(
        await client.post(f"/v1/agents/{first.json()['id']}/duplicate", headers=headers), "agents"
    )


async def test_a_deleted_agent_frees_the_slot(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    first = await client.post("/v1/agents", json={"name": "One"}, headers=headers)
    assert (await client.delete(f"/v1/agents/{first.json()['id']}", headers=headers)).status_code == 204
    assert (await client.post("/v1/agents", json={"name": "Again"}, headers=headers)).status_code == 201


async def test_workflows_are_locked(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    agent = await client.post("/v1/agents", json={"name": "One"}, headers=headers)
    resp = await client.post(
        f"/v1/agents/{agent.json()['id']}/workflows", json={"name": "Flow"}, headers=headers
    )
    _assert_plan_limit(resp, "workflows")


async def test_n8n_is_locked(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    _assert_plan_limit(await client.get("/v1/tools/n8n/workflows", headers=headers), "n8n")
    bind = await client.post(
        "/v1/tools/n8n/bind",
        json={"name": "hook", "webhook_url": "https://example.com/hook"},
        headers=headers,
    )
    _assert_plan_limit(bind, "n8n")


async def test_custom_tools_are_locked(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    http = await client.post(
        "/v1/tools",
        json={"name": "lookup", "type": "http", "config": {"url": "https://example.com/api"}},
        headers=headers,
    )
    _assert_plan_limit(http, "tool_calling")
    builtin = await client.post(
        "/v1/tools", json={"name": "get_datetime", "type": "builtin"}, headers=headers
    )
    _assert_plan_limit(builtin, "tool_calling")


async def test_mcp_servers_are_locked(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    resp = await client.post(
        "/v1/mcp/servers",
        json={"name": "docs", "transport": "sse", "url_or_command": "https://mcp.example.com/sse"},
        headers=headers,
    )
    _assert_plan_limit(resp, "tool_calling")


async def test_reading_locked_features_still_works_so_the_ui_can_show_them(
    client: AsyncClient,
) -> None:
    _, headers, _ = await trial_org(client)
    assert (await client.get("/v1/tools", headers=headers)).status_code == 200
    assert (await client.get("/v1/mcp/servers", headers=headers)).status_code == 200


async def test_the_gate_is_on_the_server_not_the_ui(client: AsyncClient) -> None:
    """A direct call with a valid token and role gets the same answer a UI click would."""
    auth = await signup(client)
    headers = await org_headers(client, auth)
    assert headers["Authorization"] == bearer(auth)["Authorization"]
    _assert_plan_limit(
        await client.post("/v1/tools", json={"name": "x", "type": "builtin"}, headers=headers),
        "tool_calling",
    )


# ── legacy workspaces are untouched ──────────────────────────────────────────
async def test_a_legacy_workspace_keeps_everything(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staff-provisioned tenants (and every pre-existing org, via the backfill) are unmetered."""
    monkeypatch.setattr(settings, "allow_self_serve_orgs", True)  # the operator/test bootstrap path
    auth = await signup(client)  # self-serve on: also gets a trial workspace, which we ignore
    org = await client.post("/v1/orgs", json={"name": "Client Co"}, headers=bearer(auth))
    assert org.status_code == 201, org.text
    assert org.json()["plan"] == "legacy"
    headers = {**bearer(auth), "X-Org-Id": org.json()["id"]}

    plan = (await client.get(f"/v1/orgs/{org.json()['id']}/plan", headers=headers)).json()
    assert plan["status"] == "legacy" and plan["messages_limit"] is None
    assert plan["features"] == {"workflows": True, "n8n": True, "tool_calling": True}

    for name in ("One", "Two", "Three"):
        assert (await client.post("/v1/agents", json={"name": name}, headers=headers)).status_code == 201
    agent = (await client.get("/v1/agents", headers=headers)).json()[0]
    assert (
        await client.post(f"/v1/agents/{agent['id']}/workflows", json={"name": "F"}, headers=headers)
    ).status_code == 201
    assert (
        await client.post("/v1/tools", json={"name": "get_datetime", "type": "builtin"}, headers=headers)
    ).status_code == 201


async def test_an_org_with_no_recorded_plan_is_not_locked(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-safe: a code path that forgets to set a plan gets the unlimited one."""
    monkeypatch.setattr(settings, "allow_self_serve_orgs", True)
    auth = await signup(client)
    org = await client.post("/v1/orgs", json={"name": "Bare"}, headers=bearer(auth))
    row = await db_session.get(Organization, uuid.UUID(org.json()["id"]))
    assert row is not None and row.plan == "legacy"
    assert row.trial_ends_at is None


# ── runtime: not just the CRUD gates ─────────────────────────────────────────
async def _attach_tool(db: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID) -> None:
    db.add(
        Tool(
            organization_id=org_id,
            agent_id=agent_id,
            name="get_datetime",
            type="builtin",
            description="time",
            enabled=True,
            config={},
            input_schema={"type": "object", "properties": {}},
        )
    )
    await db.flush()


async def test_the_runtime_does_not_attach_tools_on_a_trial_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await make_agent_public(client, headers)
    agent_row = (
        await db_session.execute(select(Agent).where(Agent.organization_id == uuid.UUID(org_id)))
    ).scalar_one()
    version = (
        await db_session.execute(select(AgentVersion).where(AgentVersion.agent_id == agent_row.id))
    ).scalars().first()
    assert version is not None
    version.features = {**(version.features or {}), "tools_enabled": True}
    await _attach_tool(db_session, uuid.UUID(org_id), agent_row.id)

    specs, executor = await build_tooling(db_session, uuid.UUID(org_id), agent_row, version, None)
    assert specs == [] and executor is None

    # Control: the very same setup on an unmetered org does attach the tool — so the assertion
    # above is the gate working, not the tool never having been attached.
    org = await db_session.get(Organization, uuid.UUID(org_id))
    assert org is not None
    org.plan = "legacy"
    await db_session.flush()
    specs, executor = await build_tooling(db_session, uuid.UUID(org_id), agent_row, version, None)
    assert [s.name for s in specs] == ["get_datetime"] and executor is not None


async def test_the_runtime_does_not_execute_workflows_on_a_trial_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, _, org_id = await trial_org(client)
    org = await db_session.get(Organization, uuid.UUID(org_id))
    assert org is not None
    wf = Workflow(organization_id=org.id, name="Leftover")
    db_session.add(wf)
    await db_session.flush()
    version = WorkflowVersion(workflow_id=wf.id, version=1, status="published", graph={})
    db_session.add(version)
    await db_session.flush()
    run = WorkflowRun(
        workflow_version_id=version.id, organization_id=org.id, status="running",
        variables={}, budget={}, is_test=False,
    )
    db_session.add(run)
    await db_session.flush()

    result = await workflow_service._execute_and_persist(db_session, run, wf, version)
    assert result.status == "failed"
    assert run.status == "failed" and "plan_limit" in (run.error or "")
    assert run.completed_at is not None


# ── expired trial: agents are read-only ──────────────────────────────────────
async def test_an_expired_trial_keeps_the_agent_readable_but_not_editable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    agent = (await client.post("/v1/agents", json={"name": "One"}, headers=headers)).json()
    await set_trial_end(db_session, org_id, dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=1))

    assert (await client.get(f"/v1/agents/{agent['id']}", headers=headers)).status_code == 200
    assert (await client.get("/v1/agents", headers=headers)).status_code == 200
    _assert_plan_limit(
        await client.patch(f"/v1/agents/{agent['id']}", json={"name": "Renamed"}, headers=headers), "agents"
    )
    _assert_plan_limit(
        await client.patch(
            f"/v1/agents/{agent['id']}/versions/1", json={"welcome_message": "hi"}, headers=headers
        ),
        "agents",
    )
    _assert_plan_limit(await client.post("/v1/agents", json={"name": "Two"}, headers=headers), "agents")
    _assert_plan_limit(
        await client.post(
            f"/v1/agents/{agent['id']}/playground/chat", json={"message": "hi", "stream": False}, headers=headers
        ),
        "agents",
    )


# ── the dashboard test chat is capped per day on a live trial ────────────────
async def test_the_playground_is_capped_per_day(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PLANS, "trial", dataclasses.replace(PLANS["trial"], playground_per_day=2))
    _, headers, _ = await trial_org(client)
    await make_agent_public(client, headers)
    agent = (await client.get("/v1/agents", headers=headers)).json()[0]
    url = f"/v1/agents/{agent['id']}/playground/chat"
    body = {"message": "hello", "stream": False}
    assert (await client.post(url, json=body, headers=headers)).status_code == 200
    assert (await client.post(url, json=body, headers=headers)).status_code == 200
    _assert_plan_limit(await client.post(url, json=body, headers=headers), "playground")


async def test_playground_messages_do_not_spend_the_visitor_message_allowance(
    client: AsyncClient,
) -> None:
    _, headers, org_id = await trial_org(client)
    await make_agent_public(client, headers)
    agent = (await client.get("/v1/agents", headers=headers)).json()[0]
    ok = await client.post(
        f"/v1/agents/{agent['id']}/playground/chat", json={"message": "hi", "stream": False}, headers=headers
    )
    assert ok.status_code == 200, ok.text
    plan = (await client.get(f"/v1/orgs/{org_id}/plan", headers=headers)).json()
    assert plan["messages_used"] == 0


# ── going live needs a verified address ──────────────────────────────────────
async def test_publishing_needs_a_verified_email_on_a_trial(client: AsyncClient) -> None:
    _, headers, _ = await trial_org(client)
    agent = (await client.post("/v1/agents", json={"name": "One"}, headers=headers)).json()
    blocked = await client.post(
        f"/v1/agents/{agent['id']}/versions/1/publish", json={}, headers=headers
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"]["code"] == "auth.email_unverified"

    await verify_email(client)
    ok = await client.post(f"/v1/agents/{agent['id']}/versions/1/publish", json={}, headers=headers)
    assert ok.status_code == 200, ok.text


async def test_enabling_a_channel_needs_a_verified_email_on_a_trial(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    # Trial's channel allowlist is web-only (docs/22 §11); this test is about the email-verify
    # gate specifically, so use a plan that includes telegram and still requires verification.
    org = await db_session.get(Organization, uuid.UUID(org_id))
    assert org is not None
    org.plan = "business"
    await db_session.flush()
    agent = (await client.post("/v1/agents", json={"name": "One"}, headers=headers)).json()
    channel = await client.post(
        "/v1/channels",
        json={"agent_id": agent["id"], "type": "telegram", "config": {"bot_token": "123:abc"}},
        headers=headers,
    )
    assert channel.status_code == 201, channel.text
    blocked = await client.post(f"/v1/channels/{channel.json()['id']}/enable", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "auth.email_unverified"


async def test_an_unverified_legacy_user_can_still_publish(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing clients may never have verified an address; that must not lock them out."""
    monkeypatch.setattr(settings, "allow_self_serve_orgs", True)
    auth = await signup(client, unique_email("legacy"))
    org = await client.post("/v1/orgs", json={"name": "Old Client"}, headers=bearer(auth))
    headers = {**bearer(auth), "X-Org-Id": org.json()["id"]}
    agent = (await client.post("/v1/agents", json={"name": "One"}, headers=headers)).json()
    ok = await client.post(f"/v1/agents/{agent['id']}/versions/1/publish", json={}, headers=headers)
    assert ok.status_code == 200, ok.text
