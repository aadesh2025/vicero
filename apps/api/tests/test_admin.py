"""Phase 17 tests: platform-staff admin console endpoints.

Staff endpoints are org-agnostic and gated by `require_staff` (user.is_staff).
Non-staff (and unauthenticated) requests must be rejected with 403.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.n8n_client import N8nClient
from app.models import Organization, Tool, User
from app.modules.admin import service as admin_service


def _mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def _signup(client: AsyncClient, email: str) -> str:
    r = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _make_staff(db_session: AsyncSession, email: str) -> None:
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.is_staff = True
    await db_session.flush()


async def test_non_staff_blocked_from_all_admin_endpoints(client: AsyncClient) -> None:
    token = await _signup(client, "plain@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    for path in ("/v1/admin/orgs", "/v1/admin/users", "/v1/admin/usage", "/v1/admin/health", "/v1/admin/feature-flags"):
        r = await client.get(path, headers=headers)
        assert r.status_code == 403, f"{path} -> {r.status_code} {r.text}"


async def test_unauthenticated_blocked(client: AsyncClient) -> None:
    r = await client.get("/v1/admin/orgs")
    assert r.status_code in (401, 403), r.text


async def test_staff_can_list_orgs_and_users(client: AsyncClient, db_session: AsyncSession) -> None:
    token = await _signup(client, "staff@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    # Create an org so there is something to aggregate.
    await client.post("/v1/orgs", json={"name": "StaffOrg"}, headers=headers)
    await _make_staff(db_session, "staff@example.com")

    orgs = await client.get("/v1/admin/orgs", headers=headers)
    assert orgs.status_code == 200, orgs.text
    names = [o["name"] for o in orgs.json()]
    assert "StaffOrg" in names
    row = next(o for o in orgs.json() if o["name"] == "StaffOrg")
    assert row["members"] >= 1  # the creator

    users = await client.get("/v1/admin/users", headers=headers)
    assert users.status_code == 200, users.text
    emails = [u["email"] for u in users.json()]
    assert "staff@example.com" in emails
    me = next(u for u in users.json() if u["email"] == "staff@example.com")
    assert me["is_staff"] is True


async def test_staff_usage_and_health(client: AsyncClient, db_session: AsyncSession) -> None:
    token = await _signup(client, "staff2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await _make_staff(db_session, "staff2@example.com")

    usage = await client.get("/v1/admin/usage", headers=headers)
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["users"] >= 1
    assert "top_orgs" in body

    health = await client.get("/v1/admin/health", headers=headers)
    assert health.status_code == 200, health.text
    assert health.json()["database"] is True


async def test_staff_feature_flag_upsert_roundtrip(client: AsyncClient, db_session: AsyncSession) -> None:
    token = await _signup(client, "staff3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await _make_staff(db_session, "staff3@example.com")

    # Create.
    put = await client.put(
        "/v1/admin/feature-flags/new_dashboard",
        json={"enabled": True, "description": "Beta dashboard"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["enabled"] is True

    # Update (on-conflict path).
    put2 = await client.put(
        "/v1/admin/feature-flags/new_dashboard",
        json={"enabled": False, "description": "Rolled back"},
        headers=headers,
    )
    assert put2.status_code == 200, put2.text
    assert put2.json()["enabled"] is False

    flags = await client.get("/v1/admin/feature-flags", headers=headers)
    assert flags.status_code == 200, flags.text
    flag = next(f for f in flags.json() if f["key"] == "new_dashboard")
    assert flag["enabled"] is False
    assert flag["description"] == "Rolled back"


async def test_non_staff_cannot_write_feature_flags(client: AsyncClient) -> None:
    token = await _signup(client, "plain2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.put(
        "/v1/admin/feature-flags/x", json={"enabled": True}, headers=headers
    )
    assert r.status_code == 403, r.text


# ── Cross-org automations overview (ADR-040 tagging backlog, staff-only) ────────────
async def test_non_staff_blocked_from_automations(client: AsyncClient) -> None:
    token = await _signup(client, "plain3@example.com")
    r = await client.get("/v1/admin/automations", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text


async def test_automations_overview_resolves_owner_from_tags(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staff see every workflow — including the internal and unowned ones a client never
    would — each resolved to the org its tags name."""
    token = await _signup(client, "autostaff@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org = await client.post("/v1/orgs", json={"name": "Tagged Co"}, headers=headers)
    slug = org.json()["slug"]
    await _make_staff(db_session, "autostaff@example.com")

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "w1", "name": "Tagged Co — Booking", "active": True,
                     "tags": [{"name": slug}], "nodes": []},
                    {"id": "w2", "name": "Orphan Flow", "active": False, "tags": [], "nodes": []},
                    {"id": "w3", "name": "Provisioner", "active": True,
                     "tags": [{"name": "internal"}], "nodes": []},
                    {"id": "w4", "name": "Starter", "active": True,
                     "tags": [{"name": "shared-template"}], "nodes": []},
                    {"id": "w5", "name": "Typo Flow", "active": True,
                     "tags": [{"name": "no-such-org"}], "nodes": []},
                ]
            },
        )

    monkeypatch.setattr(
        admin_service, "get_n8n_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler))
    )

    r = await client.get("/v1/admin/automations", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    by_id = {w["id"]: w for w in body["workflows"]}
    assert len(by_id) == 5  # nothing is filtered out for staff

    assert by_id["w1"]["owner_kind"] == "org"
    assert by_id["w1"]["owner"] == slug
    assert by_id["w1"]["organization_name"] == "Tagged Co"
    assert by_id["w2"]["owner_kind"] == "untagged"
    assert by_id["w3"]["owner_kind"] == "internal"
    assert by_id["w4"]["owner_kind"] == "shared-template"
    # A tag matching no organization is called out rather than silently treated as an owner.
    assert by_id["w5"]["owner_kind"] == "unknown-org"

    # Unowned sorts first — the page is a to-do list for the tagging backlog.
    assert body["workflows"][0]["id"] == "w2"


async def test_automations_overview_lists_bindings(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each workflow shows which agent binds it, which is how staff spot an automation
    that is bound but no longer visible to its own org."""
    token = await _signup(client, "bindstaff@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org = await client.post("/v1/orgs", json={"name": "Bind Co"}, headers=headers)
    org_id = org.json()["id"]
    agent = await client.post(
        "/v1/agents", json={"name": "Bind Bot"}, headers={**headers, "X-Org-Id": org_id}
    )
    # n8n tools are created by /v1/tools/n8n/bind (POST /v1/tools only accepts builtin|http),
    # and that path now needs a matching tag; the row itself is what this test is about.
    db_session.add(
        Tool(
            organization_id=uuid.UUID(org_id),
            agent_id=uuid.UUID(agent.json()["id"]),
            name="starter_automation",
            type="n8n",
            enabled=True,
            config={"workflow_id": "w9", "webhook_url": "http://n8n/webhook/x", "mode": "sync"},
            input_schema={},
        )
    )
    await db_session.flush()
    await _make_staff(db_session, "bindstaff@example.com")

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "w9", "name": "Bound Flow", "active": True, "tags": [], "nodes": []}]},
        )

    monkeypatch.setattr(
        admin_service, "get_n8n_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler))
    )

    r = await client.get("/v1/admin/automations", headers=headers)
    assert r.status_code == 200, r.text
    wf = r.json()["workflows"][0]
    assert wf["owner_kind"] == "untagged"  # bound, yet invisible to the org that uses it
    assert len(wf["bindings"]) == 1
    binding = wf["bindings"][0]
    assert binding["organization_name"] == "Bind Co"
    assert binding["agent_name"] == "Bind Bot"
    assert binding["tool_name"] == "starter_automation"
    assert binding["mode"] == "sync"


async def test_automations_overview_reports_n8n_being_down(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable or keyless n8n is an operational state, not a 500 — and the console
    must not render an empty table that reads as 'no automations exist'."""
    token = await _signup(client, "downstaff@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await _make_staff(db_session, "downstaff@example.com")

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    monkeypatch.setattr(
        admin_service, "get_n8n_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler))
    )

    r = await client.get("/v1/admin/automations", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["workflows"] == []
    assert r.json()["error"]


async def test_deleted_orgs_are_hidden_from_the_console_by_default(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`list_users` always filtered `deleted_at`; `list_orgs` never did.

    A client who deleted their workspace therefore stayed in the staff roster forever, sitting
    next to live tenants and indistinguishable at a glance. The two endpoints now agree.
    """
    token = await _signup(client, "admin.softdel@example.com")
    await _make_staff(db_session, "admin.softdel@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    live = await client.post("/v1/orgs", json={"name": "Still Trading"}, headers=headers)
    gone = await client.post("/v1/orgs", json={"name": "Wound Up"}, headers=headers)
    assert live.status_code == 201 and gone.status_code == 201

    doomed = (
        await db_session.execute(select(Organization).where(Organization.id == uuid.UUID(gone.json()["id"])))
    ).scalar_one()
    doomed.deleted_at = dt.datetime.now(tz=dt.UTC)
    await db_session.flush()

    names = [o["name"] for o in (await client.get("/v1/admin/orgs", headers=headers)).json()]
    assert "Still Trading" in names
    assert "Wound Up" not in names, "a soft-deleted org must not appear in the default roster"

    # The audit view still reaches it — "which client left, and when" is a real question.
    all_names = [
        o["name"] for o in (await client.get("/v1/admin/orgs?include_deleted=true", headers=headers)).json()
    ]
    assert "Wound Up" in all_names
    assert next(o for o in (await client.get("/v1/admin/orgs?include_deleted=true", headers=headers)).json()
                if o["name"] == "Wound Up")["deleted"] is True


async def test_roster_shows_who_has_access_to_each_org_and_at_what_level(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A member *count* answers "how many", which was never the question.

    "Who can publish to this client's agent, and from which address" is.
    """
    token = await _signup(client, "admin.roster@example.com")
    await _make_staff(db_session, "admin.roster@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org = await client.post("/v1/orgs", json={"name": "Roster Co"}, headers=headers)
    oid = org.json()["id"]

    # A second person, invited as editor.
    await _signup(client, "editor.roster@example.com")
    invite = await client.post(
        f"/v1/orgs/{oid}/invitations",
        json={"email": "editor.roster@example.com", "role": "editor"},
        headers=headers,
    )
    editor_token = (
        await client.post(
            "/v1/auth/login",
            json={"email": "editor.roster@example.com", "password": "password123"},
        )
    ).json()["access_token"]
    await client.post(
        f"/v1/orgs/invitations/{invite.json()['accept_token']}/accept",
        headers={"Authorization": f"Bearer {editor_token}"},
    )

    row = next(o for o in (await client.get("/v1/admin/orgs", headers=headers)).json() if o["id"] == oid)
    by_email = {m["email"]: m["role"] for m in row["member_list"]}
    assert by_email["admin.roster@example.com"] == "owner"
    assert by_email["editor.roster@example.com"] == "editor"

    # And the same relationship from the user side.
    users = (await client.get("/v1/admin/users", headers=headers)).json()
    editor = next(u for u in users if u["email"] == "editor.roster@example.com")
    assert [(m["organization_name"], m["role"]) for m in editor["memberships"]] == [("Roster Co", "editor")]


async def test_machine_accounts_are_hidden_from_the_roster(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`provision@vicero.dev` is a working credential, not a person.

    Deleting it to tidy the list would break `scripts/provision-client.mjs`, so it is flagged
    and filtered instead — still reachable with `?include_system=true`.
    """
    token = await _signup(client, "admin.system@example.com")
    await _make_staff(db_session, "admin.system@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    await _signup(client, "robot.system@example.com")
    robot = (
        await db_session.execute(select(User).where(User.email == "robot.system@example.com"))
    ).scalar_one()
    robot.is_system = True
    await db_session.flush()

    default = [u["email"] for u in (await client.get("/v1/admin/users", headers=headers)).json()]
    assert "robot.system@example.com" not in default
    assert "admin.system@example.com" in default, "real operators must still be listed"

    with_system = [
        u["email"]
        for u in (await client.get("/v1/admin/users?include_system=true", headers=headers)).json()
    ]
    assert "robot.system@example.com" in with_system


async def test_workflows_awaiting_review_counted_in_admin_console(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """docs/17 Phase 4's admin-console equivalent of `agents_with_unpublished_changes` — but
    reading the real `WorkflowVersion.status == "in_review"` signal directly (ADR-080) rather
    than a version-number proxy, since workflows (unlike agents) actually have one."""
    token = await _signup(client, "staff.workflows@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org = await client.post("/v1/orgs", json={"name": "WorkflowReviewOrg"}, headers=headers)
    org_headers = {**headers, "X-Org-Id": org.json()["id"]}
    agent = await client.post(
        "/v1/agents", json={"name": "Bot", "description": "d"}, headers=org_headers
    )
    workflow = await client.post(
        f"/v1/agents/{agent.json()['id']}/workflows", json={"name": "Flow"}, headers=org_headers
    )
    graph = {
        "nodes": [{"id": "s1", "type": "start"}, {"id": "e1", "type": "end"}],
        "edges": [{"source": "s1", "target": "e1"}],
    }
    version = await client.post(
        f"/v1/workflows/{workflow.json()['id']}/versions", json={"graph": graph}, headers=org_headers
    )
    await _make_staff(db_session, "staff.workflows@example.com")

    before = await client.get("/v1/admin/orgs", headers=headers)
    row = next(o for o in before.json() if o["name"] == "WorkflowReviewOrg")
    assert row["workflows_awaiting_review"] == 0

    submitted = await client.post(
        f"/v1/workflows/{workflow.json()['id']}/versions/{version.json()['version']}/submit-review",
        headers=org_headers,
    )
    assert submitted.status_code == 200, submitted.text

    after = await client.get("/v1/admin/orgs", headers=headers)
    row = next(o for o in after.json() if o["name"] == "WorkflowReviewOrg")
    assert row["workflows_awaiting_review"] == 1

    published = await client.post(
        f"/v1/workflows/{workflow.json()['id']}/versions/{version.json()['version']}/publish",
        headers=org_headers,
    )
    assert published.status_code == 200, published.text

    final = await client.get("/v1/admin/orgs", headers=headers)
    row = next(o for o in final.json() if o["name"] == "WorkflowReviewOrg")
    assert row["workflows_awaiting_review"] == 0
