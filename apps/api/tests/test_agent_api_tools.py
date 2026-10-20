"""API tools + agent tool bindings through the real chat path (docs/28 T1.4, T1.5)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.fake import ScriptedToolProvider
from app.llm.types import ToolCall
from app.models import AgentStep, AgentToolBinding, Message, Tool, ToolRun
from app.tools.service import resolve_agent_tools
from tests.conftest import HttpMock

SECRET = "sk-live-abcdef1234567890"
CONN = {
    "name": "shop",
    "base_url": "https://api.shop.example/v2",
    "auth_type": "bearer",
    "auth": {"token": SECRET},
}
ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}


async def _org(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "ApiToolOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _agent(client: AsyncClient, headers: dict[str, str], name: str = "Bot") -> str:
    agent = await client.post("/v1/agents", json={"name": name}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"features": {"tools_enabled": True, "memory_enabled": True, "handoff_enabled": False}},
        headers=headers,
    )
    return aid


async def _order_tool(client: AsyncClient, headers: dict[str, str], agent_id: str | None, conn_id: str) -> dict:
    r = await client.post(
        "/v1/tools",
        json={
            "name": "get_order",
            "type": "api",
            "agent_id": agent_id,
            "connection_id": conn_id,
            "description": "Look up an order by id.",
            "config": {"method": "GET", "path": "/orders/{{order_id}}", "response": {"pick": ["$.status", "$.eta"]}},
            "input_schema": ORDER_SCHEMA,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _script(monkeypatch: pytest.MonkeyPatch, args: dict) -> None:
    from app.modules.conversations import service

    call = ToolCall(id="c1", name="get_order", arguments=args)

    async def _provider(*_a: object, **_k: object) -> ScriptedToolProvider:
        return ScriptedToolProvider(call, answer="It ships tomorrow.")

    monkeypatch.setattr(service, "_resolve_provider", lambda *a, **k: _provider())


async def test_api_tool_runs_in_a_real_turn_and_leaks_nothing(
    client: AsyncClient, db_session: AsyncSession, mock_http: HttpMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "leak@example.com")
    aid = await _agent(client, headers)
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    await _order_tool(client, headers, aid, conn["id"])
    # The upstream echoes the credential in every place a careless integration might.
    mock_http.handler = lambda r: httpx.Response(
        200,
        json={"status": "shipped", "eta": "tomorrow", "debug": SECRET},
        headers={"x-echo-auth": r.headers.get("authorization", "")},
    )
    _script(monkeypatch, {"order_id": "A-100/../x"})

    resp = await client.post(
        f"/v1/agents/{aid}/chat", json={"message": "where is my order", "stream": False}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "It ships tomorrow."

    sent = mock_http.requests[0]
    assert sent.headers["authorization"] == f"Bearer {SECRET}" and sent.headers["host"] == "api.shop.example"
    assert sent.url.raw_path == b"/v2/orders/A-100%2F..%2Fx"

    run = (await db_session.execute(select(ToolRun).where(ToolRun.agent_id == uuid.UUID(aid)))).scalar_one()
    assert run.provider == "api" and run.status == "success" and run.attempt == 1
    assert run.output == {"status_code": 200, "data": {"status": "shipped", "eta": "tomorrow"}}
    assert run.request_sanitized["headers"]["Authorization"] == "••••"

    # Secret-leak grep: every row this turn persisted, serialised, must be free of the credential.
    msgs = (await db_session.execute(select(Message.content))).scalars().all()
    steps = (await db_session.execute(select(AgentStep.tool_output, AgentStep.tool_input))).all()
    runs = (
        await db_session.execute(select(ToolRun.input, ToolRun.output, ToolRun.request_sanitized, ToolRun.error))
    ).all()
    dump = json.dumps([list(map(str, msgs)), [list(map(str, s)) for s in steps], [list(map(str, r)) for r in runs]])
    assert SECRET not in dump
    assert resp.text.count(SECRET) == 0


async def test_invalid_model_arguments_come_back_as_a_structured_error(
    client: AsyncClient, db_session: AsyncSession, mock_http: HttpMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "badargs@example.com")
    aid = await _agent(client, headers)
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    await _order_tool(client, headers, aid, conn["id"])
    _script(monkeypatch, {"wrong": 1})
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)
    assert mock_http.requests == []  # never reached the network
    run = (await db_session.execute(select(ToolRun).where(ToolRun.agent_id == uuid.UUID(aid)))).scalar_one()
    assert run.status == "error" and run.output["error"] == "invalid_arguments"


async def test_a_tool_shared_with_a_second_agent_runs_for_it(
    client: AsyncClient, db_session: AsyncSession, mock_http: HttpMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "share@example.com")
    a, b = await _agent(client, headers, "A"), await _agent(client, headers, "B")
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    tool = await _order_tool(client, headers, a, conn["id"])

    # Before sharing, B has nothing and A has the tool.
    assert (await client.get(f"/v1/agents/{b}/tools", headers=headers)).json() == []
    assert [t["name"] for t in (await client.get(f"/v1/agents/{a}/tools", headers=headers)).json()] == ["get_order"]

    put = await client.put(
        f"/v1/agents/{b}/tools",
        json={"tools": [{"tool_id": tool["id"], "overrides": {"description": "Order lookup for B."}}]},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()[0]["overrides"] == {"description": "Order lookup for B."}

    _script(monkeypatch, {"order_id": "7"})
    resp = await client.post(f"/v1/agents/{b}/chat", json={"message": "order 7?", "stream": False}, headers=headers)
    assert resp.status_code == 200 and resp.json()["tool_runs"][0]["name"] == "get_order"
    runs = (await db_session.execute(select(ToolRun).where(ToolRun.agent_id == uuid.UUID(b)))).scalars().all()
    assert len(runs) == 1 and runs[0].status == "success"


async def test_unbinding_removes_the_tool_from_the_agent_but_not_the_org(client: AsyncClient) -> None:
    headers = await _org(client, "unbind@example.com")
    aid = await _agent(client, headers)
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    tool = await _order_tool(client, headers, aid, conn["id"])
    assert (await client.put(f"/v1/agents/{aid}/tools", json={"tools": []}, headers=headers)).json() == []
    assert (await client.get(f"/v1/tools/{tool['id']}", headers=headers)).status_code == 200
    assert (await client.get(f"/v1/tools?agent_id={aid}", headers=headers)).json() == []


async def test_binding_validation_and_rbac(client: AsyncClient) -> None:
    headers = await _org(client, "bindv@example.com")
    other = await _org(client, "bindv2@example.com")
    aid = await _agent(client, headers)
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    tool = await _order_tool(client, headers, None, conn["id"])
    tid = tool["id"]

    dup = await client.put(
        f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": tid}, {"tool_id": tid}]}, headers=headers
    )
    assert dup.status_code == 400 and dup.json()["error"]["code"] == "tools.duplicate_binding"
    ghost = await client.put(
        f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": str(uuid.uuid4())}]}, headers=headers
    )
    assert ghost.status_code == 404
    badov = await client.put(
        f"/v1/agents/{aid}/tools",
        json={"tools": [{"tool_id": tid, "overrides": {"temperature": 1}}]},
        headers=headers,
    )
    assert badov.status_code == 400 and badov.json()["error"]["code"] == "tools.bad_override"
    # Another org's tool id is indistinguishable from a missing one.
    foreign = await client.put(f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": tid}]}, headers=other)
    assert foreign.status_code == 404
    ok = await client.put(f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": tid}]}, headers=headers)
    assert ok.status_code == 200 and ok.json()[0]["tool_id"] == tid


async def test_more_than_twenty_enabled_tools_is_refused(client: AsyncClient) -> None:
    headers = await _org(client, "many@example.com")
    aid = await _agent(client, headers)
    ids = []
    for i in range(21):
        r = await client.post(
            "/v1/tools",
            json={"name": f"t{i}", "type": "api", "config": {"url": f"https://api.shop.example/{i}"}},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    over = await client.put(f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": i} for i in ids]}, headers=headers)
    assert over.status_code == 400 and over.json()["error"]["code"] == "tools.too_many"
    fine = await client.put(
        f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": i} for i in ids[:20]]}, headers=headers
    )
    assert fine.status_code == 200 and len(fine.json()) == 20


async def test_binding_order_and_disabled_bindings_shape_resolution(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _org(client, "order@example.com")
    aid = await _agent(client, headers)
    ids = []
    for name in ("alpha", "bravo", "charlie"):
        r = await client.post(
            "/v1/tools",
            json={"name": name, "type": "api", "config": {"url": f"https://api.shop.example/{name}"}},
            headers=headers,
        )
        ids.append(r.json()["id"])
    await client.put(
        f"/v1/agents/{aid}/tools",
        json={"tools": [{"tool_id": ids[2]}, {"tool_id": ids[0], "enabled": False}, {"tool_id": ids[1]}]},
        headers=headers,
    )
    org_id = (await client.get("/v1/orgs", headers=headers)).json()[0]["id"]
    specs, by_name = await resolve_agent_tools(db_session, uuid.UUID(org_id), uuid.UUID(aid))
    assert [s.name for s in specs] == ["charlie", "bravo"] and set(by_name) == {"charlie", "bravo"}


async def test_a_tool_created_for_an_agent_is_bound_to_it_however_it_was_inserted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Live agents resolve the same set as before: any Tool row with an owner agent gets its binding."""
    headers = await _org(client, "legacy@example.com")
    aid = await _agent(client, headers)
    org_id = uuid.UUID((await client.get("/v1/orgs", headers=headers)).json()[0]["id"])
    legacy = Tool(
        organization_id=org_id,
        agent_id=uuid.UUID(aid),
        name="legacy_http",
        type="http",
        config={"url": "https://api.shop.example/x"},
        input_schema={},
    )
    db_session.add(legacy)
    await db_session.flush()
    bound = (
        (await db_session.execute(select(AgentToolBinding).where(AgentToolBinding.tool_id == legacy.id)))
        .scalars()
        .all()
    )
    assert len(bound) == 1 and bound[0].agent_id == uuid.UUID(aid) and bound[0].enabled
    specs, _ = await resolve_agent_tools(db_session, org_id, uuid.UUID(aid))
    assert [s.name for s in specs] == ["legacy_http"]


async def test_legacy_http_tool_still_works_through_a_turn(
    client: AsyncClient, db_session: AsyncSession, mock_http: HttpMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "legacyhttp@example.com")
    aid = await _agent(client, headers)
    r = await client.post(
        "/v1/tools",
        json={
            "name": "get_order",
            "type": "http",
            "agent_id": aid,
            "config": {"method": "GET", "url": "https://api.shop.example/orders/{{order_id}}"},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    mock_http.handler = lambda req: httpx.Response(200, json={"status": "ok"})
    _script(monkeypatch, {"order_id": "9"})
    resp = await client.post(f"/v1/agents/{aid}/chat", json={"message": "x", "stream": False}, headers=headers)
    assert resp.status_code == 200 and resp.json()["tool_runs"][0]["name"] == "get_order"
    assert mock_http.requests[0].url.path == "/orders/9"


async def test_api_tool_config_is_validated_at_save(client: AsyncClient) -> None:
    headers = await _org(client, "cfg@example.com")
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    base = {"name": "t1", "type": "api", "connection_id": conn["id"]}
    cases = [
        ({"name": "bad name!", "config": {"path": "/x"}}, "tools.bad_name"),
        ({"config": {"path": "/x", "method": "TRACE"}}, "tools.bad_method"),
        ({"config": {"path": "/x", "response": {"pick": ["data.x"]}}}, "tools.bad_response_pick"),
        ({"config": {"path": "/x", "body_type": "xml"}}, "tools.bad_body_type"),
        ({"connection_id": None, "config": {}}, "tools.url_required"),
        ({"connection_id": None, "config": {"url": "https://{{h}}/x"}}, "tools.url_host_template"),
    ]
    for override, code in cases:
        r = await client.post("/v1/tools", json={**base, **override}, headers=headers)
        assert r.status_code == 400 and r.json()["error"]["code"] == code, (override, r.text)
    not_api = await client.post(
        "/v1/tools", json={"name": "calculator", "type": "builtin", "connection_id": conn["id"]}, headers=headers
    )
    assert not_api.status_code == 400


async def test_api_tool_test_endpoint_logs_a_sanitized_run(
    client: AsyncClient, db_session: AsyncSession, mock_http: HttpMock
) -> None:
    headers = await _org(client, "testep@example.com")
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    tool = await _order_tool(client, headers, None, conn["id"])
    mock_http.handler = lambda r: httpx.Response(200, json={"status": "packed", "eta": "Fri", "token": SECRET})
    r = await client.post(f"/v1/tools/{tool['id']}/test", json={"input": {"order_id": "5"}}, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "success"
    assert r.json()["output"]["data"] == {"status": "packed", "eta": "Fri"}
    run = (await db_session.execute(select(ToolRun).where(ToolRun.tool_id == uuid.UUID(tool["id"])))).scalar_one()
    assert run.provider == "api" and SECRET not in json.dumps(run.request_sanitized)
    runs = (await client.get("/v1/tools/runs", headers=headers)).json()
    assert runs[0]["provider"] == "api" and runs[0]["request_sanitized"]["method"] == "GET"


async def test_an_upstream_that_echoes_the_credential_is_scrubbed_without_a_pick(
    client: AsyncClient, db_session: AsyncSession, mock_http: HttpMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `response.pick` here, so the echo reaches the scrubber, not a selector that happens to skip it."""
    headers = await _org(client, "echo@example.com")
    aid = await _agent(client, headers)
    conn = (await client.post("/v1/connections", json=CONN, headers=headers)).json()
    r = await client.post(
        "/v1/tools",
        json={
            "name": "get_order",
            "type": "api",
            "agent_id": aid,
            "connection_id": conn["id"],
            "config": {"method": "GET", "path": "/orders/{{order_id}}"},
            "input_schema": ORDER_SCHEMA,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    mock_http.handler = lambda req: httpx.Response(200, json={"status": "ok", "echo": f"Bearer {SECRET}"})
    _script(monkeypatch, {"order_id": "1"})
    resp = await client.post(f"/v1/agents/{aid}/chat", json={"message": "x", "stream": False}, headers=headers)
    assert resp.status_code == 200, resp.text
    run = (await db_session.execute(select(ToolRun).where(ToolRun.agent_id == uuid.UUID(aid)))).scalar_one()
    assert run.output["body"]["echo"] == "Bearer ••••"
    steps = (await db_session.execute(select(AgentStep.tool_output))).scalars().all()
    assert SECRET not in json.dumps([str(s) for s in steps])
