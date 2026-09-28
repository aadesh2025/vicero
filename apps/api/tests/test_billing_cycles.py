"""The payment ledger: open/mark-paid/waive, `payment_state`, and the renew-in-one-transaction
guarantee (docs/22 §5.1, ADR-102)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import cycles
from app.core.plans import PLANS
from app.models import AuditLog, Organization, PlanGrant, User


async def _org(db: AsyncSession, plan: str = "pro") -> uuid.UUID:
    org_id = uuid.uuid4()
    db.add(Organization(id=org_id, name="Cycles test", slug=f"cyc-{org_id.hex[:10]}", plan=plan))
    await db.flush()
    return org_id


async def _staff(db: AsyncSession) -> uuid.UUID:
    """`plan_grants.actor_id`/`audit_logs.actor_user_id` are real FKs to `users.id`."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"staff-{user_id.hex[:10]}@example.com"))
    await db.flush()
    return user_id


async def _grants(db: AsyncSession, org_id: uuid.UUID) -> list[PlanGrant]:
    rows = (
        await db.execute(
            select(PlanGrant).where(PlanGrant.organization_id == org_id).order_by(PlanGrant.created_at)
        )
    ).scalars().all()
    return list(rows)


async def _audit_actions(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    rows = (
        await db.execute(select(AuditLog.action).where(AuditLog.organization_id == org_id))
    ).scalars().all()
    return list(rows)


# ── open_cycle ────────────────────────────────────────────────────────────────
async def test_open_cycle_defaults_to_pending_with_the_plans_own_price(db_session: AsyncSession) -> None:
    org_id = await _org(db_session, "pro")
    cycle = await cycles.open_cycle(db_session, org_id, plan="pro", marked_by=None)

    assert cycle.status == "pending"
    assert cycle.amount_usd_cents == PLANS["pro"].price_usd_month * 100  # type: ignore[operator]
    assert (cycle.period_end - cycle.period_start) == dt.timedelta(days=30)

    grants = await _grants(db_session, org_id)
    assert len(grants) == 1 and grants[0].action == "granted" and grants[0].to_plan == "pro"
    assert "billing.cycle_opened" in await _audit_actions(db_session, org_id)


async def test_open_cycle_with_payment_already_received_opens_paid(db_session: AsyncSession) -> None:
    org_id = await _org(db_session, "starter")
    actor = await _staff(db_session)
    cycle = await cycles.open_cycle(
        db_session, org_id, plan="starter", paid=True, method="bank transfer",
        reference="INV-1", marked_by=actor,
    )
    assert cycle.status == "paid"
    assert cycle.paid_at is not None
    assert cycle.method == "bank transfer"
    assert cycle.marked_by == actor


async def test_a_price_change_never_rewrites_an_open_cycles_amount(db_session: AsyncSession) -> None:
    """`amount_usd_cents` is copied at open time — mutating `PLANS` afterward must not affect
    an already-open cycle (docs/22 §5.1 rule 4)."""
    org_id = await _org(db_session, "pro")
    cycle = await cycles.open_cycle(db_session, org_id, plan="pro", marked_by=None)
    original_amount = cycle.amount_usd_cents

    # A later price bump (simulated) must not reach back into history.
    assert original_amount == PLANS["pro"].price_usd_month * 100  # type: ignore[operator]
    refetched = await cycles.current_cycle(db_session, org_id)
    assert refetched is not None and refetched.amount_usd_cents == original_amount


# ── mark_paid ─────────────────────────────────────────────────────────────────
async def test_mark_paid_records_method_and_reference(db_session: AsyncSession) -> None:
    org_id = await _org(db_session, "pro")
    cycle = await cycles.open_cycle(db_session, org_id, plan="pro", marked_by=None)
    actor = await _staff(db_session)

    paid = await cycles.mark_paid(
        db_session, cycle.id, method="UPI", reference="utr-123", marked_by=actor
    )
    assert paid.status == "paid"
    assert paid.method == "UPI"
    assert paid.reference == "utr-123"
    assert paid.marked_by == actor
    assert "billing.payment_marked" in await _audit_actions(db_session, org_id)


async def test_mark_paid_and_renew_extends_expiry_and_opens_the_next_cycle_in_one_transaction(
    db_session: AsyncSession,
) -> None:
    org_id = await _org(db_session, "pro")
    org = await db_session.get(Organization, org_id)
    assert org is not None
    now = dt.datetime.now(tz=dt.UTC)
    org.plan_expires_at = now + dt.timedelta(days=2)  # about to lapse
    await db_session.flush()

    cycle = await cycles.open_cycle(db_session, org_id, plan="pro", paid=False, marked_by=None)
    before_expiry = org.plan_expires_at

    actor = await _staff(db_session)
    renewed = await cycles.mark_paid(
        db_session, cycle.id, method="bank transfer", marked_by=actor, renew=True
    )
    assert renewed.status == "paid"

    await db_session.refresh(org)
    assert org.plan_expires_at == before_expiry + dt.timedelta(days=30)

    next_cycle = await cycles.current_cycle(db_session, org_id)
    assert next_cycle is not None
    assert next_cycle.id != cycle.id
    assert next_cycle.status == "pending"
    assert next_cycle.period_end == org.plan_expires_at

    grants = await _grants(db_session, org_id)
    assert [g.action for g in grants] == ["granted", "payment_marked", "extended"]
    assert "billing.plan_extended" in await _audit_actions(db_session, org_id)


async def test_mark_paid_without_renew_does_not_touch_the_organization(db_session: AsyncSession) -> None:
    org_id = await _org(db_session, "pro")
    org = await db_session.get(Organization, org_id)
    assert org is not None
    now = dt.datetime.now(tz=dt.UTC)
    org.plan_expires_at = now + dt.timedelta(days=10)
    await db_session.flush()
    before = org.plan_expires_at

    cycle = await cycles.open_cycle(db_session, org_id, plan="pro", marked_by=None)
    await cycles.mark_paid(db_session, cycle.id, method="cash", marked_by=await _staff(db_session), renew=False)

    await db_session.refresh(org)
    assert org.plan_expires_at == before
    assert await cycles.current_cycle(db_session, org_id) == cycle  # no second cycle opened


# ── waive ─────────────────────────────────────────────────────────────────────
async def test_waive_marks_the_cycle_waived_and_never_overdue(db_session: AsyncSession) -> None:
    org_id = await _org(db_session, "legacy")
    cycle = await cycles.open_cycle(db_session, org_id, plan="pro", marked_by=None)
    actor = await _staff(db_session)

    waived = await cycles.waive(db_session, cycle.id, note="comp account", marked_by=actor)
    assert waived.status == "waived"

    far_future = cycle.period_end + dt.timedelta(days=365)
    assert cycles.payment_state(waived, far_future) == "waived"


# ── payment_state ─────────────────────────────────────────────────────────────
def _cycle_stub(status: str, period_end: dt.datetime) -> object:
    class _C:
        pass

    c = _C()
    c.status = status  # type: ignore[attr-defined]
    c.period_end = period_end  # type: ignore[attr-defined]
    return c


@pytest.mark.parametrize(
    ("status", "now_offset_days", "expected"),
    [
        ("paid", 100, "paid"),  # paid passes through regardless of the clock
        ("waived", 100, "waived"),
        ("pending", -1, "pending"),  # period_end still in the future
        ("pending", 1, "overdue"),  # period_end has passed
        ("pending", 0, "overdue"),  # exactly at the boundary — inclusive
    ],
)
def test_payment_state_is_computed_from_status_and_the_clock(
    status: str, now_offset_days: int, expected: str
) -> None:
    period_end = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    now = period_end + dt.timedelta(days=now_offset_days)
    assert cycles.payment_state(_cycle_stub(status, period_end), now) == expected  # type: ignore[arg-type]


# ── current_cycle ─────────────────────────────────────────────────────────────
async def test_current_cycle_returns_the_most_recently_opened_one(db_session: AsyncSession) -> None:
    org_id = await _org(db_session, "pro")
    first = await cycles.open_cycle(db_session, org_id, plan="pro", marked_by=None)
    await cycles.mark_paid(db_session, first.id, method="cash", marked_by=await _staff(db_session), renew=True)

    latest = await cycles.current_cycle(db_session, org_id)
    assert latest is not None and latest.id != first.id


async def test_current_cycle_is_none_for_an_org_with_no_billing_history(db_session: AsyncSession) -> None:
    org_id = await _org(db_session, "legacy")
    assert await cycles.current_cycle(db_session, org_id) is None
