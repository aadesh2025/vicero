"""Message metering for plans with a cap (docs/18 §7, docs/22 §6, ADR-088, ADR-102).

**1 message = 1 visitor message OR 1 AI reply**, so one question and its answer cost 2. Not
counted: an operator's manual reply from the inbox (no model cost), Playground turns (capped
separately per day), and system/event messages.

Race safety
    `reserve()` is ONE statement — an atomic `UPDATE … WHERE messages_used + :n <= :limit
    RETURNING messages_used`, that also rolls a stale billing window in the same statement
    (docs/22 §6). Postgres serialises concurrent updates of the row and re-evaluates the WHERE
    against the winner's value, so two visitors arriving together cannot both push past the cap,
    and a rollover can never race an increment. No read-then-write anywhere.

Billing period (docs/22 §6)
    `org_message_usage.period_end` is `NULL` for `trial`/`legacy` — the lifetime-counter
    behaviour this module always had. A paid plan gets a real 30-day window via
    `start_period()`; once `period_end` is in the past, the next `reserve()` rolls it forward
    and resets `messages_used`/`extra_messages` **lazily, in the same statement that admits the
    reservation** — never a scheduled job, for the same reason `plans.py`'s expiry is computed
    rather than cron-flipped: a dead scheduler must never be able to block a paying customer.
    `:limit` is always `effective_max_messages` (plan cap + packs) — callers must pass that, not
    the raw plan cap, or a pack does nothing.

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

from sqlalchemy import and_, case, func, select, update
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
    ent = get_entitlements(
        org.plan, trial_ends_at=org.trial_ends_at, plan_expires_at=org.plan_expires_at, now=now
    )
    if not ent.is_metered:
        return ent
    row = (
        await session.execute(
            select(OrgMessageUsage.messages_used, OrgMessageUsage.extra_messages).where(
                OrgMessageUsage.organization_id == org.id
            )
        )
    ).one_or_none()
    used, extra = (int(row[0]), int(row[1])) if row is not None else (0, 0)
    return get_entitlements(
        org.plan,
        trial_ends_at=org.trial_ends_at,
        plan_expires_at=org.plan_expires_at,
        messages_used=used,
        extra_messages=extra,
        now=now,
    )


async def load_entitlements_for(
    session: AsyncSession, org_id: uuid.UUID, *, now: dt.datetime | None = None
) -> Entitlements | None:
    """As `load_entitlements`, for callers that only hold an id. `None` if the org is gone."""
    org = await session.get(Organization, org_id)
    return None if org is None else await load_entitlements(session, org, now=now)


async def reserve(
    session: AsyncSession, org_id: uuid.UUID, limit: int, n: int = MESSAGES_PER_EXCHANGE
) -> bool:
    """Atomically claim `n` messages. False means the org is out — send nothing.

    `limit` must be `Entitlements.effective_max_messages` (the plan cap plus any packs bought
    this period) — this function does not recompute a cap, it enforces whatever it is given.
    """
    now = dt.datetime.now(tz=dt.UTC)
    async with _short_session(session) as tx:
        used = await _reserve_once(tx, org_id, limit, n, now)
        if used is None and await _ensure_row(tx, org_id):
            used = await _reserve_once(tx, org_id, limit, n, now)
        await tx.commit()
    return used is not None


def _reserve_stmt(org_id: uuid.UUID, limit: int, n: int, now: dt.datetime):  # type: ignore[no-untyped-def]
    """The atomic roll-and-increment (docs/22 §6).

    A stale window (`period_end` in the past) always admits the reservation — that is the roll
    — and resets `messages_used` to `n` and `extra_messages` to 0 (packs never carry over) in
    the same statement, opening a fresh 30-day window from `now`. A fresh/non-expiring window
    (`period_end IS NULL`, i.e. `trial`/`legacy`) behaves exactly as before: admitted only if
    `messages_used + n` still fits under `limit`.
    """
    stale = and_(OrgMessageUsage.period_end.isnot(None), OrgMessageUsage.period_end <= now)
    return (
        update(OrgMessageUsage)
        .where(
            OrgMessageUsage.organization_id == org_id,
            stale | (OrgMessageUsage.messages_used + n <= limit),
        )
        .values(
            messages_used=case((stale, n), else_=OrgMessageUsage.messages_used + n),
            extra_messages=case((stale, 0), else_=OrgMessageUsage.extra_messages),
            period_start=case((stale, now), else_=OrgMessageUsage.period_start),
            period_end=case(
                (stale, now + dt.timedelta(days=30)), else_=OrgMessageUsage.period_end
            ),
        )
        .returning(OrgMessageUsage.messages_used)
    )


async def _reserve_once(
    tx: AsyncSession, org_id: uuid.UUID, limit: int, n: int, now: dt.datetime
) -> int | None:
    return (await tx.execute(_reserve_stmt(org_id, limit, n, now))).scalar_one_or_none()


async def start_period(session: AsyncSession, org_id: uuid.UUID, days: int = 30) -> None:
    """Open a fresh billing window: called when a paid plan is granted (docs/22 §6).

    Resets the counter to 0 and sets `period_start`/`period_end` — an upsert, so it works
    whether or not the org has ever been metered before (a `legacy` org upgraded straight to a
    paid plan has no row yet).
    """
    now = dt.datetime.now(tz=dt.UTC)
    period_end = now + dt.timedelta(days=days)
    values = {
        "organization_id": org_id,
        "messages_used": 0,
        "extra_messages": 0,
        "period_start": now,
        "period_end": period_end,
    }
    async with _short_session(session) as tx:
        await tx.execute(
            pg_insert(OrgMessageUsage)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[OrgMessageUsage.organization_id],
                set_={
                    "messages_used": 0,
                    "extra_messages": 0,
                    "period_start": now,
                    "period_end": period_end,
                },
            )
        )
        await tx.commit()


async def add_extra_messages(session: AsyncSession, org_id: uuid.UUID, n: int) -> None:
    """Grant `n` extra messages for the current period (docs/22 §7's packs). A single atomic
    increment — packs are always a deliberate staff action, never auto-granted, and the caller
    (the admin "Add messages" endpoint) is responsible for the `plan_grants`/audit trail."""
    async with _short_session(session) as tx:
        await tx.execute(
            update(OrgMessageUsage)
            .where(OrgMessageUsage.organization_id == org_id)
            .values(extra_messages=OrgMessageUsage.extra_messages + n)
        )
        await tx.commit()


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


# ── Server-side gates (docs/18 §9, docs/22 §11) ───────────────────────────────
# Every one reads the plan through `get_entitlements`; none knows a limit or a plan name.
async def require_feature(session: AsyncSession, org: Organization, feature: str) -> None:
    """402 `plan_limit` unless the org's plan includes `feature` (workflows / n8n / tool_calling)."""
    # Feature flags do not depend on *usage*, so no counter query — but `plan_expires_at` is
    # still required: without it, a lapsed *paid* plan (as opposed to a finished trial) is never
    # detected as expired here, and its features stay unlocked past expiry (docs/22 §11).
    if not get_entitlements(org.plan, trial_ends_at=org.trial_ends_at, plan_expires_at=org.plan_expires_at).allows(
        feature
    ):
        raise plan_limit(feature)


def feature_allowed(org: Organization, feature: str) -> bool:
    """Non-raising form of `require_feature`, for the runtime, which must skip rather than fail."""
    return get_entitlements(
        org.plan, trial_ends_at=org.trial_ends_at, plan_expires_at=org.plan_expires_at
    ).allows(feature)


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
    spec = get_entitlements(
        org.plan, trial_ends_at=org.trial_ends_at, plan_expires_at=org.plan_expires_at
    ).spec
    if spec.publish_needs_verified_email and getattr(user, "email_verified_at", None) is None:
        raise AppError(
            "auth.email_unverified",
            "Verify your email address before publishing an agent to a live channel.",
            403,
        )


# ── Per-resource creation gates (docs/22 §11 enforcement matrix) ─────────────
# Same shape as `require_new_agent_slot` above: load entitlements, compare a live COUNT(*)
# against the plan's cap, 402 `plan_limit` at the cap. `None` = unlimited, never gated; `0` =
# not included in the plan at all, same 402 with a different message. An expired trial or
# lapsed paid plan blocks creation of every limited resource, not just agents.
def _plan_title(plan: str) -> str:
    return plan.replace("_", " ").title()


async def _require_not_expired(
    session: AsyncSession, org: Organization, feature: str, message: str
) -> Entitlements:
    ent = await load_entitlements(session, org)
    if ent.is_expired:
        raise plan_limit(feature, message)
    return ent


def _limit_error(feature: str, label: str, plan: str, limit: int) -> AppError:
    if limit == 0:
        return plan_limit(feature, f"{_plan_title(plan)} plan does not include {label}. Upgrade to add one.")
    return plan_limit(feature, f"{_plan_title(plan)} plan allows up to {limit} {label}. Upgrade to add more.")


async def _require_slot(
    session: AsyncSession, org: Organization, feature: str, label: str, count: int
) -> Entitlements:
    """`count` is the caller's own live `COUNT(*)` — computed by the caller because some
    resources (tools) pool more than one table into a single cap."""
    ent = await _require_not_expired(
        session, org, feature, f"Your plan has expired, so {label} are read-only. Upgrade to keep editing."
    )
    limit = ent.limit_for(feature)
    if limit is not None and count >= limit:
        raise _limit_error(feature, label, org.plan, limit)
    return ent


async def require_kb_slot(session: AsyncSession, org: Organization) -> None:
    from app.models import KnowledgeBase

    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(KnowledgeBase)
                .where(KnowledgeBase.organization_id == org.id, KnowledgeBase.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    await _require_slot(session, org, "knowledge_bases", "knowledge bases", count)


async def require_document_slot(session: AsyncSession, org: Organization, size_bytes: int) -> None:
    """Checked **before** the file is written or embedded (docs/22 §11) — the document-count
    limit, then the storage-bytes hard cap against `org_storage_usage`'s live counter."""
    from app.models import Document, OrgStorageUsage

    count = int(
        (
            await session.execute(
                select(func.count()).select_from(Document).where(Document.organization_id == org.id)
            )
        ).scalar_one()
    )
    ent = await _require_slot(session, org, "documents", "documents", count)

    storage_limit = ent.limit_for("storage")
    if storage_limit is not None:
        used = (
            await session.execute(
                select(OrgStorageUsage.bytes_used).where(OrgStorageUsage.organization_id == org.id)
            )
        ).scalar_one_or_none()
        if int(used or 0) + size_bytes > storage_limit:
            mb = storage_limit // (1024 * 1024)
            raise plan_limit(
                "storage", f"{_plan_title(org.plan)} plan includes {mb} MB of storage. Upgrade for more space."
            )


async def record_document_stored(session: AsyncSession, org_id: uuid.UUID, size_bytes: int) -> None:
    """Keep `org_storage_usage` live going forward — A1's migration only ever backfilled it
    once. On the same session as the document write, so both commit or roll back together."""
    from app.models import OrgStorageUsage

    await session.execute(
        pg_insert(OrgStorageUsage)
        .values(organization_id=org_id, bytes_used=size_bytes, documents_count=1)
        .on_conflict_do_update(
            index_elements=[OrgStorageUsage.organization_id],
            set_={
                "bytes_used": OrgStorageUsage.bytes_used + size_bytes,
                "documents_count": OrgStorageUsage.documents_count + 1,
            },
        )
    )


async def record_document_removed(session: AsyncSession, org_id: uuid.UUID, size_bytes: int) -> None:
    """The other half of `record_document_stored` — without this, storage usage only ever
    grows and an org that deletes content stays wrongly blocked."""
    from app.models import OrgStorageUsage

    await session.execute(
        update(OrgStorageUsage)
        .where(OrgStorageUsage.organization_id == org_id)
        .values(
            bytes_used=func.greatest(OrgStorageUsage.bytes_used - size_bytes, 0),
            documents_count=func.greatest(OrgStorageUsage.documents_count - 1, 0),
        )
    )


async def require_workflow_slot(session: AsyncSession, org: Organization) -> None:
    from app.models import Workflow

    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Workflow)
                .where(Workflow.organization_id == org.id, Workflow.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    await _require_slot(session, org, "workflows", "workflows", count)


async def require_tool_slot(session: AsyncSession, org: Organization) -> None:
    """Tools and MCP servers share one cap — docs/22 §11 groups "Tool count" across both
    (`tools/service.create_tool, MCP create`)."""
    from app.models import MCPServer, Tool

    tools = int(
        (
            await session.execute(
                select(func.count()).select_from(Tool).where(Tool.organization_id == org.id)
            )
        ).scalar_one()
    )
    mcp = int(
        (
            await session.execute(
                select(func.count()).select_from(MCPServer).where(MCPServer.organization_id == org.id)
            )
        ).scalar_one()
    )
    await _require_slot(session, org, "tools", "tools", tools + mcp)


async def require_webhook_slot(session: AsyncSession, org: Organization) -> None:
    from app.models import WebhookEndpoint

    count = int(
        (
            await session.execute(
                select(func.count()).select_from(WebhookEndpoint).where(WebhookEndpoint.organization_id == org.id)
            )
        ).scalar_one()
    )
    await _require_slot(session, org, "webhooks", "webhook endpoints", count)


async def require_team_member_slot(session: AsyncSession, org: Organization) -> None:
    """Checked on invitation **and** acceptance (docs/22 §11) — checking only one lets an org
    exceed its cap by pre-inviting more people than it can seat."""
    from app.models import Membership

    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.organization_id == org.id, Membership.status == "active")
            )
        ).scalar_one()
    )
    await _require_slot(session, org, "team_members", "team members", count)


async def require_channel_allowed(session: AsyncSession, org: Organization, kind: str) -> None:
    """The channel allowlist (docs/22 §11) — gated at connect **and** enable, not at
    message-send time, so a disallowed channel can never even be wired up."""
    ent = await _require_not_expired(
        session, org, "channels", "Your plan has expired, so channels are read-only. Upgrade to keep editing."
    )
    if not ent.allows_channel(kind):
        raise plan_limit(
            "channels", f"{_plan_title(org.plan)} plan does not include the {kind} channel. Upgrade to unlock it."
        )


async def require_automation_slot(session: AsyncSession, org: Organization) -> None:
    """A new automation request needs a free slot: live automations plus requests still in the queue count."""
    from app.models import Automation, AutomationRequest

    live = int(
        (
            await session.execute(
                select(func.count()).select_from(Automation).where(Automation.organization_id == org.id)
            )
        ).scalar_one()
    )
    queued = int(
        (
            await session.execute(
                select(func.count())
                .select_from(AutomationRequest)
                .where(
                    AutomationRequest.organization_id == org.id,
                    AutomationRequest.status.in_(("requested", "building")),
                )
            )
        ).scalar_one()
    )
    await _require_slot(session, org, "automations", "automations", live + queued)
