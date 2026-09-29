"""Phase A4: creation-time plan enforcement (docs/22 §11).

A0-A3 built the entitlement engine and the endpoints that report it; none of it stopped anyone
from exceeding their limits. This is the gate itself, one resource per section — at-cap → 402,
under-cap → succeeds, unlimited (`None`) → always succeeds, `trial_expired`/`plan_expired` → 402
regardless of count, and an org already over a *new*, lower cap keeps what it has.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import get_email_backend
from app.models import KnowledgeBase, Organization
from tests.selfserve_helpers import bearer, signup, trial_org, verify_email

pytestmark = pytest.mark.usefixtures("self_serve")


async def _set_plan(
    db: AsyncSession, org_id: str, plan: str, *, expires_at: dt.datetime | None = None
) -> None:
    org = await db.get(Organization, uuid.UUID(org_id))
    assert org is not None
    org.plan = plan
    org.plan_expires_at = expires_at
    await db.flush()


FUTURE = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=20)
PAST = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)


async def _create_kb(client: AsyncClient, headers: dict[str, str], name: str = "KB") -> Response:
    return await client.post(
        "/v1/knowledge", json={"name": name, "embedding_provider": "fake"}, headers=headers
    )


# ── knowledge bases (max_knowledge_bases) ─────────────────────────────────────
async def test_kb_creation_blocked_at_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`starter` allows exactly 1 knowledge base."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)

    first = await _create_kb(client, headers, "First")
    assert first.status_code == 201, first.text

    second = await _create_kb(client, headers, "Second")
    assert second.status_code == 402, second.text
    assert second.json()["error"]["code"] == "plan_limit"
    assert "Starter" in second.json()["error"]["message"]
    assert second.json()["error"]["details"]["feature"] == "knowledge_bases"


async def test_kb_creation_succeeds_under_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`pro` allows 5 — well under is a plain 201."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)
    resp = await _create_kb(client, headers)
    assert resp.status_code == 201, resp.text


async def test_kb_creation_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "legacy")
    for i in range(3):  # legacy has no cap at all
        resp = await _create_kb(client, headers, f"KB {i}")
        assert resp.status_code == 201, resp.text


async def test_kb_creation_blocked_when_the_plan_has_expired_regardless_of_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A lapsed Pro org (well under its 5-KB cap) still can't create one — expiry blocks
    creation of every limited resource, not just messaging."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_kb(client, headers)
    assert resp.status_code == 402, resp.text
    assert resp.json()["error"]["code"] == "plan_limit"


async def test_an_org_already_over_a_new_lower_cap_keeps_its_existing_knowledge_bases(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A `pro` org with 3 KBs downgraded to `starter` (cap 1): the existing 3 are untouched —
    the gate blocks creating a 4th, it never deletes or disables what already exists."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)
    for i in range(3):
        assert (await _create_kb(client, headers, f"KB {i}")).status_code == 201

    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)

    existing = (
        await db_session.execute(
            KnowledgeBase.__table__.select().where(KnowledgeBase.organization_id == uuid.UUID(org_id))
        )
    ).fetchall()
    assert len(existing) == 3  # untouched

    blocked = await _create_kb(client, headers, "4th")
    assert blocked.status_code == 402, blocked.text


# ── documents + storage (max_documents, storage_bytes) ───────────────────────
async def _kb_id(client: AsyncClient, headers: dict[str, str]) -> str:
    resp = await _create_kb(client, headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _create_text_doc(client: AsyncClient, headers: dict[str, str], kb_id: str, text: str) -> Response:
    return await client.post(
        f"/v1/knowledge/{kb_id}/documents",
        json={"source_type": "text", "text": text, "filename": "note.txt"},
        headers=headers,
    )


async def test_document_creation_blocked_at_the_document_count_cap(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "trial")  # trial's own cap: max_documents=10
    kb_id = await _kb_id(client, headers)

    for i in range(10):
        resp = await _create_text_doc(client, headers, kb_id, f"document number {i}")
        assert resp.status_code == 201, resp.text

    blocked = await _create_text_doc(client, headers, kb_id, "the 11th")
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["error"]["details"]["feature"] == "documents"


async def test_document_creation_succeeds_under_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, _org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)
    resp = await _create_text_doc(client, headers, kb_id, "well under the trial's cap of 10")
    assert resp.status_code == 201, resp.text


async def test_document_creation_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "legacy")
    kb_id = await _kb_id(client, headers)
    for i in range(12):  # comfortably past the trial's own cap of 10
        resp = await _create_text_doc(client, headers, kb_id, f"doc {i}")
        assert resp.status_code == 201, resp.text


async def test_document_creation_blocked_when_the_plan_has_expired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_text_doc(client, headers, kb_id, "should be blocked")
    assert resp.status_code == 402, resp.text


async def test_storage_bytes_is_checked_before_the_upload_is_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Trial's storage cap is 100 MB. One oversized "document" must be refused outright rather
    than partially written, and the counter must not move."""
    _, headers, _org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)

    too_big = "x" * (101 * 1024 * 1024)  # 101 MB of text — over the trial's 100 MB cap
    resp = await _create_text_doc(client, headers, kb_id, too_big)
    assert resp.status_code == 402, resp.text
    assert resp.json()["error"]["details"]["feature"] == "storage"

    docs = await client.get(f"/v1/knowledge/{kb_id}/documents", headers=headers)
    assert docs.json() == []  # nothing was created


async def test_org_storage_usage_tracks_uploads_and_deletes_live(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The A1 counter (`org_storage_usage`) was only ever backfilled once; this pins that it now
    moves on every create and delete, which is what makes the storage gate meaningful at all."""
    from app.models import OrgStorageUsage

    _, headers, org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)

    text = "a" * 1000
    created = await _create_text_doc(client, headers, kb_id, text)
    assert created.status_code == 201, created.text
    doc_id = created.json()["id"]

    row = await db_session.get(OrgStorageUsage, uuid.UUID(org_id))
    assert row is not None
    assert row.bytes_used == 1000
    assert row.documents_count == 1

    deleted = await client.delete(f"/v1/knowledge/documents/{doc_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    await db_session.refresh(row)
    assert row.bytes_used == 0
    assert row.documents_count == 0


# ── workflows (max_workflows) ─────────────────────────────────────────────────
async def _agent_id(client: AsyncClient, headers: dict[str, str], name: str = "Agent") -> str:
    resp = await client.post("/v1/agents", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _create_workflow(client: AsyncClient, headers: dict[str, str], agent_id: str, name: str) -> Response:
    return await client.post(f"/v1/agents/{agent_id}/workflows", json={"name": name}, headers=headers)


async def test_workflow_creation_blocked_at_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`pro` allows 10 workflows."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)
    agent_id = await _agent_id(client, headers)

    for i in range(10):
        resp = await _create_workflow(client, headers, agent_id, f"Workflow {i}")
        assert resp.status_code == 201, resp.text

    blocked = await _create_workflow(client, headers, agent_id, "the 11th")
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["error"]["details"]["feature"] == "workflows"


async def test_workflow_creation_not_included_on_a_plan_with_zero(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`starter` doesn't include workflows at all (`max_workflows=0`) — the existing
    `require_feature("workflows")` flag check rejects it before the slot-count check even
    runs, since the feature itself isn't on the plan. Same 402, a message distinct from
    the "at the cap" one."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)
    agent_id = await _agent_id(client, headers)
    resp = await _create_workflow(client, headers, agent_id, "nope")
    assert resp.status_code == 402, resp.text
    assert "isn't included" in resp.json()["error"]["message"]


async def test_workflow_creation_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "business", expires_at=FUTURE)  # max_workflows=None
    agent_id = await _agent_id(client, headers)
    for i in range(12):  # past Pro's own cap of 10
        resp = await _create_workflow(client, headers, agent_id, f"wf {i}")
        assert resp.status_code == 201, resp.text


async def test_workflow_creation_blocked_when_the_plan_has_expired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    agent_id = await _agent_id(client, headers)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_workflow(client, headers, agent_id, "should be blocked")
    assert resp.status_code == 402, resp.text


async def test_an_org_already_over_a_new_lower_workflow_cap_keeps_its_existing_workflows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.models import Workflow

    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "business", expires_at=FUTURE)
    agent_id = await _agent_id(client, headers)
    for i in range(3):
        assert (await _create_workflow(client, headers, agent_id, f"wf {i}")).status_code == 201

    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)  # workflows -> 0

    existing = (
        await db_session.execute(
            Workflow.__table__.select().where(Workflow.organization_id == uuid.UUID(org_id))
        )
    ).fetchall()
    assert len(existing) == 3

    blocked = await _create_workflow(client, headers, agent_id, "4th")
    assert blocked.status_code == 402, blocked.text


# ── tools + MCP servers (max_tools, shared pool) ──────────────────────────────
async def _create_builtin_tool(client: AsyncClient, headers: dict[str, str]) -> Response:
    # `name` must match a real key in app.tools.builtins.BUILTINS for type="builtin" — nothing
    # here needs distinct names, since the cap counts rows, not distinct tools.
    return await client.post("/v1/tools", json={"name": "get_datetime", "type": "builtin"}, headers=headers)


async def test_tool_creation_blocked_at_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`pro` allows 8 tools."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)

    for _i in range(8):
        resp = await _create_builtin_tool(client, headers)
        assert resp.status_code == 201, resp.text

    blocked = await _create_builtin_tool(client, headers)
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["error"]["details"]["feature"] == "tools"


async def test_tool_creation_not_included_on_a_plan_with_zero(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`starter` doesn't include tool_calling at all — the existing
    `require_feature("tool_calling")` flag check rejects it before the slot-count check."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)
    resp = await _create_builtin_tool(client, headers)
    assert resp.status_code == 402, resp.text
    assert "isn't included" in resp.json()["error"]["message"]


async def test_tool_creation_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "legacy")
    for _i in range(10):  # past Pro's own cap of 8
        resp = await _create_builtin_tool(client, headers)
        assert resp.status_code == 201, resp.text


async def test_tool_creation_blocked_when_the_plan_has_expired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_builtin_tool(client, headers)
    assert resp.status_code == 402, resp.text


async def test_mcp_servers_share_the_tool_cap_with_ordinary_tools(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """docs/22 §11 groups tools and MCP servers under one "Tool count" — 6 ordinary tools plus
    2 MCP servers already fills Pro's cap of 8."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)

    for _i in range(6):
        assert (await _create_builtin_tool(client, headers)).status_code == 201

    for i in range(2):
        resp = await client.post(
            "/v1/mcp/servers",
            json={"name": f"mcp-{i}", "transport": "sse", "url_or_command": "https://example.com/mcp"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    blocked = await _create_builtin_tool(client, headers)
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["error"]["details"]["feature"] == "tools"


async def _create_webhook(client: AsyncClient, headers: dict[str, str], url: str) -> Response:
    return await client.post("/v1/webhooks", json={"url": url, "events": ["*"]}, headers=headers)


async def test_webhook_creation_blocked_at_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`pro` allows 5 webhook endpoints."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)

    for i in range(5):
        resp = await _create_webhook(client, headers, f"https://example.com/hook{i}")
        assert resp.status_code == 201, resp.text

    blocked = await _create_webhook(client, headers, "https://example.com/hook5")
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["error"]["details"]["feature"] == "webhooks"


async def test_webhook_creation_not_included_on_a_plan_with_zero(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`starter` doesn't include webhooks at all (`max_webhooks=0`) - unlike workflows/tools,
    there's no separate feature-flag check here, so `require_webhook_slot`'s own zero-branch
    message is what fires."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)
    resp = await _create_webhook(client, headers, "https://example.com/hook")
    assert resp.status_code == 402, resp.text
    assert "does not include" in resp.json()["error"]["message"]


async def test_webhook_creation_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "business", expires_at=FUTURE)  # max_webhooks=None
    for i in range(7):  # past Pro's own cap of 5
        resp = await _create_webhook(client, headers, f"https://example.com/hook{i}")
        assert resp.status_code == 201, resp.text


async def test_webhook_creation_blocked_when_the_plan_has_expired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_webhook(client, headers, "https://example.com/hook")
    assert resp.status_code == 402, resp.text


async def test_an_org_already_over_a_new_lower_webhook_cap_keeps_its_existing_webhooks(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.models import WebhookEndpoint

    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "business", expires_at=FUTURE)
    for i in range(6):
        assert (
            await _create_webhook(client, headers, f"https://example.com/hook{i}")
        ).status_code == 201

    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)  # webhooks -> 0

    existing = (
        await db_session.execute(
            WebhookEndpoint.__table__.select().where(
                WebhookEndpoint.organization_id == uuid.UUID(org_id)
            )
        )
    ).fetchall()
    assert len(existing) == 6

    blocked = await _create_webhook(client, headers, "https://example.com/hook6")
    assert blocked.status_code == 402, blocked.text


def _last_invite_token() -> str:
    body = get_email_backend().outbox[-1].body
    m = re.search(r"Token:\s*(\S+)", body)
    assert m, body
    return m.group(1)


async def _invite(
    client: AsyncClient, headers: dict[str, str], org_id: str, email: str, role: str = "editor"
) -> Response:
    return await client.post(
        f"/v1/orgs/{org_id}/invitations", json={"email": email, "role": role}, headers=headers
    )


async def _invite_and_accept(
    client: AsyncClient, headers: dict[str, str], org_id: str, email: str
) -> Response:
    inv = await _invite(client, headers, org_id, email)
    assert inv.status_code == 201, inv.text
    token = _last_invite_token()
    invitee_auth = await signup(client, email)
    return await client.post(f"/v1/orgs/invitations/{token}/accept", headers=bearer(invitee_auth))


async def test_team_invite_blocked_at_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`pro` allows 5 team members; the owner already occupies one seat."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)

    for i in range(4):  # + the owner = 5, at the cap
        resp = await _invite_and_accept(client, headers, org_id, f"member{i}.{org_id[:8]}@example.com")
        assert resp.status_code == 200, resp.text

    blocked = await _invite(client, headers, org_id, f"one-too-many.{org_id[:8]}@example.com")
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["error"]["details"]["feature"] == "team_members"


async def test_team_invite_succeeds_under_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)
    resp = await _invite(client, headers, org_id, f"newbie.{org_id[:8]}@example.com")
    assert resp.status_code == 201, resp.text


async def test_team_accept_blocked_when_the_cap_fills_before_this_invite_is_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Gating only invite-creation would let an org pre-invite past its seat count — the
    accept-time check is what actually stops the overage once other invitees fill the cap
    first (docs/22 §11)."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)

    early = await _invite(client, headers, org_id, f"early.{org_id[:8]}@example.com")
    assert early.status_code == 201, early.text
    early_token = _last_invite_token()

    for i in range(4):  # + the owner = 5, fills the cap before `early` gets accepted
        resp = await _invite_and_accept(client, headers, org_id, f"filler{i}.{org_id[:8]}@example.com")
        assert resp.status_code == 200, resp.text

    early_invitee = await signup(client, f"early.{org_id[:8]}@example.com")
    late_accept = await client.post(
        f"/v1/orgs/invitations/{early_token}/accept", headers=bearer(early_invitee)
    )
    assert late_accept.status_code == 402, late_accept.text
    assert late_accept.json()["error"]["details"]["feature"] == "team_members"


async def test_team_invite_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "legacy")  # max_team_members=None
    for i in range(6):  # past Pro's own cap of 5
        resp = await _invite_and_accept(client, headers, org_id, f"m{i}.{org_id[:8]}@example.com")
        assert resp.status_code == 200, resp.text


async def test_team_invite_blocked_when_the_plan_has_expired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _invite(client, headers, org_id, f"nope.{org_id[:8]}@example.com")
    assert resp.status_code == 402, resp.text


async def test_an_org_already_over_a_new_lower_team_cap_keeps_its_existing_members(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.models import Membership

    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "business", expires_at=FUTURE)  # cap=15
    for i in range(4):  # + the owner = 5 active members
        resp = await _invite_and_accept(client, headers, org_id, f"m{i}.{org_id[:8]}@example.com")
        assert resp.status_code == 200, resp.text

    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)  # cap -> 1

    existing = (
        await db_session.execute(
            Membership.__table__.select().where(
                Membership.organization_id == uuid.UUID(org_id), Membership.status == "active"
            )
        )
    ).fetchall()
    assert len(existing) == 5

    blocked = await _invite(client, headers, org_id, f"sixth.{org_id[:8]}@example.com")
    assert blocked.status_code == 402, blocked.text


async def _create_channel(client: AsyncClient, headers: dict[str, str], agent_id: str, kind: str) -> Response:
    return await client.post(
        "/v1/channels", json={"agent_id": agent_id, "type": kind, "config": {}}, headers=headers
    )


async def test_channel_connect_blocked_when_not_on_the_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`starter` only includes the `web` channel — `whatsapp` isn't in its allowlist."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)
    agent_id = await _agent_id(client, headers)
    resp = await _create_channel(client, headers, agent_id, "whatsapp")
    assert resp.status_code == 402, resp.text
    assert resp.json()["error"]["details"]["feature"] == "channels"


async def test_channel_connect_succeeds_when_on_the_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)  # includes whatsapp
    agent_id = await _agent_id(client, headers)
    resp = await _create_channel(client, headers, agent_id, "whatsapp")
    assert resp.status_code == 201, resp.text


async def test_channel_connect_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "business", expires_at=FUTURE)  # channels=None
    agent_id = await _agent_id(client, headers)
    resp = await _create_channel(client, headers, agent_id, "discord")  # not even on Pro
    assert resp.status_code == 201, resp.text


async def test_channel_connect_blocked_when_the_plan_has_expired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    agent_id = await _agent_id(client, headers)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_channel(client, headers, agent_id, "whatsapp")
    assert resp.status_code == 402, resp.text


async def test_channel_enable_blocked_after_a_downgrade_even_though_it_already_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """docs/22 §11: channels gate at connect *and* enable, not just message-send time — an
    existing channel from a richer plan must not be re-enable-able after a downgrade."""
    _, headers, org_id = await trial_org(client)
    await verify_email(client)  # required for enabling; isolates the channel-allowed gate
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)
    agent_id = await _agent_id(client, headers)
    created = await _create_channel(client, headers, agent_id, "whatsapp")
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]

    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)  # whatsapp no longer allowed

    enabled = await client.post(f"/v1/channels/{channel_id}/enable", headers=headers)
    assert enabled.status_code == 402, enabled.text
    assert enabled.json()["error"]["details"]["feature"] == "channels"
