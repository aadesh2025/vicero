"""docs/17 Phase 1 §4/§10: MCP server registry (`POST/GET /v1/mcp/servers`,
`POST /v1/mcp/servers/{id}/test-connection`) over the real HTTP client (real DB, tx-rollback).

Closes a coverage gap found during the Phase 1-4 hardening pass: these three endpoints (and the
service functions behind them) had zero test coverage of any kind — not even a pure-logic unit
test — despite shipping in Phase 1 (2026-08-19). `test-connection` mocks
`app.tools.service.mcp_list_tools` rather than hitting a real MCP server (stdio spawn or SSE
socket), the same reasoning `test_admin.py` already applies to the n8n client.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.tools.mcp_client as mcp_client
import app.tools.mcp_service as mcp_service
import app.tools.mcp_tool as mcp_tool_module
from app.models import MCPServer, User
from app.tools.mcp_client import MCPToolError


@pytest.fixture(autouse=True)
def _public_dns(dns: dict[str, list[str]]) -> None:
    """Made-up server names resolve to a public address; a literal private IP is refused at registration."""


async def _headers(client: AsyncClient, email: str = "a@example.com") -> tuple[dict[str, str], dict]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "Acme"}, headers={"Authorization": f"Bearer {token}"})
    org_json = org.json()
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org_json["id"]}, org_json


async def _invite_and_join(
    client: AsyncClient, owner_headers: dict[str, str], org: dict, email: str, role: str
) -> dict[str, str]:
    import re

    from app.core.email import get_email_backend

    invite = await client.post(
        f"/v1/orgs/{org['id']}/invitations", json={"email": email, "role": role}, headers=owner_headers
    )
    assert invite.status_code == 201, invite.text
    token = re.search(r"Token:\s*(\S+)", get_email_backend().outbox[-1].body).group(1)  # type: ignore[union-attr]
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    member_token = signup.json()["access_token"]
    await client.post(f"/v1/orgs/invitations/{token}/accept", headers={"Authorization": f"Bearer {member_token}"})
    return {"Authorization": f"Bearer {member_token}", "X-Org-Id": org["id"]}


async def _create_server(client: AsyncClient, headers: dict[str, str], **overrides: object) -> dict:
    body: dict[str, object] = {
        "name": "Calendar",
        "transport": "sse",
        "url_or_command": "https://mcp.example.com/sse",
        "headers": {"Authorization": "Bearer secret-token"},
        "enabled": True,
    }
    body.update(overrides)
    r = await client.post("/v1/mcp/servers", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ── CRUD ──────────────────────────────────────────────────────────────────────
async def test_create_and_list_mcp_servers(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    server = await _create_server(client, headers)
    assert server["name"] == "Calendar"
    assert server["transport"] == "sse"
    assert server["enabled"] is True
    # auth (headers) is encrypted server-side and never echoed back in the response shape.
    assert "headers" not in server
    assert "auth_config_enc" not in server

    listed = await client.get("/v1/mcp/servers", headers=headers)
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [server["id"]]


async def test_editor_cannot_register_a_stdio_server(client: AsyncClient) -> None:
    """A stdio server is a command run on the API host: registering one is remote code execution
    for whoever holds TOOLS_MANAGE, which includes the client `editor` role."""
    headers, _ = await _headers(client)
    r = await client.post(
        "/v1/mcp/servers",
        json={"name": "Local FS", "transport": "stdio", "url_or_command": "npx", "args": ["-y", "x"]},
        headers=headers,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "mcp.transport_forbidden"


async def test_staff_can_register_a_stdio_server_with_args_and_env(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, _ = await _headers(client, "staff@example.com")
    user = (await db_session.execute(select(User).where(User.email == "staff@example.com"))).scalar_one()
    user.is_staff = True
    await db_session.flush()
    server = await _create_server(
        client, headers, name="Local FS", transport="stdio", url_or_command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], env={"NODE_ENV": "production"},
    )
    assert server["transport"] == "stdio"
    assert server["url_or_command"] == "npx"


async def test_sse_server_at_a_private_address_is_rejected(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    r = await client.post(
        "/v1/mcp/servers",
        json={"name": "Inner", "transport": "sse", "url_or_command": "http://10.0.0.5/sse"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "tools.mcp_url_blocked"


async def test_sse_server_needs_an_http_url(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    r = await client.post(
        "/v1/mcp/servers",
        json={"name": "Odd", "transport": "sse", "url_or_command": "file:///etc/passwd"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "tools.mcp_url_invalid"


async def test_a_stdio_row_from_a_non_staff_creator_is_never_run(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Rows registered before the rule existed must stop running at connect time."""
    _, org = await _headers(client, "legacy@example.com")
    creator = (await db_session.execute(select(User).where(User.email == "legacy@example.com"))).scalar_one()
    row = MCPServer(
        organization_id=uuid.UUID(org["id"]), name="Legacy", transport="stdio",
        url_or_command="calc.exe", enabled=True, created_by=creator.id,
    )
    db_session.add(row)
    await db_session.flush()

    config = await mcp_tool_module.resolve_server_config(db_session, row)
    assert config.stdio_allowed is False
    with pytest.raises(MCPToolError, match="platform staff"):
        await mcp_client.list_tools(config)

    creator.is_staff = True
    await db_session.flush()
    assert (await mcp_tool_module.resolve_server_config(db_session, row)).stdio_allowed is True


async def test_connect_refuses_an_sse_url_that_resolves_to_a_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import egress

    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.9"]

    monkeypatch.setattr(egress, "resolve_host", private)
    config = mcp_client.MCPServerConfig(transport="sse", url_or_command="https://rebind.example/sse")
    with pytest.raises(MCPToolError, match="private/loopback"):
        await mcp_client.list_tools(config)


async def test_invalid_transport_rejected(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    r = await client.post(
        "/v1/mcp/servers",
        json={"name": "Bad", "transport": "http", "url_or_command": "https://x"},
        headers=headers,
    )
    assert r.status_code == 422


async def test_servers_are_org_scoped(client: AsyncClient) -> None:
    headers_a, _ = await _headers(client, "orga@example.com")
    await _create_server(client, headers_a)

    headers_b, _ = await _headers(client, "orgb@example.com")
    listed_b = await client.get("/v1/mcp/servers", headers=headers_b)
    assert listed_b.status_code == 200
    assert listed_b.json() == []


# ── RBAC ──────────────────────────────────────────────────────────────────────
async def test_viewer_can_read_but_not_register_or_test(client: AsyncClient) -> None:
    owner_headers, org = await _headers(client, "owner@example.com")
    server = await _create_server(client, owner_headers)
    viewer_headers = await _invite_and_join(client, owner_headers, org, "viewer@example.com", "viewer")

    listed = await client.get("/v1/mcp/servers", headers=viewer_headers)
    assert listed.status_code == 200

    denied_create = await client.post(
        "/v1/mcp/servers",
        json={"name": "x", "transport": "sse", "url_or_command": "https://x"},
        headers=viewer_headers,
    )
    assert denied_create.status_code == 403

    denied_test = await client.post(
        f"/v1/mcp/servers/{server['id']}/test-connection", headers=viewer_headers
    )
    assert denied_test.status_code == 403


async def test_editor_can_register_and_test(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`editor` holds TOOLS_MANAGE per the RBAC matrix — proven here, not assumed."""
    owner_headers, org = await _headers(client, "owner2@example.com")
    editor_headers = await _invite_and_join(client, owner_headers, org, "editor2@example.com", "editor")

    async def fake_list_tools(_config: object) -> list[dict[str, object]]:
        return [{"name": "get_events", "description": "List calendar events", "input_schema": {}}]

    monkeypatch.setattr(mcp_service, "mcp_list_tools", fake_list_tools)

    server = await _create_server(client, editor_headers)
    tested = await client.post(f"/v1/mcp/servers/{server['id']}/test-connection", headers=editor_headers)
    assert tested.status_code == 200, tested.text
    assert tested.json() == {
        "ok": True,
        "tools": [{"name": "get_events", "description": "List calendar events", "input_schema": {}}],
        "error": None,
    }


async def test_server_from_other_org_is_not_found(client: AsyncClient) -> None:
    headers_a, _ = await _headers(client, "orga2@example.com")
    server = await _create_server(client, headers_a)

    headers_b, _ = await _headers(client, "orgb2@example.com")
    r = await client.post(f"/v1/mcp/servers/{server['id']}/test-connection", headers=headers_b)
    assert r.status_code == 404


# ── test-connection: never a 5xx, even on a real connection/protocol failure ───
async def test_connection_failure_returns_ok_false_not_a_5xx(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_list_tools(_config: object) -> list[dict[str, object]]:
        raise MCPToolError("connection refused")

    monkeypatch.setattr(mcp_service, "mcp_list_tools", failing_list_tools)

    headers, _ = await _headers(client)
    server = await _create_server(client, headers)
    tested = await client.post(f"/v1/mcp/servers/{server['id']}/test-connection", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json() == {"ok": False, "tools": [], "error": "connection refused"}


async def test_connection_for_unknown_server_returns_404(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    r = await client.post(f"/v1/mcp/servers/{uuid.uuid4()}/test-connection", headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "tools.mcp_server_not_found"


# ── Binding a Tool row to an MCP server (POST /v1/tools, type="mcp") ───────────
# Zero coverage before this file, for the registry endpoints AND this validation.


async def test_create_mcp_tool_requires_server_id_and_tool_name(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    r = await client.post(
        "/v1/tools", json={"name": "calendar_lookup", "type": "mcp", "config": {}}, headers=headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "tools.mcp_config_required"


async def test_create_mcp_tool_with_unknown_server_returns_404(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    r = await client.post(
        "/v1/tools",
        json={
            "name": "calendar_lookup", "type": "mcp",
            "config": {"server_id": str(uuid.uuid4()), "tool_name": "get_events"},
        },
        headers=headers,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "tools.mcp_server_not_found"


async def test_create_mcp_tool_rejects_another_orgs_server(client: AsyncClient) -> None:
    headers_a, _ = await _headers(client, "orga3@example.com")
    server = await _create_server(client, headers_a)

    headers_b, _ = await _headers(client, "orgb3@example.com")
    r = await client.post(
        "/v1/tools",
        json={
            "name": "calendar_lookup", "type": "mcp",
            "config": {"server_id": server["id"], "tool_name": "get_events"},
        },
        headers=headers_b,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "tools.mcp_server_not_found"


async def test_create_mcp_tool_succeeds_with_a_real_server(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    server = await _create_server(client, headers)
    r = await client.post(
        "/v1/tools",
        json={
            "name": "calendar_lookup", "type": "mcp",
            "config": {"server_id": server["id"], "tool_name": "get_events"},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "mcp"


# ── execute_mcp_tool dispatch (DB-session integration, not just pure logic) ────
# The DB lookup — tenant check, disabled check — was previously exercised by nothing at all.


async def test_execute_mcp_tool_dispatches_with_the_servers_real_config(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, org = await _headers(client)
    server = await _create_server(client, headers, name="Calendar", transport="sse")

    seen: dict[str, object] = {}

    async def fake_call_tool(config: object, tool_name: str, args: dict[str, object]) -> dict[str, object]:
        seen["config"] = config
        seen["tool_name"] = tool_name
        seen["args"] = args
        return {"events": ["standup at 9am"]}

    monkeypatch.setattr(mcp_tool_module, "call_tool", fake_call_tool)

    result = await mcp_tool_module.execute_mcp_tool(
        db_session, uuid.UUID(org["id"]), {"server_id": server["id"], "tool_name": "get_events"},
        {"date": "today"},
    )
    assert result.status == "success"
    assert result.output == {"events": ["standup at 9am"]}
    assert seen["tool_name"] == "get_events"
    assert seen["args"] == {"date": "today"}
    # The config passed through is the server's OWN (url/transport), not a caller-supplied one —
    # proves the DB lookup actually ran, not just a name-based dispatch.
    assert seen["config"].url_or_command == "https://mcp.example.com/sse"  # type: ignore[attr-defined]


async def test_execute_mcp_tool_rejects_a_server_from_another_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The security rule ADR-055/047/048 already established for guard models: a server is
    never resolved against the wrong tenant, even if a config carried another org's server_id."""
    headers_a, _ = await _headers(client, "orga4@example.com")
    server = await _create_server(client, headers_a)

    _headers_b, org_b = await _headers(client, "orgb4@example.com")

    result = await mcp_tool_module.execute_mcp_tool(
        db_session, uuid.UUID(org_b["id"]), {"server_id": server["id"], "tool_name": "get_events"}, {},
    )
    assert result.status == "error"
    assert "not found" in (result.error or "")


async def test_execute_mcp_tool_rejects_a_disabled_server(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org = await _headers(client)
    server = await _create_server(client, headers, enabled=False)

    result = await mcp_tool_module.execute_mcp_tool(
        db_session, uuid.UUID(org["id"]), {"server_id": server["id"], "tool_name": "get_events"}, {},
    )
    assert result.status == "error"
    assert "disabled" in (result.error or "")


async def test_execute_mcp_tool_surfaces_a_protocol_error_as_a_result_not_an_exception(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, org = await _headers(client)
    server = await _create_server(client, headers)

    async def failing_call_tool(_config: object, _tool_name: str, _args: dict[str, object]) -> dict[str, object]:
        raise MCPToolError("server closed the connection")

    monkeypatch.setattr(mcp_tool_module, "call_tool", failing_call_tool)

    result = await mcp_tool_module.execute_mcp_tool(
        db_session, uuid.UUID(org["id"]), {"server_id": server["id"], "tool_name": "get_events"}, {},
    )
    assert result.status == "error"
    assert result.error == "server closed the connection"


def test_structured_content_is_not_sent_twice() -> None:
    from mcp import types as mcp_types

    from app.tools.mcp_client import _flatten_content

    both = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text='{"a": 1}')], structuredContent={"a": 1}
    )
    assert _flatten_content(both) == {"text": '{"a": 1}'}
    only_structured = mcp_types.CallToolResult(content=[], structuredContent={"a": 1})
    assert _flatten_content(only_structured) == {"text": "", "structured": {"a": 1}}


def test_a_grouped_connection_error_reads_as_its_cause() -> None:
    import httpx

    from app.tools.mcp_client import _reason

    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [httpx.ReadTimeout("slow")])
    assert _reason(group) == "ReadTimeout: slow"
    assert "TaskGroup" not in _reason(group)
