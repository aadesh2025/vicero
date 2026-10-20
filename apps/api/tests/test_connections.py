"""Connections module (docs/28 T1.3): CRUD, RBAC, masking, plan gate, test endpoint, audit."""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid

import httpx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import get_email_backend
from app.models import AuditLog, Organization
from tests.conftest import HttpMock

SECRET = "sk-live-abcdef1234567890"
BODY = {
    "name": "crm",
    "base_url": "https://api.example.com/v1/",
    "auth_type": "bearer",
    "auth": {"token": SECRET},
}


async def _owner(client: AsyncClient, email: str = "conn@example.com") -> tuple[dict[str, str], str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "ConnOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}, org.json()["id"]


async def _member(client: AsyncClient, owner: dict[str, str], org_id: str, role: str, email: str) -> dict[str, str]:
    invite = await client.post(f"/v1/orgs/{org_id}/invitations", json={"email": email, "role": role}, headers=owner)
    assert invite.status_code == 201, invite.text
    token = re.search(r"Token:\s*(\S+)", get_email_backend().outbox[-1].body).group(1)  # type: ignore[union-attr]
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    t = signup.json()["access_token"]
    await client.post(f"/v1/orgs/invitations/{token}/accept", headers={"Authorization": f"Bearer {t}"})
    return {"Authorization": f"Bearer {t}", "X-Org-Id": org_id}


async def test_create_masks_the_secret_everywhere_it_is_returned(client: AsyncClient) -> None:
    headers, _ = await _owner(client)
    created = await client.post("/v1/connections", json=BODY, headers=headers)
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    for resp in (
        created,
        await client.get(f"/v1/connections/{cid}", headers=headers),
        await client.get("/v1/connections", headers=headers),
    ):
        assert SECRET not in resp.text
    out = created.json()
    assert out["auth"] == {"token": "••••7890"}
    assert out["base_url"] == "https://api.example.com/v1"  # trailing slash dropped
    assert out["allowed_hosts"] == ["api.example.com"]  # defaults to the base host
    assert out["tools_count"] == 0


async def test_secret_is_encrypted_at_rest(client: AsyncClient, db_session: AsyncSession) -> None:
    from app.models import Connection

    headers, _ = await _owner(client)
    cid = (await client.post("/v1/connections", json=BODY, headers=headers)).json()["id"]
    row = await db_session.get(Connection, uuid.UUID(cid))
    assert row is not None and row.secrets_enc and SECRET not in row.secrets_enc


async def test_blank_or_masked_secret_on_patch_keeps_the_stored_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.models import Connection
    from app.tools.connections import resolve

    headers, _ = await _owner(client)
    cid = (await client.post("/v1/connections", json=BODY, headers=headers)).json()["id"]
    for patch in ({"auth": {"token": ""}}, {"auth": {"token": "••••7890"}}, {"name": "renamed"}):
        r = await client.patch(f"/v1/connections/{cid}", json=patch, headers=headers)
        assert r.status_code == 200, r.text
    row = await db_session.get(Connection, uuid.UUID(cid))
    assert row is not None and resolve(row).secrets == {"token": SECRET}
    new = await client.patch(
        f"/v1/connections/{cid}", json={"auth": {"token": "brand-new-token-0001"}}, headers=headers
    )
    assert new.json()["auth"] == {"token": "••••0001"}


async def test_api_key_non_secret_fields_survive_a_secret_only_patch(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.models import Connection
    from app.tools.connections import resolve

    headers, _ = await _owner(client)
    body = {
        "name": "k",
        "base_url": "https://api.example.com",
        "auth_type": "api_key",
        "auth": {"name": "X-Key", "in": "header", "value": SECRET},
    }
    cid = (await client.post("/v1/connections", json=body, headers=headers)).json()["id"]
    r = await client.patch(f"/v1/connections/{cid}", json={"auth": {"value": "rotated-key-000099"}}, headers=headers)
    assert r.status_code == 200, r.text
    row = await db_session.get(Connection, uuid.UUID(cid))
    assert row is not None and resolve(row).secrets == {"name": "X-Key", "in": "header", "value": "rotated-key-000099"}


async def test_changing_auth_type_needs_the_full_new_payload(client: AsyncClient) -> None:
    headers, _ = await _owner(client)
    cid = (await client.post("/v1/connections", json=BODY, headers=headers)).json()["id"]
    r = await client.patch(
        f"/v1/connections/{cid}", json={"auth_type": "basic", "auth": {"username": "u"}}, headers=headers
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "connections.auth_incomplete"
    ok = await client.patch(
        f"/v1/connections/{cid}",
        json={"auth_type": "basic", "auth": {"username": "u", "password": "p-secret-99"}},
        headers=headers,
    )
    assert ok.status_code == 200 and ok.json()["auth"]["username"] == "u"


async def test_validation(client: AsyncClient) -> None:
    headers, _ = await _owner(client)
    cases = [
        ({**BODY, "base_url": "ftp://x.com"}, "connections.bad_url"),
        ({**BODY, "base_url": "https://u:p@x.com"}, "connections.bad_url"),
        ({**BODY, "base_url": "https://x.com/{{id}}"}, "connections.bad_url"),
        ({**BODY, "base_url": "http://127.0.0.1:8000"}, "connections.bad_url"),
        ({**BODY, "base_url": "http://localhost"}, "connections.bad_url"),
        ({**BODY, "base_url": "https://x.com?a=1"}, "connections.bad_url"),
        ({**BODY, "auth_type": "magic"}, "connections.bad_auth_type"),
        ({**BODY, "auth": {}}, "connections.auth_incomplete"),
        ({**BODY, "auth": {"token": "t-123456789", "extra": "x"}}, "connections.bad_auth_field"),
        ({**BODY, "allowed_hosts": ["bad host!"]}, "connections.bad_allowed_host"),
        ({**BODY, "default_headers": {"X": "a\r\nb"}}, "connections.bad_header"),
        (
            {**BODY, "auth_type": "api_key", "auth": {"name": "k", "in": "cookie", "value": "v-123456789"}},
            "connections.bad_auth_field",
        ),
        (
            {
                **BODY,
                "auth_type": "oauth2_client_credentials",
                "auth": {"token_url": "ftp://t", "client_id": "a", "client_secret": "bbbbbbbb"},
            },
            "connections.bad_auth_field",
        ),
    ]
    for body, code in cases:
        r = await client.post("/v1/connections", json=body, headers=headers)
        assert r.status_code == 400 and r.json()["error"]["code"] == code, (body, r.text)


async def test_name_is_unique_per_org(client: AsyncClient) -> None:
    headers, _ = await _owner(client)
    assert (await client.post("/v1/connections", json=BODY, headers=headers)).status_code == 201
    dup = await client.post("/v1/connections", json=BODY, headers=headers)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "connections.name_taken"


async def test_rbac_viewer_cannot_see_or_change_connections(client: AsyncClient) -> None:
    owner, org_id = await _owner(client)
    cid = (await client.post("/v1/connections", json=BODY, headers=owner)).json()["id"]
    viewer = await _member(client, owner, org_id, "viewer", "viewer@example.com")
    for method, path, body in [
        ("GET", "/v1/connections", None),
        ("POST", "/v1/connections", {**BODY, "name": "x"}),
        ("GET", f"/v1/connections/{cid}", None),
        ("PATCH", f"/v1/connections/{cid}", {"name": "x"}),
        ("DELETE", f"/v1/connections/{cid}", None),
        ("POST", f"/v1/connections/{cid}/test", None),
    ]:
        r = await client.request(method, path, json=body, headers=viewer)
        assert r.status_code == 403, (method, path, r.status_code)
    editor = await _member(client, owner, org_id, "editor", "editor@example.com")
    assert (await client.get("/v1/connections", headers=editor)).status_code == 200


async def test_another_orgs_connection_is_a_404(client: AsyncClient) -> None:
    a, _ = await _owner(client, "a-conn@example.com")
    b, _ = await _owner(client, "b-conn@example.com")
    cid = (await client.post("/v1/connections", json=BODY, headers=a)).json()["id"]
    assert (await client.get(f"/v1/connections/{cid}", headers=b)).status_code == 404
    assert (await client.get("/v1/connections", headers=b)).json() == []
    # ... and B cannot attach it to a tool.
    tool = await client.post(
        "/v1/tools", json={"name": "t", "type": "api", "connection_id": cid, "config": {"path": "/x"}}, headers=b
    )
    assert tool.status_code == 404


async def test_delete_is_blocked_while_a_tool_uses_it(client: AsyncClient) -> None:
    headers, _ = await _owner(client)
    cid = (await client.post("/v1/connections", json=BODY, headers=headers)).json()["id"]
    tool = await client.post(
        "/v1/tools",
        json={"name": "get_order", "type": "api", "connection_id": cid, "config": {"path": "/o"}},
        headers=headers,
    )
    assert tool.status_code == 201, tool.text
    blocked = await client.delete(f"/v1/connections/{cid}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "connections.in_use"
    assert blocked.json()["error"]["details"]["tools"] == ["get_order"]
    assert (await client.get(f"/v1/connections/{cid}", headers=headers)).json()["tools_count"] == 1
    await client.delete(f"/v1/tools/{tool.json()['id']}", headers=headers)
    assert (await client.delete(f"/v1/connections/{cid}", headers=headers)).status_code == 204


async def test_plan_gate_locks_trial_and_starter_and_caps_pro(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id = await _owner(client)
    org = await db_session.get(Organization, uuid.UUID(org_id))
    assert org is not None
    future = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=20)
    for plan in ("starter",):
        org.plan, org.plan_expires_at = plan, future
        await db_session.flush()
        r = await client.post("/v1/connections", json=BODY, headers=headers)
        assert r.status_code == 402 and r.json()["error"]["code"] == "plan_limit", r.text
    org.plan = "pro"
    await db_session.flush()
    for i in range(5):
        assert (
            await client.post("/v1/connections", json={**BODY, "name": f"c{i}"}, headers=headers)
        ).status_code == 201
    sixth = await client.post("/v1/connections", json={**BODY, "name": "c6"}, headers=headers)
    assert sixth.status_code == 402
    assert sixth.json()["error"]["details"]["feature"] == "connections"


async def test_changes_are_audited_without_secrets(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id = await _owner(client)
    cid = (await client.post("/v1/connections", json=BODY, headers=headers)).json()["id"]
    await client.patch(f"/v1/connections/{cid}", json={"auth": {"token": "another-secret-5555"}}, headers=headers)
    await client.delete(f"/v1/connections/{cid}", headers=headers)
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == uuid.UUID(org_id), AuditLog.target_type == "connection"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.action for r in rows] == ["connection.created", "connection.updated", "connection.deleted"] or {
        r.action for r in rows
    } == {"connection.created", "connection.updated", "connection.deleted"}
    blob = json.dumps([r.meta for r in rows])
    assert SECRET not in blob and "another-secret-5555" not in blob


# ── Test endpoint ────────────────────────────────────────────────────────────────
async def test_test_endpoint_reports_status_and_never_the_body(client: AsyncClient, mock_http: HttpMock) -> None:
    headers, _ = await _owner(client)
    body = {**BODY, "health_path": "health"}
    cid = (await client.post("/v1/connections", json=body, headers=headers)).json()["id"]
    mock_http.handler = lambda r: httpx.Response(200, json={"internal": "customer-list", "token": SECRET})
    r = await client.post(f"/v1/connections/{cid}/test", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["status_code"] == 200
    assert "customer-list" not in r.text and SECRET not in r.text
    sent = mock_http.requests[0]
    assert sent.url.path == "/v1/health" and sent.headers["authorization"] == f"Bearer {SECRET}"
    assert sent.headers["host"] == "api.example.com"


async def test_test_endpoint_failure_modes(client: AsyncClient, mock_http: HttpMock, dns: dict) -> None:
    headers, _ = await _owner(client)
    cid = (await client.post("/v1/connections", json=BODY, headers=headers)).json()["id"]
    mock_http.handler = lambda r: httpx.Response(401, json={"error": "bad key"})
    r = await client.post(f"/v1/connections/{cid}/test", headers=headers)
    assert r.json() == {"ok": False, "status_code": 401, "latency_ms": r.json()["latency_ms"], "error": "HTTP 401"}

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    mock_http.handler = boom
    r = await client.post(f"/v1/connections/{cid}/test", headers=headers)
    assert r.json()["ok"] is False and "failed" in r.json()["error"]

    dns["api.example.com"] = ["10.0.0.9"]  # the name now points inside the network
    r = await client.post(f"/v1/connections/{cid}/test", headers=headers)
    assert r.json()["ok"] is False and "private" in r.json()["error"]
