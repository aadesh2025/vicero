"""GET /v1/me/entitlements (docs/22 §8.1, Phase A3) — the customer-facing "what can I do" endpoint.

Deliberately not the admin console: no payment state, no billing cycles, nothing staff-only.
What matters most here is `effective_max_messages` — the exact bug class `app/chat/inbound.py`
was fixed for (commit 4aabae0): a customer who bought a pack must see it reflected, not the raw
plan cap.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.usage import add_extra_messages
from app.core.plans import PLANS
from app.main import create_app
from app.models import Organization
from tests.selfserve_helpers import bearer, signup, trial_org

pytestmark = pytest.mark.usefixtures("self_serve")


async def _set_plan(
    db: AsyncSession, org_id: str, plan: str, *, expires_at: dt.datetime | None = None
) -> Organization:
    org = await db.get(Organization, uuid.UUID(org_id))
    assert org is not None
    org.plan = plan
    org.plan_expires_at = expires_at
    await db.flush()
    return org


# ── the four org states ───────────────────────────────────────────────────────
async def test_trial_org(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, _org_id = await trial_org(client)

    resp = await client.get("/v1/me/entitlements", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["plan"] == "trial"
    assert body["status"] == "trial"
    assert body["plan_expires_at"] is None  # trials expire via trial_ends_at, not this field
    assert body["days_left"] is not None and body["days_left"] <= 10
    assert body["limits"]["messages"] == PLANS["trial"].max_messages == 500
    assert body["messages"]["used"] == 0
    assert body["messages"]["effective_limit"] == 500
    assert body["usage"]["agents"] == 0
    assert body["channels"] == ["web"]


async def test_active_paid_org(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, org_id = await trial_org(client)
    expires = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=20)
    await _set_plan(db_session, org_id, "pro", expires_at=expires)

    resp = await client.get("/v1/me/entitlements", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["plan"] == "pro"
    assert body["status"] == "pro"
    assert body["plan_expires_at"] is not None
    assert 19 <= body["days_left"] <= 20
    assert body["limits"]["messages"] == PLANS["pro"].max_messages == 10_000
    assert body["messages"]["plan_limit"] == 10_000
    assert body["messages"]["effective_limit"] == 10_000
    assert body["features"]["n8n"] is True
    assert sorted(body["channels"]) == ["facebook", "instagram", "web", "whatsapp"]


async def test_plan_expired_org_is_read_only_with_a_zero_enforced_cap(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A lapsed grant: `spec` becomes the zeroed `_EXPIRED` spec (the real enforced state), but
    the meter still shows the original plan's cap rather than a confusing "x / 0" (docs/22 §16.1:
    read-only, nothing deleted)."""
    _, headers, org_id = await trial_org(client)
    expired = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)
    await _set_plan(db_session, org_id, "pro", expires_at=expired)

    resp = await client.get("/v1/me/entitlements", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["plan"] == "pro"
    assert body["status"] == "plan_expired"
    assert body["messages"]["effective_limit"] == 0  # nothing more can be sent
    assert body["messages"]["plan_limit"] == 10_000  # but the meter still reads "x / 10,000"
    assert body["limits"]["agents"] == 1  # the read-only allowance, not Pro's 10


async def test_legacy_org_is_unlimited(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "legacy")

    resp = await client.get("/v1/me/entitlements", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["plan"] == "legacy"
    assert body["status"] == "legacy"
    assert body["days_left"] is None
    assert body["limits"]["messages"] is None
    assert body["messages"]["plan_limit"] is None
    assert body["messages"]["effective_limit"] is None
    assert body["channels"] is None  # every channel


# ── the bug class this endpoint exists to not repeat (commit 4aabae0) ────────
async def test_a_purchased_pack_raises_effective_max_messages_not_just_the_dashboard(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The exact case that would have caught the `inbound.py` bug: a pack must show up as a
    higher `effective_limit`, never the raw plan cap."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "starter", expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=30))
    await add_extra_messages(db_session, uuid.UUID(org_id), 500)

    resp = await client.get("/v1/me/entitlements", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["limits"]["messages"] == PLANS["starter"].max_messages == 2_000  # the raw plan cap
    assert body["messages"]["extra"] == 500
    # On an active (non-expired) plan, `meter_limit` already includes packs — it only diverges
    # from `effective_limit` post-expiry, when the meter keeps showing the pre-expiry cap.
    assert body["messages"]["plan_limit"] == 2_500
    assert body["messages"]["effective_limit"] == 2_500  # cap + pack — never just 2_000


# ── payment status is staff-only, never here (docs/22 §5.1 rule 1) ──────────
async def test_response_never_contains_payment_state_or_billing_cycles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=10))

    resp = await client.get("/v1/me/entitlements", headers=headers)
    assert resp.status_code == 200, resp.text
    raw = resp.text.lower()
    assert "payment_state" not in raw
    assert "billing_cycle" not in raw
    assert "payment_pending" not in raw
    assert "overdue" not in raw


# ── auth ───────────────────────────────────────────────────────────────────────
async def test_unauthenticated_is_401() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.get("/v1/me/entitlements")
    assert resp.status_code == 401, resp.text


async def test_a_member_of_no_org_is_missing_the_required_header(client: AsyncClient) -> None:
    """Authenticated, but no `X-Org-Id` at all — `current_org` (the same dependency every
    org-scoped route already uses) rejects this before any entitlements logic runs."""
    auth = await signup(client)
    resp = await client.get("/v1/me/entitlements", headers=bearer(auth))
    assert resp.status_code == 400, resp.text


async def test_a_user_with_no_membership_in_the_given_org_is_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, _, real_org_id = await trial_org(client)
    outsider = await signup(client)
    headers = {**bearer(outsider), "X-Org-Id": real_org_id}
    resp = await client.get("/v1/me/entitlements", headers=headers)
    assert resp.status_code == 403, resp.text
