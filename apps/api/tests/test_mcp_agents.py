"""Per-agent MCP (docs/28 T2): discovery cache, allowlist bindings, isolation, secrets, plan gates."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.fake import ScriptedToolProvider
from app.llm.types import ToolCall
from app.models import MCPServer, Organization, Tool, ToolRun
from app.tools import mcp_client, mcp_service
from app.tools.service import resolve_agent_tools
from tests.mcp_fixture import FakeMCP, running

URL = "https://mcp.example.com/mcp"
SLUG = "fixture_crm"


@pytest.fixture
async def mcp(monkeypatch: pytest.MonkeyPatch, dns: dict[str, list[str]]) -> AsyncIterator[FakeMCP]:
    async with running() as fake:
        monkeypatch.setattr(mcp_client, "INNER_TRANSPORT", fake.transport)
        yield fake


async def _org(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "McpOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _agent(client: AsyncClient, headers: dict[str, str], name: str = "Bot") -> str:
    aid = (await client.post("/v1/agents", json={"name": name}, headers=headers)).json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"features": {"tools_enabled": True, "memory_enabled": True, "handoff_enabled": False}},
        headers=headers,
    )
    return str(aid)


async def _server(client: AsyncClient, headers: dict[str, str], **extra: object) -> dict:
    body = {"name": "Fixture CRM", "url_or_command": URL, **extra}
    r = await client.post("/v1/mcp/servers", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _discover(client: AsyncClient, headers: dict[str, str], server_id: str) -> dict:
    r = await client.post(f"/v1/mcp/servers/{server_id}/refresh-tools", headers=headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _bind(
    client: AsyncClient, headers: dict[str, str], agent_id: str, server_id: str, tools: list[str], **extra: object
) -> list[dict]:
    r = await client.put(
        f"/v1/agents/{agent_id}/mcp-servers",
        json={"servers": [{"server_id": server_id, "enabled_tools": tools, **extra}]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return list(r.json())


class _Recording(ScriptedToolProvider):
    """Remembers every request the model was sent, so a test can read what it was shown."""

    def __init__(self, *a: object, seen: list, **k: object) -> None:
        super().__init__(*a, **k)  # type: ignore[arg-type]
        self.seen = seen

    async def stream(self, req):  # type: ignore[no-untyped-def]
        self.seen.append(req)
        async for ev in super().stream(req):
            yield ev


def _script(monkeypatch: pytest.MonkeyPatch, name: str, args: dict, seen: list | None = None) -> None:
    from app.modules.conversations import service

    call = ToolCall(id="c1", name=name, arguments=args)
    shown = seen if seen is not None else []

    async def _provider(*_a: object, **_k: object) -> ScriptedToolProvider:
        return _Recording(call, answer="Done.", seen=shown)

    monkeypatch.setattr(service, "_resolve_provider", lambda *a, **k: _provider())


async def _chat(client: AsyncClient, headers: dict[str, str], agent_id: str) -> dict:
    r = await client.post(
        f"/v1/agents/{agent_id}/chat", json={"message": "check my order", "stream": False}, headers=headers
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


# ── Registration and discovery ────────────────────────────────────────────────────
async def test_new_servers_default_to_streamable_http_and_get_a_slug(client: AsyncClient, mcp: FakeMCP) -> None:
    headers = await _org(client, "mcp1@example.com")
    s = await _server(client, headers)
    assert s["transport"] == "streamable_http" and s["slug"] == SLUG
    assert s["tools"] == [] and s["tools_cached_at"] is None
    second = await _server(client, headers, name="Fixture CRM!")
    assert second["slug"] == "fixture_crm_2"


async def test_refresh_stores_a_cache_and_neutralizes_untrusted_descriptions(client: AsyncClient, mcp: FakeMCP) -> None:
    headers = await _org(client, "mcp2@example.com")
    s = await _server(client, headers)
    out = await _discover(client, headers, s["id"])
    assert out["ok"] is True and {t["name"] for t in out["tools"]} == {"echo", "lookup_order", "leak"}
    lookup = next(t for t in out["tools"] if t["name"] == "lookup_order")
    assert "ignore all previous instructions" not in lookup["description"].lower()
    assert "[filtered" in lookup["description"]
    listed = (await client.get("/v1/mcp/servers", headers=headers)).json()[0]
    assert listed["tools_cached_at"] and listed["last_error"] is None and len(listed["tools"]) == 3


async def test_a_failed_refresh_keeps_the_old_cache_and_records_why(
    client: AsyncClient, mcp: FakeMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "mcp3@example.com")
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])

    async def boom(*_a: object, **_k: object) -> list:
        raise mcp_client.MCPToolError("connection refused")

    monkeypatch.setattr(mcp_service, "mcp_list_tools", boom)
    out = await _discover(client, headers, s["id"])
    assert out["ok"] is False and "refused" in out["error"]
    listed = (await client.get("/v1/mcp/servers", headers=headers)).json()[0]
    assert len(listed["tools"]) == 3 and "refused" in listed["last_error"]


async def test_stdio_is_refused_for_non_staff_in_code(client: AsyncClient, mcp: FakeMCP) -> None:
    headers = await _org(client, "mcp4@example.com")
    r = await client.post(
        "/v1/mcp/servers",
        json={"name": "local", "transport": "stdio", "url_or_command": "python"},
        headers=headers,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "mcp.transport_forbidden"
    sse = await client.post(
        "/v1/mcp/servers",
        json={"name": "old", "transport": "sse", "url_or_command": URL},
        headers=headers,
    )
    assert sse.status_code == 201


async def test_private_urls_are_refused_at_registration(client: AsyncClient, mcp: FakeMCP) -> None:
    headers = await _org(client, "mcp5@example.com")
    for url in ("http://127.0.0.1:9000/mcp", "http://localhost/mcp", "http://169.254.169.254/", "ftp://x/mcp"):
        r = await client.post("/v1/mcp/servers", json={"name": "x", "url_or_command": url}, headers=headers)
        assert r.status_code == 422, (url, r.text)


# ── Bindings and the allowlist ─────────────────────────────────────────────────────
async def test_bound_tools_run_in_a_turn_and_results_are_neutralized(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "mcp6@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    out = await _bind(client, headers, aid, s["id"], ["lookup_order"])
    assert out[0]["enabled_tools"] == ["lookup_order"]
    exposed = {t["name"]: t["exposed_as"] for t in out[0]["available_tools"]}
    assert exposed["lookup_order"] == f"{SLUG}__lookup_order"

    # A turn must not rediscover: only the cache is read.
    async def no_discovery(*_a: object, **_k: object) -> list:
        raise AssertionError("a visitor turn must not list tools")

    monkeypatch.setattr(mcp_service, "mcp_list_tools", no_discovery)
    monkeypatch.setattr(mcp_client, "list_tools", no_discovery)
    shown: list = []
    _script(monkeypatch, f"{SLUG}__lookup_order", {"order_id": "A-7"}, shown)
    chat = await _chat(client, headers, aid)
    assert chat["content"] == "Done." and chat["tool_runs"][0]["name"] == f"{SLUG}__lookup_order"
    assert mcp.calls == [("lookup_order", {"order_id": "A-7"})]

    run = (await db_session.execute(select(ToolRun).where(ToolRun.agent_id == uuid.UUID(aid)))).scalar_one()
    assert run.provider == "mcp" and run.status == "success"
    # Red-team: the server's reply carried instructions; what the model re-reads is neutralized.
    second_round = " ".join(str(m.content) for m in shown[-1].messages if m.role == "tool")
    assert "Order A-7." in second_round and "[filtered" in second_round
    assert "ignore all previous instructions" not in second_round.lower()


async def test_an_unbound_agent_in_the_same_org_cannot_call_it(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "mcp7@example.com")
    a, b = await _agent(client, headers, "A"), await _agent(client, headers, "B")
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, a, s["id"], ["echo"])
    _script(monkeypatch, f"{SLUG}__echo", {"text": "hi"})
    chat = await _chat(client, headers, b)
    # B was never offered the tool, so the call goes nowhere (no run, or a refused one) and the server is untouched.
    assert all(r["status"] == "error" for r in chat["tool_runs"])
    assert mcp.calls == []


async def test_only_enabled_tools_are_offered_and_empty_means_none(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp8@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    org_id = uuid.UUID((await client.get("/v1/orgs", headers=headers)).json()[0]["id"])

    await _bind(client, headers, aid, s["id"], ["echo"])
    specs, _ = await resolve_agent_tools(db_session, org_id, uuid.UUID(aid))
    assert [sp.name for sp in specs] == [f"{SLUG}__echo"]

    await _bind(client, headers, aid, s["id"], [])  # empty = none, never "all"
    specs, _ = await resolve_agent_tools(db_session, org_id, uuid.UUID(aid))
    assert specs == []


async def test_a_per_tool_description_override_reaches_the_model(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp9@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(
        client, headers, aid, s["id"], ["echo"], tool_overrides={"echo": {"description": "Repeat what the user said."}}
    )
    org_id = uuid.UUID((await client.get("/v1/orgs", headers=headers)).json()[0]["id"])
    specs, _ = await resolve_agent_tools(db_session, org_id, uuid.UUID(aid))
    assert specs[0].description == "Repeat what the user said."


async def test_binding_validation(client: AsyncClient, mcp: FakeMCP) -> None:
    headers = await _org(client, "mcp10@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)

    undiscovered = await client.put(
        f"/v1/agents/{aid}/mcp-servers",
        json={"servers": [{"server_id": s["id"], "enabled_tools": ["echo"]}]},
        headers=headers,
    )
    assert undiscovered.status_code == 409 and undiscovered.json()["error"]["code"] == "mcp.refresh_required"
    await _discover(client, headers, s["id"])
    unknown = await client.put(
        f"/v1/agents/{aid}/mcp-servers",
        json={"servers": [{"server_id": s["id"], "enabled_tools": ["drop_tables"]}]},
        headers=headers,
    )
    assert unknown.status_code == 400 and unknown.json()["error"]["code"] == "mcp.unknown_tool"
    dup = await client.put(
        f"/v1/agents/{aid}/mcp-servers",
        json={"servers": [{"server_id": s["id"]}, {"server_id": s["id"]}]},
        headers=headers,
    )
    assert dup.status_code == 400
    stray = await client.put(
        f"/v1/agents/{aid}/mcp-servers",
        json={
            "servers": [
                {"server_id": s["id"], "enabled_tools": ["echo"], "tool_overrides": {"leak": {"description": "x"}}}
            ]
        },
        headers=headers,
    )
    assert stray.status_code == 400 and stray.json()["error"]["code"] == "mcp.bad_override"


async def test_twenty_tools_is_the_ceiling_across_mcp_and_other_tools(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, dns: dict[str, list[str]]
) -> None:
    extra = {f"extra_{i:02d}": f"result {i}" for i in range(20)}  # 20 + echo/lookup_order/leak = 23 on the server
    async with running(extra) as fake:
        monkeypatch.setattr(mcp_client, "INNER_TRANSPORT", fake.transport)
        headers = await _org(client, "mcp11@example.com")
        aid = await _agent(client, headers)
        s = await _server(client, headers)
        await _discover(client, headers, s["id"])
        names = sorted(extra)
        over = await client.put(
            f"/v1/agents/{aid}/mcp-servers",
            json={"servers": [{"server_id": s["id"], "enabled_tools": [*names, "echo"]}]},
            headers=headers,
        )
        assert over.status_code == 400 and over.json()["error"]["code"] == "tools.too_many"
        ok = await _bind(client, headers, aid, s["id"], names)
        assert len(ok[0]["enabled_tools"]) == 20
        # An ordinary tool now no longer fits next to them.
        tool = await client.post(
            "/v1/tools",
            json={"name": "t1", "type": "api", "config": {"url": "https://api.example.com/x"}},
            headers=headers,
        )
        put = await client.put(
            f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": tool.json()["id"]}]}, headers=headers
        )
        assert put.status_code == 400 and put.json()["error"]["code"] == "tools.too_many"


async def test_the_tools_endpoint_leaves_mcp_bindings_alone_and_refuses_managed_ids(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp12@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, aid, s["id"], ["echo"])
    listed = (await client.get(f"/v1/agents/{aid}/tools", headers=headers)).json()
    assert [t["name"] for t in listed] == [f"{SLUG}__echo"]
    wiped = await client.put(f"/v1/agents/{aid}/tools", json={"tools": []}, headers=headers)
    assert [t["name"] for t in wiped.json()] == [f"{SLUG}__echo"]  # still bound
    refused = await client.put(
        f"/v1/agents/{aid}/tools", json={"tools": [{"tool_id": listed[0]["tool_id"]}]}, headers=headers
    )
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "tools.managed_binding"


async def test_unbinding_a_server_removes_its_tools_from_the_agent(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp13@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, aid, s["id"], ["echo", "leak"])
    assert (await client.put(f"/v1/agents/{aid}/mcp-servers", json={"servers": []}, headers=headers)).json() == []
    assert (await client.get(f"/v1/agents/{aid}/tools", headers=headers)).json() == []


# ── Isolation ───────────────────────────────────────────────────────────────────
async def test_another_org_cannot_see_bind_refresh_or_delete(client: AsyncClient, mcp: FakeMCP) -> None:
    a = await _org(client, "mcp14a@example.com")
    b = await _org(client, "mcp14b@example.com")
    s = await _server(client, a)
    await _discover(client, a, s["id"])
    b_agent = await _agent(client, b)
    assert (await client.get("/v1/mcp/servers", headers=b)).json() == []
    bound = await client.put(f"/v1/agents/{b_agent}/mcp-servers", json={"servers": [{"server_id": s["id"]}]}, headers=b)
    assert bound.status_code == 404
    for method, path in [
        ("POST", f"/v1/mcp/servers/{s['id']}/refresh-tools"),
        ("PATCH", f"/v1/mcp/servers/{s['id']}"),
        ("DELETE", f"/v1/mcp/servers/{s['id']}"),
        ("POST", f"/v1/mcp/servers/{s['id']}/test-connection"),
    ]:
        r = await client.request(method, path, json={"name": "x"} if method == "PATCH" else None, headers=b)
        assert r.status_code == 404, (method, path, r.status_code)
    a_agent = await _agent(client, a)
    foreign_agent = await client.put(f"/v1/agents/{a_agent}/mcp-servers", json={"servers": []}, headers=b)
    assert foreign_agent.status_code == 404


async def test_viewer_cannot_manage_mcp(client: AsyncClient, mcp: FakeMCP) -> None:
    from tests.test_connections import _member

    owner = await _org(client, "mcp15@example.com")
    org_id = owner["X-Org-Id"]
    s = await _server(client, owner)
    aid = await _agent(client, owner)
    viewer = await _member(client, owner, org_id, "viewer", "mcpviewer@example.com")
    assert (
        await client.post("/v1/mcp/servers", json={"name": "z", "url_or_command": URL}, headers=viewer)
    ).status_code == 403
    assert (await client.post(f"/v1/mcp/servers/{s['id']}/refresh-tools", headers=viewer)).status_code == 403
    assert (await client.put(f"/v1/agents/{aid}/mcp-servers", json={"servers": []}, headers=viewer)).status_code == 403
    assert (await client.get(f"/v1/agents/{aid}/mcp-servers", headers=viewer)).status_code == 200


# ── Secrets ─────────────────────────────────────────────────────────────────────
async def test_server_headers_are_sent_never_returned_and_scrubbed_from_results(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "Bearer sekret-token-99999"
    headers = await _org(client, "mcp16@example.com")
    aid = await _agent(client, headers)
    created = await client.post(
        "/v1/mcp/servers",
        json={"name": "Fixture CRM", "url_or_command": URL, "headers": {"Authorization": secret}},
        headers=headers,
    )
    assert created.status_code == 201 and "sekret" not in created.text
    s = created.json()
    await _discover(client, headers, s["id"])
    assert secret in mcp.authorizations
    await _bind(client, headers, aid, s["id"], ["leak"])
    assert "sekret" not in (await client.get("/v1/mcp/servers", headers=headers)).text
    # The server echoes a credential it was handed back in its reply; the stored run must not keep it.
    _script(monkeypatch, f"{SLUG}__leak", {"token": secret})
    await _chat(client, headers, aid)
    run = (await db_session.execute(select(ToolRun).where(ToolRun.agent_id == uuid.UUID(aid)))).scalar_one()
    assert "sekret-token-99999" not in json.dumps(run.output)
    assert "••••" in json.dumps(run.output, ensure_ascii=False)


async def test_a_connection_supplies_the_credential_but_only_to_its_allowed_hosts(
    client: AsyncClient, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp17@example.com")
    conn = await client.post(
        "/v1/connections",
        json={
            "name": "mcp-auth",
            "base_url": "https://mcp.example.com",
            "auth_type": "bearer",
            "auth": {"token": "conn-token-abcdef123"},
        },
        headers=headers,
    )
    cid = conn.json()["id"]
    s = await _server(client, headers, connection_id=cid)
    await _discover(client, headers, s["id"])
    assert "Bearer conn-token-abcdef123" in mcp.authorizations
    # Pointing the same credential at another host would hand it to a stranger.
    other = await client.post(
        "/v1/mcp/servers",
        json={"name": "Elsewhere", "url_or_command": "https://evil.example/mcp", "connection_id": cid},
        headers=headers,
    )
    assert other.status_code == 400 and other.json()["error"]["code"] == "mcp.connection_host_mismatch"
    moved = await client.patch(
        f"/v1/mcp/servers/{s['id']}", json={"url_or_command": "https://evil.example/mcp"}, headers=headers
    )
    assert moved.status_code == 400
    # A connection that is in use cannot be deleted... until the server stops using it.
    in_use = await client.delete(f"/v1/connections/{cid}", headers=headers)
    assert in_use.status_code == 409 and in_use.json()["error"]["details"]["mcp_servers"] == ["Fixture CRM"]


# ── Lifecycle and plan ──────────────────────────────────────────────────────────
async def test_disabling_a_server_stops_its_tools_reaching_the_model(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp18@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, aid, s["id"], ["echo"])
    org_id = uuid.UUID((await client.get("/v1/orgs", headers=headers)).json()[0]["id"])
    assert len((await resolve_agent_tools(db_session, org_id, uuid.UUID(aid)))[0]) == 1
    await client.patch(f"/v1/mcp/servers/{s['id']}", json={"enabled": False}, headers=headers)
    assert (await resolve_agent_tools(db_session, org_id, uuid.UUID(aid)))[0] == []


async def test_changing_the_url_forgets_the_cache_and_switches_tools_off(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp19@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, aid, s["id"], ["echo"])
    moved = await client.patch(
        f"/v1/mcp/servers/{s['id']}", json={"url_or_command": "https://other.example.com/mcp"}, headers=headers
    )
    assert moved.json()["tools"] == [] and moved.json()["tools_cached_at"] is None
    org_id = uuid.UUID((await client.get("/v1/orgs", headers=headers)).json()[0]["id"])
    assert (await resolve_agent_tools(db_session, org_id, uuid.UUID(aid)))[0] == []


async def test_a_tool_the_server_stops_offering_is_switched_off_on_refresh(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "mcp20@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, aid, s["id"], ["echo", "leak"])
    real = mcp_client.list_tools

    async def only_echo(config: object, **kw: object) -> list:
        return [t for t in await real(config, **kw) if t["name"] == "echo"]  # type: ignore[arg-type]

    monkeypatch.setattr(mcp_service, "mcp_list_tools", only_echo)
    await _discover(client, headers, s["id"])
    org_id = uuid.UUID((await client.get("/v1/orgs", headers=headers)).json()[0]["id"])
    specs, _ = await resolve_agent_tools(db_session, org_id, uuid.UUID(aid))
    assert [sp.name for sp in specs] == [f"{SLUG}__echo"]


async def test_deleting_a_server_removes_its_tools(client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP) -> None:
    headers = await _org(client, "mcp21@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, aid, s["id"], ["echo"])
    assert (await client.delete(f"/v1/mcp/servers/{s['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/v1/agents/{aid}/tools", headers=headers)).json() == []
    left = (await db_session.execute(select(Tool).where(Tool.type == "mcp"))).scalars().all()
    assert not [t for t in left if t.config.get("server_id") == s["id"]]


async def test_the_stale_sweep_finds_used_servers_with_an_old_or_missing_cache(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    headers = await _org(client, "mcp22@example.com")
    aid = await _agent(client, headers)
    used, unused = await _server(client, headers), await _server(client, headers, name="Unused")
    await _discover(client, headers, used["id"])
    await _bind(client, headers, aid, used["id"], ["echo"])
    row = await db_session.get(MCPServer, uuid.UUID(used["id"]))
    assert row is not None
    assert uuid.UUID(used["id"]) not in await mcp_service.stale_server_ids(db_session)  # fresh
    row.tools_cached_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=30)
    await db_session.flush()
    stale = await mcp_service.stale_server_ids(db_session)
    assert uuid.UUID(used["id"]) in stale and uuid.UUID(unused["id"]) not in stale  # unused servers are left alone
    assert (await mcp_service.refresh_server(db_session, row)).ok is True
    assert uuid.UUID(used["id"]) not in await mcp_service.stale_server_ids(db_session)


async def test_a_downgraded_plan_stops_mcp_tools_in_the_runtime(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org(client, "mcp23@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    await _discover(client, headers, s["id"])
    await _bind(client, headers, aid, s["id"], ["echo"])
    org = await db_session.get(Organization, uuid.UUID(headers["X-Org-Id"]))
    assert org is not None
    org.plan, org.plan_expires_at = "starter", dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=10)
    await db_session.flush()
    assert (await resolve_agent_tools(db_session, org.id, uuid.UUID(aid)))[0] == []
    refused = await client.put(f"/v1/agents/{aid}/mcp-servers", json={"servers": []}, headers=headers)
    assert refused.status_code == 402


async def test_old_hand_made_mcp_tools_still_follow_the_agentic_flag(
    client: AsyncClient, db_session: AsyncSession, mcp: FakeMCP
) -> None:
    """Rows made before bindings existed are not 'managed', so they keep their old gate."""
    headers = await _org(client, "mcp24@example.com")
    aid = await _agent(client, headers)
    s = await _server(client, headers)
    tool = await client.post(
        "/v1/tools",
        json={
            "name": "manual_echo",
            "type": "mcp",
            "agent_id": aid,
            "config": {"server_id": s["id"], "tool_name": "echo"},
        },
        headers=headers,
    )
    assert tool.status_code == 201, tool.text
    org_id = uuid.UUID(headers["X-Org-Id"])
    assert (await resolve_agent_tools(db_session, org_id, uuid.UUID(aid)))[0] == []
    assert [sp.name for sp in (await resolve_agent_tools(db_session, org_id, uuid.UUID(aid), include_mcp=True))[0]] == [
        "manual_echo"
    ]
    blocked = await client.delete(f"/v1/mcp/servers/{s['id']}", headers=headers)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "mcp.in_use"


class _Stub:
    """Stands in for the real network transport and records what it was asked to send."""

    def __init__(self) -> None:
        self.requests: list = []

    async def handle_async_request(self, request):  # type: ignore[no-untyped-def]
        import httpx2

        self.requests.append(request)
        return httpx2.Response(200)

    async def aclose(self) -> None:
        return None


async def test_the_mcp_transport_connects_to_the_validated_ip_with_the_original_host(dns: dict[str, list[str]]) -> None:
    """Rebinding defence for the SDK's own connections (docs/28 section 7 step 9)."""
    import httpx2

    stub = _Stub()
    transport = mcp_client.PinnedTransport(None, inner=stub)  # type: ignore[arg-type]
    dns["mcp.example.com"] = ["93.184.216.34"]
    await transport.handle_async_request(httpx2.Request("POST", "https://mcp.example.com/mcp"))
    sent = stub.requests[0]
    assert sent.url.host == "93.184.216.34"
    assert sent.headers["host"] == "mcp.example.com"
    assert sent.extensions["sni_hostname"] == "mcp.example.com"

    dns["mcp.example.com"] = ["10.0.0.4"]  # the name now points inside: the next request is refused
    with pytest.raises(httpx2.ConnectError, match="private"):
        await transport.handle_async_request(httpx2.Request("POST", "https://mcp.example.com/mcp"))
    assert len(stub.requests) == 1


async def test_the_mcp_transport_enforces_allowed_hosts_on_every_request(dns: dict[str, list[str]]) -> None:
    import httpx2

    transport = mcp_client.PinnedTransport(["mcp.example.com"], inner=_Stub())  # type: ignore[arg-type]
    with pytest.raises(httpx2.ConnectError, match="allowed list"):
        await transport.handle_async_request(httpx2.Request("GET", "https://elsewhere.example/x"))
