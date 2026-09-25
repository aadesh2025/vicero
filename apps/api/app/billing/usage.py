"""Message metering for plans with a cap (docs/18 §7, ADR-088).

**1 message = 1 visitor message OR 1 AI reply**, so one question and its answer cost 2. Not
counted: an operator's manual reply from the inbox (no model cost), Playground turns (capped
separately per day), and system/event messages.

Race safety
    `reserve()` is ONE statement — `UPDATE … SET messages_used = messages_used + :n WHERE
    messages_used + :n <= :limit RETURNING messages_used`. Postgres serialises concurrent
    updates of the row and re-evaluates the WHERE against the winner's value, so two visitors
    arriving together cannot both push past the cap. No read-then-write anywhere.

Why it runs in its own short transaction
    A row updated inside the request's transaction stays locked until that transaction ends —
    for a streamed reply, several seconds. Every other visitor of the same org would queue behind
    it. So each counter write opens a session on the request session's own bind and commits at
    once. In production that is a second pooled connection (lock held for one round trip); in
    the test suite the bind is the shared test connection, so the write joins the test's
    transaction and is rolled back with it, exactly like every other row a test creates.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.plans import MESSAGES_PER_EXCHANGE, Entitlements, get_entitlements, plan_limit
from app.core.ratelimit import limiter
from app.models import Organization, OrgMessageUsage


def _short_session(session: AsyncSession) -> AsyncSession:
    """A session on the same bind that commits independently of the request's transaction."""
    return AsyncSession(bind=session.bind, expire_on_commit=False, autoflush=False)


async def messages_used(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Fresh value straight from the table — never the identity map, which can be stale here."""
    value = (
        await session.execute(
            select(OrgMessageUsage.messages_used).where(OrgMessageUsage.organization_id == org_id)
        )
    ).scalar_one_or_none()
    return int(value or 0)


async def load_entitlements(
    session: AsyncSession, org: Organization, *, now: dt.datetime | None = None
) -> Entitlements:
    """What `org` may do right now. The only DB-backed way to ask (see `plans.get_entitlements`)."""
    ent = get_entitlements(org.plan, trial_ends_at=org.trial_ends_at, now=now)
    if not ent.is_metered:
        return ent
    used = await messages_used(session, org.id)
    return get_entitlements(org.plan, trial_ends_at=org.trial_ends_at, messages_used=used, now=now)


async def load_entitlements_for(
    session: AsyncSession, org_id: uuid.UUID, *, now: dt.datetime | None = None
) -> Entitlements | None:
    """As `load_entitlements`, for callers that only hold an id. `None` if the org is gone."""
    org = await session.get(Organization, org_id)
    return None if org is None else await load_entitlements(session, org, now=now)


async def reserve(
    session: AsyncSession, org_id: uuid.UUID, limit: int, n: int = MESSAGES_PER_EXCHANGE
) -> bool:
    """Atomically claim `n` messages. False means the org is out — send nothing."""
    async with _short_session(session) as tx:
        used = await _reserve_once(tx, org_id, limit, n)
        if used is None and await _ensure_row(tx, org_id):
            used = await _reserve_once(tx, org_id, limit, n)
        await tx.commit()
    return used is not None


def _reserve_stmt(org_id: uuid.UUID, limit: int, n: int):  # type: ignore[no-untyped-def]
    return (
        update(OrgMessageUsage)
        .where(
            OrgMessageUsage.organization_id == org_id,
            OrgMessageUsage.messages_used + n <= limit,
        )
        .values(messages_used=OrgMessageUsage.messages_used + n)
        .returning(OrgMessageUsage.messages_used)
    )


async def _reserve_once(tx: AsyncSession, org_id: uuid.UUID, limit: int, n: int) -> int | None:
    return (await tx.execute(_reserve_stmt(org_id, limit, n))).scalar_one_or_none()


async def _ensure_row(tx: AsyncSession, org_id: uuid.UUID) -> bool:
    """Create the counter row if missing. True when the retry is worth making (row now exists)."""
    exists = (
        await tx.execute(
            select(OrgMessageUsage.organization_id).where(OrgMessageUsage.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if exists is not None:
        return False  # the row was there: the UPDATE was refused because the org is out
    await tx.execute(
        pg_insert(OrgMessageUsage).values(organization_id=org_id).on_conflict_do_nothing()
    )
    return True


async def refund(session: AsyncSession, org_id: uuid.UUID, n: int = 1) -> None:
    """Give back `n` messages — the reply that was reserved but never produced."""
    async with _short_session(session) as tx:
        await tx.execute(
            update(OrgMessageUsage)
            .where(OrgMessageUsage.organization_id == org_id)
            .values(messages_used=OrgMessageUsage.messages_used - n)
        )
        await tx.commit()


async def record_unanswered(session: AsyncSession, org_id: uuid.UUID) -> int:
    """A visitor message was saved but the plan is out, so the bot stayed silent. Returns the new total.

    Counted because it is the strongest upgrade argument the dashboard has: "12 visitors wrote
    to you and nobody answered."
    """
    async with _short_session(session) as tx:
        total = (
            await tx.execute(
                pg_insert(OrgMessageUsage)
                .values(organization_id=org_id, unanswered_messages=1)
                .on_conflict_do_update(
                    index_elements=[OrgMessageUsage.organization_id],
                    set_={"unanswered_messages": OrgMessageUsage.unanswered_messages + 1},
                )
                .returning(OrgMessageUsage.unanswered_messages)
            )
        ).scalar_one()
        await tx.commit()
    return int(total)


async def unanswered_messages(session: AsyncSession, org_id: uuid.UUID) -> int:
    value = (
        await session.execute(
            select(OrgMessageUsage.unanswered_messages).where(
                OrgMessageUsage.organization_id == org_id
            )
        )
    ).scalar_one_or_none()
    return int(value or 0)


async def playground_allowed(org_id: uuid.UUID, ent: Entitlements) -> bool:
    """Dashboard test chat: a daily cap on metered plans so it cannot become a free chatbot.

    Uses the shared Redis fixed-window limiter (per-process memory when Redis is down), which is
    exact enough for a courtesy cap. `None` = unlimited, `0` = not allowed (expired trial).
    """
    cap = ent.playground_per_day
    if cap is None:
        return True
    if cap <= 0:
        return False
    allowed, _ = await limiter.hit(f"playground:{org_id}", cap, 86400)
    return allowed


# ── Server-side gates (docs/18 §9) ────────────────────────────────────────────
# Every one reads the plan through `get_entitlements`; none knows a limit or a plan name.
async def require_feature(session: AsyncSession, org: Organization, feature: str) -> None:
    """402 `plan_limit` unless the org's plan includes `feature` (workflows / n8n / tool_calling)."""
    # Feature flags do not depend on usage, so no counter query: a trial and an expired trial
    # both lock the same features.
    if not get_entitlements(org.plan, trial_ends_at=org.trial_ends_at).allows(feature):
        raise plan_limit(feature)


def feature_allowed(org: Organization, feature: str) -> bool:
    """Non-raising form of `require_feature`, for the runtime, which must skip rather than fail."""
    return get_entitlements(org.plan, trial_ends_at=org.trial_ends_at).allows(feature)


async def require_agents_writable(session: AsyncSession, org: Organization) -> Entitlements:
    """An expired trial keeps its agent read-only: nothing that edits or spends may proceed."""
    ent = await load_entitlements(session, org)
    if not ent.agents_writable:
        raise plan_limit(
            "agents", "Your free trial has ended, so your agent is read-only. Upgrade to keep editing."
        )
    return ent


async def require_new_agent_slot(session: AsyncSession, org: Organization) -> None:
    from sqlalchemy import func

    from app.models import Agent

    ent = await require_agents_writable(session, org)
    if ent.max_agents is None:
        return
    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Agent)
                .where(Agent.organization_id == org.id, Agent.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if count >= ent.max_agents:
        raise plan_limit("agents", "Your plan includes one agent. Upgrade to create more.")


def require_verified_email_to_go_live(org: Organization, user: object) -> None:
    """Publishing to a live channel needs a verified address on plans that ask for it.

    Only self-serve (trial) workspaces are held to this: accounts provisioned before self-serve
    existed may never have verified, and locking a client out of their own agent is exactly what
    the `legacy` plan exists to prevent.
    """
    spec = get_entitlements(org.plan, trial_ends_at=org.trial_ends_at).spec
    if spec.publish_needs_verified_email and getattr(user, "email_verified_at", None) is None:
        raise AppError(
            "auth.email_unverified",
            "Verify your email address before publishing an agent to a live channel.",
            403,
        )
