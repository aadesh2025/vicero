"""Multi-currency price lists and display-only geo detection (docs/22 §3, ADR-106)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import geo
from app.core.config import settings
from app.core.plans import (
    PAID_PLANS,
    PLANS,
    SUPPORTED_CURRENCIES,
    pack_price_for,
    price_for,
)
from app.models import BillingCycle, Organization, PlanGrant
from app.modules.admin import service as admin_service
from tests.selfserve_helpers import trial_org
from tests.test_admin_billing import grant, staff_headers

pytestmark = pytest.mark.usefixtures("self_serve")

# The agreed table: REAL prices (what a customer sees). Stored value = real x 100.
REAL_PLAN = {
    "starter": {"USD": 49, "EUR": 45, "INR": 1499},
    "pro": {"USD": 99, "EUR": 89, "INR": 3499},
    "business": {"USD": 199, "EUR": 179, "INR": 6999},
}
STORED_PLAN = {
    "starter": {"USD": 4900, "EUR": 4500, "INR": 149900},
    "pro": {"USD": 9900, "EUR": 8900, "INR": 349900},
    "business": {"USD": 19900, "EUR": 17900, "INR": 699900},
}
REAL_PACK = {
    "starter": {"USD": 6, "EUR": 5, "INR": 199},
    "pro": {"USD": 5, "EUR": 4.5, "INR": 149},
    "business": {"USD": 4, "EUR": 3.5, "INR": 99},
}
STORED_PACK = {
    "starter": {"USD": 600, "EUR": 500, "INR": 19900},
    "pro": {"USD": 500, "EUR": 450, "INR": 14900},
    "business": {"USD": 400, "EUR": 350, "INR": 9900},
}


# ── the table ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("plan", PAID_PLANS)
@pytest.mark.parametrize("currency", SUPPORTED_CURRENCIES)
def test_table_drift_and_x100_sanity(plan: str, currency: str) -> None:
    """Stored numbers match the agreed table exactly, and stored / 100 equals the real price —
    which catches a x100 mistake immediately."""
    assert price_for(plan, currency) == STORED_PLAN[plan][currency]
    assert pack_price_for(plan, currency) == STORED_PACK[plan][currency]
    assert price_for(plan, currency) / 100 == REAL_PLAN[plan][currency]
    assert pack_price_for(plan, currency) / 100 == REAL_PACK[plan][currency]
    assert isinstance(price_for(plan, currency), int)
    assert isinstance(pack_price_for(plan, currency), int)


def test_every_paid_plan_has_every_currency_and_unknown_raises() -> None:
    for plan in PAID_PLANS:
        spec = PLANS[plan]
        assert spec.price_minor is not None and spec.extra_message_pack_price_minor is not None
        assert set(spec.price_minor) == set(SUPPORTED_CURRENCIES)
        assert set(spec.extra_message_pack_price_minor) == set(SUPPORTED_CURRENCIES)
        with pytest.raises(ValueError):
            price_for(plan, "GBP")
        with pytest.raises(ValueError):
            pack_price_for(plan, "gbp")
    with pytest.raises(ValueError):
        price_for("legacy", "USD")


def test_derived_usd_fields_are_unchanged() -> None:
    assert [PLANS[p].price_usd_month for p in PAID_PLANS] == [49, 99, 199]
    assert [PLANS[p].extra_message_pack_usd for p in PAID_PLANS] == [6, 5, 4]


# ── geo (pure) ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("country", "expected"),
    [
        ("IN", "INR"),
        ("in", "INR"),
        ("DE", "EUR"),
        ("FR", "EUR"),
        ("NL", "EUR"),
        ("de", "EUR"),
        ("US", "USD"),
        ("GB", "USD"),
        ("BR", "USD"),
        (None, "USD"),
        ("", "USD"),
        ("XX", "USD"),
        ("T1", "USD"),
        ("garbage!!", "USD"),
    ],
)
def test_currency_for_country(country: str | None, expected: str) -> None:
    assert geo.currency_for_country(country) == expected


def test_eu27_has_27_members() -> None:
    assert len(geo.EU27) == 27


def _req(**headers: str) -> SimpleNamespace:
    return SimpleNamespace(headers=headers)


def test_precedence_query_beats_header_beats_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_geo_headers", True)
    assert geo.resolve_currency(_req(**{"CF-IPCountry": "IN"}), "EUR") == ("EUR", "query")
    assert geo.resolve_currency(_req(**{"CF-IPCountry": "IN"}), None) == ("INR", "geo")
    assert geo.resolve_currency(_req(**{"X-Vercel-IP-Country": "de"}), None) == ("EUR", "geo")
    assert geo.resolve_currency(_req(), None) == ("USD", "default")
    # Cloudflare unknown / Tor carry no information.
    assert geo.resolve_currency(_req(**{"CF-IPCountry": "XX"}), None) == ("USD", "default")
    assert geo.resolve_currency(_req(**{"CF-IPCountry": "T1"}), None) == ("USD", "default")


def test_header_ignored_unless_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_geo_headers", False)
    assert geo.resolve_currency(_req(**{"CF-IPCountry": "IN"}), None) == ("USD", "default")
    # An explicit query still works with headers untrusted.
    assert geo.resolve_currency(_req(**{"CF-IPCountry": "IN"}), "inr") == ("INR", "query")


# ── GET /v1/billing/plans ─────────────────────────────────────────────────────────
async def test_endpoint_default_and_legacy_fields(client: AsyncClient) -> None:
    body = (await client.get("/v1/billing/plans")).json()
    assert body["currency"] == "USD" and body["currency_source"] == "default"
    assert body["available_currencies"] == ["USD", "EUR", "INR"]
    assert [p["price_usd_month"] for p in body["plans"]] == [49, 99, 199]
    assert [p["extra_message_pack_usd"] for p in body["plans"]] == [6, 5, 4]
    assert [p["price_minor"] for p in body["plans"]] == [4900, 9900, 19900]


@pytest.mark.parametrize("currency", SUPPORTED_CURRENCIES)
async def test_endpoint_prices_in_requested_currency(client: AsyncClient, currency: str) -> None:
    body = (await client.get("/v1/billing/plans", params={"currency": currency})).json()
    assert body["currency"] == currency and body["currency_source"] == "query"
    for plan in body["plans"]:
        assert plan["price_minor"] == STORED_PLAN[plan["id"]][currency]
        assert plan["extra_message_pack_price_minor"] == STORED_PACK[plan["id"]][currency]
        # legacy fields stay USD whatever the currency.
        assert plan["price_usd_month"] == REAL_PLAN[plan["id"]]["USD"]
    assert body["tax_note"] == {"USD": "excl. taxes", "EUR": "excl. VAT", "INR": "excl. GST"}[currency]


async def test_endpoint_limits_do_not_vary_by_currency(client: AsyncClient) -> None:
    usd = (await client.get("/v1/billing/plans", params={"currency": "USD"})).json()["plans"]
    inr = (await client.get("/v1/billing/plans", params={"currency": "INR"})).json()["plans"]
    for a, b in zip(usd, inr, strict=True):
        assert a["limits"] == b["limits"] and a["features"] == b["features"]


async def test_endpoint_geo_header_trusted(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_geo_headers", True)
    r = await client.get("/v1/billing/plans", headers={"CF-IPCountry": "IN"})
    assert (r.json()["currency"], r.json()["currency_source"]) == ("INR", "geo")
    r = await client.get("/v1/billing/plans", headers={"CF-IPCountry": "DE"})
    assert r.json()["currency"] == "EUR"
    r = await client.get("/v1/billing/plans", headers={"CF-IPCountry": "US"})
    assert r.json()["currency"] == "USD"
    # ?currency wins over the header.
    r = await client.get("/v1/billing/plans", headers={"CF-IPCountry": "IN"}, params={"currency": "USD"})
    assert (r.json()["currency"], r.json()["currency_source"]) == ("USD", "query")


async def test_endpoint_geo_header_ignored_when_untrusted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "trust_geo_headers", False)
    r = await client.get("/v1/billing/plans", headers={"CF-IPCountry": "IN"})
    assert (r.json()["currency"], r.json()["currency_source"]) == ("USD", "default")


async def test_endpoint_unsupported_currency_falls_back_and_never_500s(client: AsyncClient) -> None:
    """Decision (ADR-106): an unsupported `?currency=` is ignored - it falls through to the
    header/USD rules - rather than a 400, so a stale bookmark still renders a price list."""
    for bad in ("GBP", "", "x" * 500, "%00"):
        r = await client.get("/v1/billing/plans", params={"currency": bad})
        assert r.status_code == 200, bad
        assert r.json()["currency"] == "USD" and r.json()["currency_source"] == "default"


async def test_endpoint_is_not_shareable_by_a_cache(client: AsyncClient) -> None:
    r = await client.get("/v1/billing/plans")
    assert "private" in r.headers["cache-control"]
    vary = r.headers["vary"].lower()
    for header in geo.geo_headers():
        assert header.lower() in vary


# ── admin: grant / packs / mark-paid in a currency ─────────────────────────────────
async def test_grant_in_inr_records_currency_and_minor_amount(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)

    # An amount in the body is ignored - the server prices from the plan table.
    res = await grant(client, staff, org_id, "pro", currency="INR", amount_minor=1, amount_usd_cents=1)
    assert res["status"] == 200, res["body"]

    cycle = (
        await db_session.execute(
            select(BillingCycle).where(BillingCycle.organization_id == uuid.UUID(org_id))
        )
    ).scalar_one()
    assert (cycle.currency, cycle.amount_minor) == ("INR", 349900)
    assert cycle.amount_usd_cents is None  # legacy mirror is USD-only

    grants = (
        await db_session.execute(
            select(PlanGrant).where(PlanGrant.organization_id == uuid.UUID(org_id), PlanGrant.action == "granted")
        )
    ).scalars().all()
    assert [(g.currency, g.amount_minor, g.amount_usd_cents) for g in grants] == [("INR", 349900, None)]

    detail = (await client.get(f"/v1/admin/orgs/{org_id}", headers=staff)).json()
    assert detail["billing_cycles"][0]["currency"] == "INR"
    assert detail["billing_cycles"][0]["amount_minor"] == 349900


async def test_grant_usd_default_unchanged(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    assert (await grant(client, staff, org_id, "pro"))["status"] == 200
    cycle = (
        await db_session.execute(
            select(BillingCycle).where(BillingCycle.organization_id == uuid.UUID(org_id))
        )
    ).scalar_one()
    assert (cycle.currency, cycle.amount_minor, cycle.amount_usd_cents) == ("USD", 9900, 9900)


async def test_grant_with_unknown_currency_is_rejected_and_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    res = await grant(client, staff, org_id, "pro", currency="GBP")
    assert res["status"] == 400 and res["body"]["error"]["code"] == "invalid_currency"
    count = (
        await db_session.execute(
            select(BillingCycle).where(BillingCycle.organization_id == uuid.UUID(org_id))
        )
    ).scalars().all()
    assert count == []


async def test_add_packs_in_eur_prices_from_plan_table(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    assert (await grant(client, staff, org_id, "pro"))["status"] == 200

    resp = await client.post(
        f"/v1/admin/orgs/{org_id}/messages",
        json={"packs": 2, "note": "INV-9", "currency": "EUR", "amount_minor": 1},
        headers=staff,
    )
    assert resp.status_code == 200, resp.text
    pack = (
        await db_session.execute(
            select(PlanGrant).where(PlanGrant.organization_id == uuid.UUID(org_id), PlanGrant.action == "pack_added")
        )
    ).scalar_one()
    assert (pack.currency, pack.amount_minor, pack.amount_usd_cents) == ("EUR", 900, None)  # 2 x EUR 4.50

    packs = (await client.get("/v1/admin/billing/packs", headers=staff)).json()
    mine = [p for p in packs if p["organization_id"] == org_id]
    assert mine[0]["currency"] == "EUR" and mine[0]["amount_minor"] == 900


async def test_mark_paid_can_reprice_a_pending_cycle_in_another_currency(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _auth, _headers, org_id = await trial_org(client)
    assert (await grant(client, staff, org_id, "starter"))["status"] == 200  # pending, USD
    cycle = (
        await db_session.execute(
            select(BillingCycle).where(BillingCycle.organization_id == uuid.UUID(org_id))
        )
    ).scalar_one()

    resp = await client.post(
        f"/v1/admin/billing/cycles/{cycle.id}/paid",
        json={"method": "upi", "currency": "INR", "amount_minor": 1},
        headers=staff,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["currency"], body["amount_minor"], body["amount_usd_cents"]) == ("INR", 149900, None)

    # A settled cycle's currency is frozen.
    again = await client.post(
        f"/v1/admin/billing/cycles/{cycle.id}/paid",
        json={"method": "upi", "currency": "EUR"},
        headers=staff,
    )
    assert again.status_code == 409


# ── the money rule: never sum across currencies ────────────────────────────────────
def _cycle(currency: str, minor: int, status: str = "paid", overdue: bool = False) -> BillingCycle:
    now = dt.datetime.now(tz=dt.UTC)
    end = now - dt.timedelta(days=1) if overdue else now + dt.timedelta(days=10)
    return BillingCycle(
        organization_id=uuid.uuid4(),
        plan="pro",
        period_start=now - dt.timedelta(days=30),
        period_end=end,
        currency=currency,
        amount_minor=minor,
        status=status,
    )


def test_totals_are_per_currency_and_never_combined() -> None:
    now = dt.datetime.now(tz=dt.UTC)
    rows = admin_service.totals_by_currency(
        [
            _cycle("USD", 9900),
            _cycle("USD", 4900),
            _cycle("INR", 349900),
            _cycle("INR", 149900, status="pending"),
            _cycle("INR", 149900, status="pending", overdue=True),
            _cycle("EUR", 8900, status="waived"),
        ],
        now,
    )
    by = {r.currency: r for r in rows}
    assert set(by) == {"USD", "INR", "EUR"}
    assert by["USD"].collected_minor == 14800
    assert by["INR"].collected_minor == 349900
    assert by["INR"].pending_minor == 149900
    assert (by["INR"].overdue_minor, by["INR"].overdue_count) == (149900, 1)
    assert by["EUR"].collected_minor == 0  # waived is not collected
    # No bucket holds a cross-currency sum.
    assert all(r.collected_minor != 14800 + 349900 for r in rows)


async def test_totals_endpoint_returns_one_row_per_currency(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await staff_headers(client, db_session)
    _a, _h, usd_org = await trial_org(client)
    _a, _h, inr_org = await trial_org(client)
    assert (await grant(client, staff, usd_org, "pro", payment_received=True, method="bank"))["status"] == 200
    assert (
        await grant(client, staff, inr_org, "pro", currency="INR", payment_received=True, method="upi")
    )["status"] == 200

    rows = (await client.get("/v1/admin/billing/totals", headers=staff)).json()
    by = {r["currency"]: r for r in rows}
    assert by["USD"]["collected_minor"] >= 9900 and by["INR"]["collected_minor"] >= 349900
    # The INR amount is not hiding inside the USD figure.
    assert by["USD"]["collected_minor"] < 349900


# ── migration ───────────────────────────────────────────────────────────────────────
async def test_ledger_columns_and_check_constraint(db_session: AsyncSession) -> None:
    cols = (
        await db_session.execute(
            text(
                "select table_name, column_name from information_schema.columns "
                "where table_name in ('billing_cycles','plan_grants') "
                "and column_name in ('currency','amount_minor')"
            )
        )
    ).all()
    assert len(cols) == 4
    org_id = uuid.uuid4()
    db_session.add(Organization(id=org_id, name="CK", slug=f"ck-{org_id.hex[:10]}", plan="pro"))
    await db_session.flush()
    db_session.add(_cycle("GBP", 1))
    with pytest.raises(Exception, match="ck_billing_cycles_currency"):
        await db_session.flush()
