"""Cross-tenant sweep: org B must not read, change, or delete anything that belongs to org A.

Tenant isolation is by convention (ADR-084: an org-scoped `_get_*` fetch per service function),
not by a structural guard, so the safety net is here: one table of every authenticated
endpoint that takes a resource id, fired as a *member of a different org* against org A's real
ids. Each must answer 403/404 — never 2xx (leak/mutation) and never 5xx (unhandled) — and A's
data must still be intact afterwards. A new id-taking endpoint belongs in `PROBES`.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import app.tools.service as tools_service

Headers = dict[str, str]


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_service, "is_blocked_host", lambda host: False)


async def _org(client: AsyncClient, email: str, name: str) -> tuple[Headers, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": name}, headers={"Authorization": f"Bearer {token}"})
    assert org.status_code == 201, org.text
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}, org.json()["id"]


async def _create(client: AsyncClient, headers: Headers, path: str, body: dict) -> dict:
    r = await client.post(path, json=body, headers=headers)
    assert r.status_code in (200, 201), f"{path}: {r.status_code} {r.text}"
    return dict(r.json())


async def _seed_org_a(client: AsyncClient, headers: Headers, org_id: str) -> dict[str, str]:
    ids: dict[str, str] = {"org": org_id}
    agent = await _create(client, headers, "/v1/agents", {"name": "A-Bot"})
    ids["agent"] = agent["id"]
    kb = await _create(client, headers, "/v1/knowledge", {"name": "A-KB"})
    ids["kb"] = kb["id"]
    doc = await _create(
        client, headers, f"/v1/knowledge/{kb['id']}/documents",
        {"source_type": "text", "text": "secret pricing", "filename": "a.txt"},
    )
    ids["doc"] = doc["id"]
    ids["tool"] = (await _create(client, headers, "/v1/tools", {"name": "calculator", "type": "builtin"}))["id"]
    ids["apikey"] = (await _create(client, headers, "/v1/apikeys", {"name": "k", "scopes": ["read"]}))["id"]
    ids["webhook"] = (
        await _create(client, headers, "/v1/webhooks", {"url": "https://example.com/h", "events": ["*"]})
    )["id"]
    ids["macro"] = (await _create(client, headers, "/v1/macros", {"name": "m", "actions": []}))["id"]
    ids["canned"] = (
        await _create(client, headers, "/v1/canned-responses", {"shortcut": "hi", "content": "hello"})
    )["id"]
    ids["campaign"] = (
        await _create(
            client, headers, "/v1/campaigns",
            {"agent_id": agent["id"], "name": "c", "message": "hi", "kind": "widget_trigger"},
        )
    )["id"]
    ids["workflow"] = (await _create(client, headers, f"/v1/agents/{agent['id']}/workflows", {"name": "wf"}))["id"]
    ids["channel"] = (
        await _create(client, headers, "/v1/channels", {"agent_id": agent["id"], "type": "telegram", "config": {}})
    )["id"]
    ids["article"] = (
        await _create(
            client, headers, "/v1/help-articles",
            {"agent_id": agent["id"], "title": "T", "body_markdown": "body"},
        )
    )["id"]
    ids["contact"] = (await _create(client, headers, "/v1/contacts", {"display_name": "Alice"}))["id"]
    ids["credential"] = (
        await _create(client, headers, "/v1/credentials", {"provider": "groq", "api_key": "gsk_a_secret_1234"})
    )["id"]
    ids["mcp"] = (
        await _create(
            client, headers, "/v1/mcp/servers",
            {"name": "m", "transport": "sse", "url_or_command": "https://mcp.example.com/sse"},
        )
    )["id"]
    ids["connection"] = (
        await _create(
            client, headers, "/v1/connections",
            {
                "name": "crm", "base_url": "https://api.example.com",
                "auth_type": "bearer", "auth": {"token": "tok-secret-1234"},
            },
        )
    )["id"]
    chat = await client.post(
        f"/v1/agents/{agent['id']}/chat", json={"message": "hello", "stream": False}, headers=headers
    )
    assert chat.status_code == 200, chat.text
    convs = await client.get(f"/v1/conversations?agent_id={agent['id']}", headers=headers)
    ids["conv"] = convs.json()[0]["id"]
    return ids


# (method, path template, json body). `{name}` is filled from org A's ids.
PROBES: list[tuple[str, str, dict | None]] = [
    ("GET", "/v1/agents/{agent}", None),
    ("PATCH", "/v1/agents/{agent}", {"name": "pwned"}),
    ("DELETE", "/v1/agents/{agent}", None),
    ("POST", "/v1/agents/{agent}/chat", {"message": "hi", "stream": False}),
    ("GET", "/v1/agents/{agent}/versions", None),
    ("GET", "/v1/agents/{agent}/widget-config", None),
    ("GET", "/v1/agents/{agent}/tests", None),
    ("GET", "/v1/agents/{agent}/workflows", None),
    ("GET", "/v1/knowledge/{kb}", None),
    ("PATCH", "/v1/knowledge/{kb}", {"name": "pwned"}),
    ("DELETE", "/v1/knowledge/{kb}", None),
    ("GET", "/v1/knowledge/{kb}/documents", None),
    ("POST", "/v1/knowledge/{kb}/search", {"query": "pricing"}),
    ("GET", "/v1/knowledge/documents/{doc}", None),
    ("GET", "/v1/knowledge/documents/{doc}/chunks", None),
    ("POST", "/v1/knowledge/documents/{doc}/reingest", None),
    ("DELETE", "/v1/knowledge/documents/{doc}", None),
    ("GET", "/v1/tools/{tool}", None),
    ("PATCH", "/v1/tools/{tool}", {"description": "pwned"}),
    ("POST", "/v1/tools/{tool}/test", {"args": {"expression": "1+1"}}),
    ("DELETE", "/v1/tools/{tool}", None),
    ("POST", "/v1/mcp/servers/{mcp}/test-connection", None),
    ("GET", "/v1/connections/{connection}", None),
    ("PATCH", "/v1/connections/{connection}", {"name": "pwned"}),
    ("POST", "/v1/connections/{connection}/test", None),
    ("DELETE", "/v1/connections/{connection}", None),
    ("GET", "/v1/agents/{agent}/tools", None),
    ("PUT", "/v1/agents/{agent}/tools", {"tools": []}),
    ("POST", "/v1/apikeys/{apikey}/revoke", None),
    ("DELETE", "/v1/apikeys/{apikey}", None),
    ("PATCH", "/v1/webhooks/{webhook}", {"url": "https://evil.example/h"}),
    ("GET", "/v1/webhooks/{webhook}/deliveries", None),
    ("POST", "/v1/webhooks/{webhook}/test", None),
    ("DELETE", "/v1/webhooks/{webhook}", None),
    ("PATCH", "/v1/macros/{macro}", {"name": "pwned"}),
    ("DELETE", "/v1/macros/{macro}", None),
    ("PATCH", "/v1/canned-responses/{canned}", {"content": "pwned"}),
    ("DELETE", "/v1/canned-responses/{canned}", None),
    ("PATCH", "/v1/campaigns/{campaign}", {"name": "pwned"}),
    ("DELETE", "/v1/campaigns/{campaign}", None),
    ("GET", "/v1/workflows/{workflow}", None),
    ("PATCH", "/v1/workflows/{workflow}", {"name": "pwned"}),
    ("GET", "/v1/workflows/{workflow}/runs", None),
    ("GET", "/v1/workflows/{workflow}/versions", None),
    ("DELETE", "/v1/workflows/{workflow}", None),
    ("GET", "/v1/channels/{channel}", None),
    ("PATCH", "/v1/channels/{channel}", {"name": "pwned"}),
    ("POST", "/v1/channels/{channel}/enable", None),
    ("DELETE", "/v1/channels/{channel}", None),
    ("GET", "/v1/help-articles/{article}", None),
    ("PATCH", "/v1/help-articles/{article}", {"title": "pwned"}),
    ("DELETE", "/v1/help-articles/{article}", None),
    ("GET", "/v1/contacts/{contact}", None),
    ("PATCH", "/v1/contacts/{contact}", {"display_name": "pwned"}),
    ("POST", "/v1/contacts/{contact}/notes", {"text": "pwned"}),
    ("PATCH", "/v1/credentials/{credential}", {"label": "pwned"}),
    ("POST", "/v1/credentials/{credential}/test", None),
    ("DELETE", "/v1/credentials/{credential}", None),
    ("GET", "/v1/conversations/{conv}", None),
    ("GET", "/v1/conversations/{conv}/messages", None),
    ("PATCH", "/v1/conversations/{conv}", {"title": "pwned"}),
    ("DELETE", "/v1/conversations/{conv}", None),
    ("GET", "/v1/inbox/conversations/{conv}", None),
    ("POST", "/v1/inbox/conversations/{conv}/messages", {"text": "pwned"}),
    ("POST", "/v1/inbox/conversations/{conv}/takeover", None),
    ("POST", "/v1/inbox/conversations/{conv}/close", None),
    ("POST", "/v1/inbox/conversations/{conv}/macros/{macro}", None),
    ("GET", "/v1/orgs/{org}", None),
    ("PATCH", "/v1/orgs/{org}", {"name": "pwned"}),
    ("GET", "/v1/orgs/{org}/members", None),
    ("GET", "/v1/orgs/{org}/invitations", None),
    ("DELETE", "/v1/orgs/{org}", None),
]


def _fmt(template: str, ids: dict[str, str]) -> str:
    return template.format(**ids)


@pytest.mark.parametrize(("method", "path", "body"), PROBES, ids=[f"{m} {p}" for m, p, _ in PROBES])
async def test_other_orgs_member_cannot_touch_org_a_resources(
    client: AsyncClient, method: str, path: str, body: dict | None
) -> None:
    a_headers, a_org = await _org(client, "owner-a@example.com", "Org A")
    ids = await _seed_org_a(client, a_headers, a_org)
    b_headers, _ = await _org(client, "owner-b@example.com", "Org B")

    r = await client.request(method, _fmt(path, ids), json=body, headers=b_headers)
    # A list filtered by org answers 200 with nothing in it for a foreign parent id; that is
    # not a leak. Anything else must be refused.
    empty_list = method == "GET" and r.status_code == 200 and r.json() == []
    assert empty_list or r.status_code in (403, 404), f"{method} {path} -> {r.status_code}: {r.text[:200]}"

    # Whatever B tried, A's data is intact.
    for kind, url in [
        ("agent", "/v1/agents/{agent}"), ("kb", "/v1/knowledge/{kb}"), ("tool", "/v1/tools/{tool}"),
        ("conv", "/v1/conversations/{conv}"), ("workflow", "/v1/workflows/{workflow}"),
        ("channel", "/v1/channels/{channel}"), ("contact", "/v1/contacts/{contact}"),
        ("article", "/v1/help-articles/{article}"), ("connection", "/v1/connections/{connection}"),
    ]:
        got = await client.get(_fmt(url, ids), headers=a_headers)
        assert got.status_code == 200, f"after {method} {path}, org A lost {kind}: {got.status_code}"


async def test_a_foreign_org_header_is_refused_for_a_non_member(client: AsyncClient) -> None:
    """B's valid token with A's `X-Org-Id` must not be treated as a member of A."""
    a_headers, a_org = await _org(client, "owner-a@example.com", "Org A")
    ids = await _seed_org_a(client, a_headers, a_org)
    b_headers, _ = await _org(client, "owner-b@example.com", "Org B")
    spoofed = {"Authorization": b_headers["Authorization"], "X-Org-Id": a_org}

    for path in ("/v1/agents", "/v1/knowledge", "/v1/conversations", "/v1/contacts", "/v1/tools",
                 f"/v1/agents/{ids['agent']}", f"/v1/knowledge/{ids['kb']}"):
        r = await client.get(path, headers=spoofed)
        assert r.status_code in (403, 404), f"GET {path} with a spoofed X-Org-Id -> {r.status_code}"


async def test_lists_only_contain_the_callers_own_rows(client: AsyncClient) -> None:
    a_headers, a_org = await _org(client, "owner-a@example.com", "Org A")
    await _seed_org_a(client, a_headers, a_org)
    b_headers, _ = await _org(client, "owner-b@example.com", "Org B")

    for path in ("/v1/agents", "/v1/knowledge", "/v1/tools", "/v1/apikeys", "/v1/webhooks", "/v1/macros",
                 "/v1/canned-responses", "/v1/campaigns", "/v1/channels",
                 "/v1/help-articles", "/v1/contacts", "/v1/credentials", "/v1/conversations",
                 "/v1/mcp/servers", "/v1/connections"):
        r = await client.get(path, headers=b_headers)
        assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text[:120]}"
        payload = r.json()
        rows = payload if isinstance(payload, list) else payload.get("items", payload.get("data", []))
        assert rows == [] or all(isinstance(x, dict) for x in rows), path
        assert not rows, f"org B sees {len(rows)} of org A's rows at GET {path}"


def test_probe_table_has_no_duplicates() -> None:
    keys = [(m, p) for m, p, _ in PROBES]
    assert len(keys) == len(set(keys))
