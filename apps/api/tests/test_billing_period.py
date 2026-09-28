"""Billing period: lazy rollover, packs, and the concurrency guarantee (docs/22 §6, §7, ADR-102).

Real committed rows on separate connections throughout — the shared-transaction `client`/
`db_session` fixture cannot exhibit the races these tests exist to catch, since every statement
there runs on one connection (same reasoning as `test_message_metering.py`'s own concurrency
tests).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.billing import usage
from app.core.plans import PLANS, get_entitlements
from app.models import Organization, OrgMessageUsage
from tests.dbconn import new_engine


class _Org:
    """A real committed org + usage row, cleaned up after the test."""

    def __init__(self, engine: AsyncEngine, plan: str = "pro") -> None:
        self.engine = engine
        self.plan = plan
        self.id = uuid.uuid4()

    async def __aenter__(self) -> _Org:
        async with AsyncSession(self.engine) as s:
            s.add(
                Organization(
                    id=self.id,
                    name="Billing period test",
                    slug=f"bp-{self.id.hex[:10]}",
                    plan=self.plan,
                    plan_expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=60),
                )
            )
            await s.commit()
        return self

    async def add_usage_row(
        self,
        *,
        messages_used: int = 0,
        extra_messages: int = 0,
        period_end: dt.datetime | None,
    ) -> None:
        async with AsyncSession(self.engine) as s:
            s.add(
                OrgMessageUsage(
                    organization_id=self.id,
                    messages_used=messages_used,
                    extra_messages=extra_messages,
                    period_end=period_end,
                )
            )
            await s.commit()

    async def row(self) -> OrgMessageUsage:
        async with AsyncSession(self.engine) as s:
            row = await s.get(OrgMessageUsage, self.id)
            assert row is not None
            return row

    async def __aexit__(self, *exc: object) -> None:
        async with AsyncSession(self.engine) as s:
            org = await s.get(Organization, self.id)
            if org is not None:
                await s.delete(org)
                await s.commit()


def _limit(plan: str) -> int:
    spec = PLANS[plan]
    assert spec.max_messages is not None
    return spec.max_messages


async def test_a_stale_window_at_the_cap_rolls_over_with_no_scheduler_running() -> None:
    """The DoD scenario verbatim: a Pro org at 10,000/10,000 with `period_end` in the past
    answers the next visitor message and reads 2 / 10,000 — nothing scheduled this rollover."""
    engine = new_engine()
    limit = _limit("pro")
    try:
        async with _Org(engine, "pro") as org:
            past = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)
            await org.add_usage_row(messages_used=limit, extra_messages=0, period_end=past)

            admitted = await usage.reserve(AsyncSession(engine), org.id, limit)
            assert admitted is True

            row = await org.row()
            assert row.messages_used == 2
            assert row.extra_messages == 0
            assert row.period_end is not None and row.period_end > dt.datetime.now(tz=dt.UTC)
            assert row.period_start <= dt.datetime.now(tz=dt.UTC)
    finally:
        await engine.dispose()


async def test_a_fresh_window_at_the_cap_is_still_blocked() -> None:
    """The rollover only fires when the window is actually stale — a Pro org already at
    10,000/10,000 *within* its current period must stay blocked, not roll over early."""
    engine = new_engine()
    limit = _limit("pro")
    try:
        async with _Org(engine, "pro") as org:
            future = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=10)
            await org.add_usage_row(messages_used=limit, extra_messages=0, period_end=future)

            admitted = await usage.reserve(AsyncSession(engine), org.id, limit)
            assert admitted is False

            row = await org.row()
            assert row.messages_used == limit  # unchanged
    finally:
        await engine.dispose()


async def test_a_pack_raises_the_enforced_cap_and_lets_the_org_reply_again() -> None:
    engine = new_engine()
    limit = _limit("pro")
    try:
        async with _Org(engine, "pro") as org:
            future = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=10)
            await org.add_usage_row(messages_used=limit, extra_messages=0, period_end=future)

            # Blocked, and the entitlements the dashboard reads say exactly why.
            row = await org.row()
            ent = get_entitlements(
                "pro", plan_expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=60),
                messages_used=row.messages_used, extra_messages=row.extra_messages,
            )
            assert ent.bot_replies is False
            assert ent.messages_remaining == 0

            await usage.add_extra_messages(AsyncSession(engine), org.id, 1500)

            row = await org.row()
            ent = get_entitlements(
                "pro", plan_expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=60),
                messages_used=row.messages_used, extra_messages=row.extra_messages,
            )
            assert ent.effective_max_messages == limit + 1500
            assert ent.messages_remaining == 1500
            assert ent.bot_replies is True

            # And it can actually reserve again, using effective_max_messages as the caller must.
            admitted = await usage.reserve(AsyncSession(engine), org.id, ent.effective_max_messages)
            assert admitted is True
    finally:
        await engine.dispose()


async def test_packs_do_not_carry_over_a_rollover_resets_extra_messages_to_zero() -> None:
    engine = new_engine()
    limit = _limit("starter")
    try:
        async with _Org(engine, "starter") as org:
            past = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=1)
            # A pack bought last period, plus a lifetime total that only fits because of it.
            await org.add_usage_row(messages_used=limit, extra_messages=500, period_end=past)

            admitted = await usage.reserve(AsyncSession(engine), org.id, limit + 500)
            assert admitted is True

            row = await org.row()
            assert row.extra_messages == 0  # gone with the rollover
            assert row.messages_used == 2  # the fresh period's own count, not carried forward
    finally:
        await engine.dispose()


async def test_trial_and_legacy_rows_never_roll_null_period_end_is_unaffected() -> None:
    """`period_end IS NULL` (trial/legacy) must behave byte-identically to the lifetime counter
    this module always had — no rollover ever fires for it."""
    engine = new_engine()
    try:
        async with _Org(engine, "trial") as org:
            await org.add_usage_row(messages_used=498, extra_messages=0, period_end=None)
            assert await usage.reserve(AsyncSession(engine), org.id, 500) is True  # 498 -> 500
            assert await usage.reserve(AsyncSession(engine), org.id, 500) is False  # would be 502

            row = await org.row()
            assert row.messages_used == 500
            assert row.period_end is None
    finally:
        await engine.dispose()


async def test_start_period_resets_the_counter_and_opens_a_30_day_window() -> None:
    engine = new_engine()
    try:
        async with _Org(engine, "pro") as org:
            await org.add_usage_row(messages_used=9999, extra_messages=200, period_end=None)
            await usage.start_period(AsyncSession(engine), org.id, days=30)

            row = await org.row()
            assert row.messages_used == 0
            assert row.extra_messages == 0
            assert row.period_end is not None
            span = row.period_end - row.period_start
            assert dt.timedelta(days=29, hours=23) < span < dt.timedelta(days=30, hours=1)
    finally:
        await engine.dispose()


async def test_start_period_creates_the_row_when_the_org_has_never_been_metered() -> None:
    """A `legacy` org upgraded straight to a paid plan has no `org_message_usage` row yet."""
    engine = new_engine()
    try:
        async with _Org(engine, "legacy") as org:
            await usage.start_period(AsyncSession(engine), org.id, days=30)
            row = await org.row()
            assert row.messages_used == 0
            assert row.period_end is not None
    finally:
        await engine.dispose()


# ── concurrency: the rollover cannot be won twice ────────────────────────────────────
async def test_concurrent_reservations_against_a_stale_window_roll_over_exactly_once() -> None:
    """300 visitors race a Pro org sitting at the cap with a *stale* window — enough racers to
    exhaust the FRESH window too, so the test cannot pass by every caller simply fitting.
    Whoever wins the rollover resets the counter to its own `n` and opens a new 500-cap window;
    the rest compete for what is left of it. Regardless of who wins the roll, the final count is
    deterministic: floor(500/2) = 250 admitted, final `messages_used == 500`, never over.
    """
    engine = new_engine()
    limit = 500
    try:
        async with _Org(engine, "pro") as org:
            # Pro's own cap is 10,000; pin a smaller limit here so the boundary is reachable
            # with a race this size — reserve() enforces whatever `limit` it is given.
            past = dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1)
            await org.add_usage_row(messages_used=limit, extra_messages=0, period_end=past)

            async def one() -> bool:
                return await usage.reserve(AsyncSession(engine), org.id, limit)

            results = await asyncio.gather(*[one() for _ in range(300)])
            assert sum(results) == 250

            row = await org.row()
            assert row.messages_used == 500
            assert row.period_end is not None and row.period_end > dt.datetime.now(tz=dt.UTC)
    finally:
        await engine.dispose()
