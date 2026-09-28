"""Admin billing: grant, revoke, packs and the payment ledger (docs/22 §8.2).

Staff-only access is **not** retested here — `test_self_serve_admin.py` walks the real route
table and asserts 403/401 on every admin route, so the nine routes added by this phase are
covered there automatically. What this file tests is what those routes *do* once staff are in,
and above all the three rules that cost real money if they break:

    money  — a pack's price is computed from the plan, never taken from the request
    trust  — a revoke expires a workspace, it never deletes one
    proof  — every mutation leaves both billing evidence and an audit entry
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage as billing_usage
from app.models import Agent, AuditLog, BillingCycle, Organization, PlanGrant, User
from tests.selfserve_helpers import bearer, org_headers, signup, trial_org

pytestmark = pytest.mark.usefixtures("self_serve")


async def staff_headers(client: AsyncClient, db: AsyncSession) -> dict[str, str]:
    """A signed-up user promoted to platform staff — the same route the console really uses."""
    auth = await signup(client)
    user = await db.get(User, uuid.UUID(auth["user"]["id"]))
    assert user is not None
    user.is_staff = True
    await db.flush()
    return bearer(auth)


async def grant(
    client: AsyncClient,
    staff: dict[str, str],
    org_id: str,
    plan: str = "pro",
    **body: object,
) -> dict:
    payload: dict[str, object] = {"plan": plan, "note": "Acme Corp — INV-001"}
    payload.update(body)
    resp = await client.post(f"/v1/admin/orgs/{org_id}/plan", json=payload, headers=staff)
    return {"status": resp.status_code, "body": resp.json(), "raw": resp}


async def _org(db: AsyncSession, org_id: str) -> Organization:
    org = await db.get(Organization, uuid.UUID(org_id))
    assert org is not None
    await db.refresh(org)
    return org


# ── granting ────────────────────────────────────────────────────────────────────
async def test_grant_puts_a_workspace_on_a_paid_plan_immediately(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No cache to invalidate and no restart: the entitlement engine reads the row every time."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)

    before = await billing_usage.load_entitlements(db_session, await _org(db_session, org_id))
    assert before.status == "trial"
    assert before.max_agents == 1
    assert not before.allows("n8n")

    out = await grant(client, staff, org_id, "pro", payment_received=True, method="bank transfer")
    assert out["status"] == 200, out["body"]
    assert out["body"]["plan"] == "pro"
    assert out["body"]["status"] == "pro"
    assert out["body"]["plan_source"] == "admin"

    after = await billing_usage.load_entitlements(db_session, await _org(db_session, org_id))
    assert after.status == "pro"
    assert after.max_agents == 10
    assert after.allows("n8n")


async def test_grant_without_a_note_is_refused_and_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Billing evidence with no reason is not evidence — and a refusal must not half-apply."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)

    out = await grant(client, staff, org_id, "pro", note="   ")
    assert out["status"] == 400
    assert out["body"]["error"]["code"] == "note_required"

    org = await _org(db_session, org_id)
    assert org.plan == "trial", "a rejected grant must not touch the row"
    assert org.plan_source != "admin"
    grants = await db_session.scalar(
        select(func.count()).select_from(PlanGrant).where(PlanGrant.organization_id == org.id)
    )
    assert grants == 0


async def test_grant_with_a_past_expiry_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A past date would silently grant nothing at all — the worst possible outcome."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    yesterday = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)

    out = await grant(client, staff, org_id, "pro", expires_at=yesterday.isoformat())
    assert out["status"] == 400
    assert out["body"]["error"]["code"] == "invalid_expiry"
    assert (await _org(db_session, org_id)).plan == "trial"


async def test_grant_with_an_unknown_plan_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`get_entitlements` fails open on an unknown plan so a typo can't lock a client out —
    which is exactly why the write path must refuse to store one in the first place."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)

    out = await grant(client, staff, org_id, "enterprise")
    assert out["status"] == 400
    assert out["body"]["error"]["code"] == "invalid_plan"
    assert (await _org(db_session, org_id)).plan == "trial"


async def test_grant_leaves_both_billing_evidence_and_an_audit_entry(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two records on purpose: `plan_grants` is the billing view, `audit_logs` the security one."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    oid = uuid.UUID(org_id)

    assert (await grant(client, staff, org_id, "starter"))["status"] == 200

    grants = (
        (await db_session.execute(select(PlanGrant).where(PlanGrant.organization_id == oid)))
        .scalars()
        .all()
    )
    assert [g.action for g in grants] == ["granted"], "exactly one granted row, never two"
    assert grants[0].note == "Acme Corp — INV-001"
    assert grants[0].actor_id is not None

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == oid, AuditLog.action == "billing.plan_granted"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].meta["from"] == "trial"
    assert audits[0].meta["to"] == "starter"


# ── the payment ledger ──────────────────────────────────────────────────────────
async def test_granting_a_paid_plan_opens_a_paid_cycle_when_payment_received(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The normal flow: the client pays, then you grant — so the cycle opens already settled."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)

    await grant(
        client, staff, org_id, "pro", payment_received=True, method="UPI", reference="UTR-99"
    )
    cycles = (await client.get("/v1/admin/billing/cycles", headers=staff)).json()
    mine = [c for c in cycles if c["organization_id"] == org_id]
    assert len(mine) == 1
    assert mine[0]["payment_state"] == "paid"
    assert mine[0]["amount_usd_cents"] == 9900, "$99 Pro, copied from the plan at cycle open"
    assert mine[0]["method"] == "UPI"
    assert mine[0]["reference"] == "UTR-99"


async def test_granting_a_paid_plan_opens_a_pending_cycle_otherwise(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Access starts now; the workspace just lands on the collections list until you mark it."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)

    await grant(client, staff, org_id, "starter", payment_received=False)
    pending = (await client.get("/v1/admin/billing/cycles?status=pending", headers=staff)).json()
    mine = [c for c in pending if c["organization_id"] == org_id]
    assert len(mine) == 1
    assert mine[0]["amount_usd_cents"] == 4900

    ent = await billing_usage.load_entitlements(db_session, await _org(db_session, org_id))
    assert ent.status == "starter", "an unpaid cycle must NOT gate access (docs/22 §5.1 rule 1)"


async def test_overdue_is_computed_from_the_clock_not_stored(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "pro", payment_received=False)

    cycle = await db_session.scalar(
        select(BillingCycle).where(BillingCycle.organization_id == uuid.UUID(org_id))
    )
    assert cycle is not None
    cycle.period_end = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)
    await db_session.flush()

    overdue = (await client.get("/v1/admin/billing/cycles?status=overdue", headers=staff)).json()
    assert [c["id"] for c in overdue if c["organization_id"] == org_id] == [str(cycle.id)]
    assert cycle.status == "pending", "still `pending` in the column — overdue is derived"


async def test_mark_paid_and_renew_extends_the_plan_and_opens_the_next_cycle(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The button pressed every month: one click records the payment and renews the term."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    expires = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=3)
    await grant(client, staff, org_id, "pro", expires_at=expires.isoformat())

    cycle_id = (await client.get("/v1/admin/billing/cycles", headers=staff)).json()[0]["id"]
    resp = await client.post(
        f"/v1/admin/billing/cycles/{cycle_id}/paid",
        json={"method": "bank transfer", "reference": "INV-002", "renew": True},
        headers=staff,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["payment_state"] == "paid"

    org = await _org(db_session, org_id)
    assert org.plan_expires_at is not None
    assert org.plan_expires_at > expires, "the term moved out by 30 days"

    cycles = (await client.get("/v1/admin/billing/cycles", headers=staff)).json()
    mine = [c for c in cycles if c["organization_id"] == org_id]
    assert len(mine) == 2
    assert sorted(c["payment_state"] for c in mine) == ["paid", "pending"]


async def test_waiving_keeps_a_free_workspace_off_the_overdue_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "business", payment_received=False)
    cycle_id = (await client.get("/v1/admin/billing/cycles", headers=staff)).json()[0]["id"]

    resp = await client.post(
        f"/v1/admin/billing/cycles/{cycle_id}/waive",
        json={"note": "internal demo workspace"},
        headers=staff,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["payment_state"] == "waived"

    pending = (await client.get("/v1/admin/billing/cycles?status=pending", headers=staff)).json()
    assert org_id not in [c["organization_id"] for c in pending]


# ── packs: the money rule ───────────────────────────────────────────────────────
async def test_a_pack_raises_the_enforced_cap_and_records_what_to_invoice(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "pro", payment_received=True)

    resp = await client.post(
        f"/v1/admin/orgs/{org_id}/messages",
        json={"packs": 3, "note": "INV-003"},
        headers=staff,
    )
    assert resp.status_code == 200, resp.text

    ent = await billing_usage.load_entitlements(db_session, await _org(db_session, org_id))
    assert ent.max_messages == 10_000, "the plan's own allowance is unchanged"
    assert ent.effective_max_messages == 11_500, "3 packs x 500 messages"

    packs = (await client.get("/v1/admin/billing/packs", headers=staff)).json()
    mine = [p for p in packs if p["organization_id"] == org_id]
    assert len(mine) == 1
    assert mine[0]["extra_messages"] == 1_500
    assert mine[0]["amount_usd_cents"] == 1_500, "3 packs x $5 on Pro"
    assert mine[0]["invoiced"] is False


async def test_pack_price_comes_from_the_plan_not_the_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Starter is $6/pack and Business $4 for the same 500 messages. The caller says how many
    packs; it never says what they cost."""
    staff = await staff_headers(client, db_session)
    _a1, _h1, starter_org = await trial_org(client)
    _a2, _h2, business_org = await trial_org(client)
    await grant(client, staff, starter_org, "starter", payment_received=True)
    await grant(client, staff, business_org, "business", payment_received=True)

    for org_id in (starter_org, business_org):
        resp = await client.post(
            f"/v1/admin/orgs/{org_id}/messages",
            json={"packs": 1, "note": "one pack", "amount_usd_cents": 1},  # ignored on purpose
            headers=staff,
        )
        assert resp.status_code == 200, resp.text

    packs = (await client.get("/v1/admin/billing/packs", headers=staff)).json()
    amounts = {p["organization_id"]: p["amount_usd_cents"] for p in packs}
    assert amounts[starter_org] == 600
    assert amounts[business_org] == 400


async def test_packs_are_refused_on_a_plan_that_does_not_sell_them(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    resp = await client.post(
        f"/v1/admin/orgs/{org_id}/messages", json={"packs": 1, "note": "x"}, headers=staff
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "packs_not_sold"


async def test_marking_a_pack_invoiced_is_the_only_mutable_field(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "pro", payment_received=True)
    await client.post(
        f"/v1/admin/orgs/{org_id}/messages", json={"packs": 2, "note": "INV-004"}, headers=staff
    )
    pack = (await client.get("/v1/admin/billing/packs", headers=staff)).json()[0]

    resp = await client.patch(
        f"/v1/admin/billing/packs/{pack['id']}", json={"invoiced": True}, headers=staff
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["invoiced"] is True
    assert resp.json()["amount_usd_cents"] == pack["amount_usd_cents"], "what was sold never moves"

    remaining = (await client.get("/v1/admin/billing/packs", headers=staff)).json()
    assert pack["id"] not in [p["id"] for p in remaining]


# ── revoking: the trust rule ────────────────────────────────────────────────────
async def test_revoke_expires_the_plan_without_deleting_anything(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A client who is late paying must not lose their work. This is the test that says so."""
    staff = await staff_headers(client, db_session)
    auth = await signup(client)
    headers = await org_headers(client, auth)
    org_id = headers["X-Org-Id"]
    await grant(client, staff, org_id, "business", payment_received=True)

    for n in range(3):
        created = await client.post("/v1/agents", json={"name": f"Bot {n}"}, headers=headers)
        assert created.status_code == 201, created.text

    resp = await client.request(
        "DELETE",
        f"/v1/admin/orgs/{org_id}/plan",
        json={"note": "stopped paying — Sept"},
        headers=staff,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "plan_expired"

    surviving = await db_session.scalar(
        select(func.count())
        .select_from(Agent)
        .where(Agent.organization_id == uuid.UUID(org_id), Agent.deleted_at.is_(None))
    )
    assert surviving == 3, "every agent survives a revoke — read-only, never deleted"

    org = await _org(db_session, org_id)
    assert org.plan == "business", "the plan is kept so the meter still reads 'of 30,000'"
    ent = await billing_usage.load_entitlements(db_session, org)
    assert ent.is_expired and not ent.agents_writable and not ent.bot_replies

    refused = await client.post("/v1/agents", json={"name": "Bot 4"}, headers=headers)
    assert refused.status_code == 402
    assert refused.json()["error"]["code"] == "plan_limit"


async def test_revoke_without_a_note_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "pro", payment_received=True)

    resp = await client.request(
        "DELETE", f"/v1/admin/orgs/{org_id}/plan", json={"note": ""}, headers=staff
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "note_required"
    assert (await _org(db_session, org_id)).plan_expires_at is None


async def test_revoke_says_so_rather_than_silently_doing_nothing_on_legacy(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`legacy` is unlimited by design (your own and demo workspaces) and has no term to lapse.
    A silent no-op here would look exactly like a successful revoke."""
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "legacy")

    resp = await client.request(
        "DELETE", f"/v1/admin/orgs/{org_id}/plan", json={"note": "please stop"}, headers=staff
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "nothing_to_revoke"


# ── the roster and its filters ──────────────────────────────────────────────────
async def test_the_detail_view_carries_the_billing_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "pro", payment_received=True)
    await client.post(
        f"/v1/admin/orgs/{org_id}/messages", json={"packs": 1, "note": "INV-005"}, headers=staff
    )

    body = (await client.get(f"/v1/admin/orgs/{org_id}", headers=staff)).json()
    assert body["plan"] == "pro"
    assert {g["action"] for g in body["recent_grants"]} == {"granted", "pack_added"}
    assert len(body["billing_cycles"]) == 1
    assert body["owner_email"]


async def test_the_roster_filters_find_the_workspaces_that_need_attention(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    await grant(client, staff, org_id, "pro", payment_received=False)

    pending = (await client.get("/v1/admin/orgs?status=payment_pending", headers=staff)).json()
    assert org_id in [o["id"] for o in pending]

    by_plan = (await client.get("/v1/admin/orgs?plan=pro", headers=staff)).json()
    assert [o["id"] for o in by_plan] == [org_id]

    owner = next(o for o in pending if o["id"] == org_id)["owner_email"]
    found = (await client.get(f"/v1/admin/orgs?q={owner}", headers=staff)).json()
    assert [o["id"] for o in found] == [org_id]


async def test_an_unknown_status_filter_is_an_error_not_an_empty_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An empty roster and a typo'd filter must not look the same."""
    staff = await staff_headers(client, db_session)
    resp = await client.get("/v1/admin/orgs?status=nonsense", headers=staff)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_status_filter"
