"""The payment ledger — one row per 30-day billing cycle per workspace (docs/22 §5.1, ADR-102).

**Payment status never gates access.** Access is decided by `Organization.plan_expires_at`
alone (`app/core/plans.py`) — a `pending` cycle is a note to the operator, not a punishment for
the client. If a caller reads `BillingCycle`/`payment_state()` to decide whether an org may do
something, that is wrong: revoke the plan (or let `plan_expires_at` lapse) instead.

Every state change here also writes a `plan_grants` row and calls `write_audit(...)` — two
records on purpose: `plan_grants` is the billing view, `audit_logs` the security view.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import AppError
from app.core.plans import DEFAULT_CURRENCY, price_for
from app.models import BillingCycle, Organization, PlanGrant


def usd_mirror(currency: str, amount_minor: int | None) -> int | None:
    """What goes in the legacy `amount_usd_cents` column: the amount for USD rows, `None` for
    every other currency, so an old reader can never mistake paise for cents."""
    return amount_minor if currency == "USD" else None


async def open_cycle(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    plan: str,
    days: int = 30,
    paid: bool = False,
    currency: str = DEFAULT_CURRENCY,
    method: str | None = None,
    reference: str | None = None,
    note: str | None = None,
    marked_by: uuid.UUID | None = None,
) -> BillingCycle:
    """Open the next billing cycle for `org_id`. `paid=True` is the admin panel's "Payment
    already received" checkbox — the normal case (client pays, then you grant) opens the cycle
    already settled instead of `pending` followed by an immediate separate `mark_paid`.

    The amount is read from the plan's price list in `currency` **now** (minor units) and copied
    onto the row, so a later price change never rewrites what this cycle actually cost. The
    currency is the operator's explicit choice — never inferred from a visitor's country.
    """
    now = dt.datetime.now(tz=dt.UTC)
    amount = price_for(plan, currency)  # raises on an unknown currency; never falls back
    cycle = BillingCycle(
        organization_id=org_id,
        plan=plan,
        period_start=now,
        period_end=now + dt.timedelta(days=days),
        currency=currency,
        amount_minor=amount,
        amount_usd_cents=usd_mirror(currency, amount),
        status="paid" if paid else "pending",
        paid_at=now if paid else None,
        method=method if paid else None,
        reference=reference if paid else None,
        note=note,
        marked_by=marked_by if paid else None,
    )
    session.add(cycle)
    session.add(
        PlanGrant(
            organization_id=org_id,
            action="granted",
            to_plan=plan,
            expires_at=cycle.period_end,
            currency=currency,
            amount_minor=amount,
            amount_usd_cents=usd_mirror(currency, amount),
            note=note,
            actor_id=marked_by,
        )
    )
    await write_audit(
        session, org_id, marked_by, "billing.cycle_opened", meta={"plan": plan, "paid": paid, "currency": currency}
    )
    await session.commit()
    await session.refresh(cycle)
    return cycle


async def mark_paid(
    session: AsyncSession,
    cycle_id: uuid.UUID,
    *,
    method: str,
    reference: str | None = None,
    note: str | None = None,
    marked_by: uuid.UUID,
    renew: bool = False,
    currency: str | None = None,
) -> BillingCycle:
    """Record that `cycle_id` was paid. `renew=True` (the "Mark paid & renew" monthly-renewal
    button) also extends `Organization.plan_expires_at` by 30 days and opens the next cycle as
    `pending`, in this same transaction.

    `currency` is for the client who was invoiced in one currency and paid in another: a still
    `pending` cycle is re-priced from the plan table in that currency (the amount is never taken
    from the caller). A cycle that is already settled keeps the amount it was settled at.
    """
    now = dt.datetime.now(tz=dt.UTC)
    cycle = await session.get(BillingCycle, cycle_id)
    if cycle is None:
        raise ValueError(f"billing cycle {cycle_id} not found")

    if currency is not None and currency != cycle.currency:
        if cycle.status != "pending":
            raise AppError("cycle_settled", "A settled cycle's currency cannot be changed.", 409)
        cycle.amount_minor = price_for(cycle.plan, currency)
        cycle.currency = currency
        cycle.amount_usd_cents = usd_mirror(currency, cycle.amount_minor)

    cycle.status = "paid"
    cycle.paid_at = now
    cycle.method = method
    cycle.reference = reference
    if note is not None:
        cycle.note = note
    cycle.marked_by = marked_by
    session.add(
        PlanGrant(
            organization_id=cycle.organization_id,
            action="payment_marked",
            to_plan=cycle.plan,
            currency=cycle.currency,
            amount_minor=cycle.amount_minor,
            amount_usd_cents=cycle.amount_usd_cents,
            note=note,
            actor_id=marked_by,
        )
    )
    await write_audit(
        session,
        cycle.organization_id,
        marked_by,
        "billing.payment_marked",
        meta={"cycle_id": str(cycle_id), "method": method},
    )

    if renew:
        org = await session.get(Organization, cycle.organization_id)
        if org is None:
            raise ValueError(f"organization {cycle.organization_id} not found")
        new_expiry = (org.plan_expires_at or now) + dt.timedelta(days=30)
        org.plan_expires_at = new_expiry
        next_cycle = BillingCycle(
            organization_id=cycle.organization_id,
            plan=cycle.plan,
            period_start=cycle.period_end,
            period_end=new_expiry,
            currency=cycle.currency,
            amount_minor=cycle.amount_minor,
            amount_usd_cents=cycle.amount_usd_cents,
            status="pending",
        )
        session.add(next_cycle)
        session.add(
            PlanGrant(
                organization_id=cycle.organization_id,
                action="extended",
                to_plan=cycle.plan,
                expires_at=new_expiry,
                actor_id=marked_by,
            )
        )
        await write_audit(
            session,
            cycle.organization_id,
            marked_by,
            "billing.plan_extended",
            meta={"expires_at": new_expiry.isoformat()},
        )

    await session.commit()
    await session.refresh(cycle)
    return cycle


async def waive(
    session: AsyncSession, cycle_id: uuid.UUID, *, note: str, marked_by: uuid.UUID
) -> BillingCycle:
    """Comps, demos, and the operator's own workspaces — so a free org doesn't sit in the
    overdue list forever. Reuses the `payment_marked` grant action: the grant record is about
    this cycle's payment status changing, not a new value in `plan_grants.action`'s enum."""
    cycle = await session.get(BillingCycle, cycle_id)
    if cycle is None:
        raise ValueError(f"billing cycle {cycle_id} not found")
    cycle.status = "waived"
    cycle.note = note
    cycle.marked_by = marked_by
    session.add(
        PlanGrant(
            organization_id=cycle.organization_id,
            action="payment_marked",
            to_plan=cycle.plan,
            note=note,
            actor_id=marked_by,
        )
    )
    await write_audit(
        session,
        cycle.organization_id,
        marked_by,
        "billing.cycle_waived",
        meta={"cycle_id": str(cycle_id)},
    )
    await session.commit()
    await session.refresh(cycle)
    return cycle


async def current_cycle(session: AsyncSession, org_id: uuid.UUID) -> BillingCycle | None:
    """The most recently opened cycle for `org_id`, or `None` if it has never had one (trial,
    legacy, or a paid plan never billed through this path)."""
    return (
        await session.execute(
            select(BillingCycle)
            .where(BillingCycle.organization_id == org_id)
            .order_by(BillingCycle.period_start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def payment_state(cycle: BillingCycle, now: dt.datetime) -> Literal["paid", "pending", "overdue", "waived"]:
    """Computed, never a stored flag: `overdue` is a function of the clock, not an event."""
    if cycle.status in ("paid", "waived"):
        return cycle.status  # type: ignore[return-value]
    return "overdue" if now >= cycle.period_end else "pending"
