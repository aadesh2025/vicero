"""The public pricing endpoint (docs/22 §8.1). Pure — no database, no app fixtures.

The point of these tests is drift: `docs/22 §3`, `app/core/plans.py` and what the pricing page
renders must always be the same three numbers. If one moves, this file fails.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.plans import FEATURES, PAID_PLANS, PLANS
from app.modules.billing import service
from app.modules.billing.router import router


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Just this router — the endpoint has no database or auth dependency to satisfy."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _plan(payload: dict[str, Any], plan_id: str) -> dict[str, Any]:
    return next(p for p in payload["plans"] if p["id"] == plan_id)


# ── the wire format ──────────────────────────────────────────────────────────────
def test_the_endpoint_returns_every_sold_plan_cheapest_first(client: TestClient) -> None:
    body = client.get("/v1/billing/plans").json()
    assert [p["id"] for p in body["plans"]] == list(PAID_PLANS)
    assert [p["price_usd_month"] for p in body["plans"]] == [49, 99, 199]
    assert body["currency"] == "USD"


def test_it_is_contact_only_while_plans_are_granted_by_staff(client: TestClient) -> None:
    """Flips when self-serve payment ships (docs/23). Until then the page says "Contact us"."""
    assert client.get("/v1/billing/plans").json()["contact_only"] is True


def test_the_endpoint_needs_no_authentication(client: TestClient) -> None:
    """A price list behind a login cannot be a pricing page."""
    assert client.get("/v1/billing/plans").status_code == 200


def test_display_names_are_derived_not_typed(client: TestClient) -> None:
    body = client.get("/v1/billing/plans").json()
    assert [p["name"] for p in body["plans"]] == ["Starter", "Pro", "Business"]


# ── the numbers, against docs/22 §3 ──────────────────────────────────────────────
def test_starter_row(client: TestClient) -> None:
    p = _plan(client.get("/v1/billing/plans").json(), "starter")
    assert p["limits"] == {
        "workspaces": 1,
        "agents": 3,
        "messages": 2_000,
        "knowledge_bases": 1,
        "documents": 20,
        "storage_bytes": 500 * 1024 * 1024,
        "workflows": 0,
        "tools": 0,
        "webhooks": 0,
        "team_members": 1,
    }
    assert p["channels"] == ["web"]
    assert p["extra_message_pack_usd"] == 6
    assert p["support"] == "email"


def test_pro_row(client: TestClient) -> None:
    p = _plan(client.get("/v1/billing/plans").json(), "pro")
    assert p["limits"]["agents"] == 10
    assert p["limits"]["messages"] == 10_000
    assert p["limits"]["workflows"] == 10
    assert p["limits"]["tools"] == 8
    assert p["limits"]["webhooks"] == 5
    assert p["channels"] == ["facebook", "instagram", "web", "whatsapp"]
    assert p["extra_message_pack_usd"] == 5


def test_business_row(client: TestClient) -> None:
    p = _plan(client.get("/v1/billing/plans").json(), "business")
    assert p["limits"]["agents"] == 30
    assert p["limits"]["messages"] == 30_000
    assert p["limits"]["storage_bytes"] == 10 * 1024 * 1024 * 1024
    assert p["channels"] is None, "null means every supported channel"
    assert p["extra_message_pack_usd"] == 4


def test_unlimited_is_null_and_not_included_is_zero(client: TestClient) -> None:
    """The convention the whole plan table uses, preserved onto the wire."""
    body = client.get("/v1/billing/plans").json()
    assert _plan(body, "business")["limits"]["workflows"] is None  # unlimited
    assert _plan(body, "starter")["limits"]["workflows"] == 0  # not included


def test_packs_are_five_hundred_messages_on_every_plan(client: TestClient) -> None:
    for p in client.get("/v1/billing/plans").json()["plans"]:
        assert p["extra_message_pack_size"] == 500


# ── drift guards ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("plan_id", PAID_PLANS)
def test_every_limit_matches_the_plan_table_exactly(plan_id: str) -> None:
    """If a number changes in `plans.py`, it changes here — no second copy to forget."""
    spec = PLANS[plan_id]
    limits = service.plan_out(plan_id).limits
    assert limits.agents == spec.max_agents
    assert limits.messages == spec.max_messages
    assert limits.knowledge_bases == spec.max_knowledge_bases
    assert limits.documents == spec.max_documents
    assert limits.storage_bytes == spec.storage_bytes
    assert limits.workflows == spec.max_workflows
    assert limits.tools == spec.max_tools
    assert limits.webhooks == spec.max_webhooks
    assert limits.team_members == spec.max_team_members
    assert limits.workspaces == spec.max_workspaces


@pytest.mark.parametrize("plan_id", PAID_PLANS)
def test_every_feature_flag_is_published(plan_id: str) -> None:
    """A capability added to `FEATURES` must appear on the pricing page, not go missing."""
    published = service.plan_out(plan_id).features.model_dump()
    assert set(published) == set(FEATURES)
    for name in FEATURES:
        assert published[name] == bool(getattr(PLANS[plan_id], name)), name


def test_starter_publishes_no_automation() -> None:
    f = service.plan_out("starter").features
    assert not (f.workflows or f.n8n or f.tool_calling or f.webhooks)
    assert not f.api_write and not f.remove_branding


def test_unsold_plans_are_never_published(client: TestClient) -> None:
    ids = {p["id"] for p in client.get("/v1/billing/plans").json()["plans"]}
    assert "trial" not in ids and "legacy" not in ids


def test_rendering_an_unsold_plan_is_a_loud_failure() -> None:
    """`legacy` has no price. Publishing it as `$0` would be worse than crashing."""
    with pytest.raises(AssertionError):
        service.plan_out("legacy")
