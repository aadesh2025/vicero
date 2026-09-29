"""Admin console service: cross-tenant aggregates (no org scoping — staff only)."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import NamedTuple

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import cycles as billing_cycles
from app.billing import usage as billing_usage
from app.chat import guard_models
from app.core.audit import write_audit
from app.core.config import settings
from app.core.errors import AppError
from app.core.plans import GRANTABLE_PLANS, PAID_PLANS, PLANS, get_entitlements
from app.core.probes import check_database, check_redis
from app.integrations.n8n_client import N8nClient
from app.integrations.n8n_client import get_client as get_n8n_client
from app.integrations.n8n_signature import FIX_HINT, unverified_reason
from app.models import (
    Agent,
    AgentVersion,
    BillingCycle,
    Conversation,
    FeatureFlag,
    Membership,
    Message,
    Organization,
    OrgMessageUsage,
    OrgStorageUsage,
    PlanGrant,
    Tool,
    UsageRecord,
    User,
    Workflow,
    WorkflowVersion,
)
from app.modules.admin import schemas
from app.tools.service import INTERNAL_TAGS, SHARED_TEMPLATE_TAG

#: How close to the cap counts as a warning, not yet a hard stop (docs/22 doesn't pin a number
#: — decided here, recorded in ADR-103).
_NEAR_LIMIT_RATIO = 0.9
#: How many days out a plan counts as "expiring soon" for the `?status=expiring` filter.
_EXPIRING_SOON_DAYS = 7


class _OrgBilling(NamedTuple):
    messages_used: int
    extra_messages: int
    unanswered_messages: int
    storage_bytes_used: int
    latest_cycle: BillingCycle | None


async def _billing_snapshot(
    session: AsyncSession, org_ids: list[uuid.UUID]
) -> dict[uuid.UUID, _OrgBilling]:
    """Per-org message/storage/payment snapshot for the roster and its filters.

    Batch-fetched here rather than N single-org calls into `app/billing/`: this phase may only
    *call* that package, not extend it with a bulk variant, and a per-org query for the whole
    roster would be N+1 exactly like every other aggregate on this page.
    """
    empty = _OrgBilling(0, 0, 0, 0, None)
    out: dict[uuid.UUID, _OrgBilling] = {oid: empty for oid in org_ids}
    if not org_ids:
        return out

    usage_rows = (
        await session.execute(
            select(
                OrgMessageUsage.organization_id,
                OrgMessageUsage.messages_used,
                OrgMessageUsage.extra_messages,
                OrgMessageUsage.unanswered_messages,
            ).where(OrgMessageUsage.organization_id.in_(org_ids))
        )
    ).all()
    storage_rows = (
        await session.execute(
            select(OrgStorageUsage.organization_id, OrgStorageUsage.bytes_used).where(
                OrgStorageUsage.organization_id.in_(org_ids)
            )
        )
    ).all()
    # Newest first, so the first row seen per org in the loop below is its latest cycle.
    cycle_rows = (
        (
            await session.execute(
                select(BillingCycle)
                .where(BillingCycle.organization_id.in_(org_ids))
                .order_by(BillingCycle.period_start.desc())
            )
        )
        .scalars()
        .all()
    )

    used = {oid: (int(u), int(e), int(un)) for oid, u, e, un in usage_rows}
    stored = {oid: int(b) for oid, b in storage_rows}
    latest_cycle: dict[uuid.UUID, BillingCycle] = {}
    for cycle in cycle_rows:
        latest_cycle.setdefault(cycle.organization_id, cycle)

    for oid in org_ids:
        u, e, un = used.get(oid, (0, 0, 0))
        out[oid] = _OrgBilling(u, e, un, stored.get(oid, 0), latest_cycle.get(oid))
    return out


async def _owner_emails(session: AsyncSession, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not org_ids:
        return {}
    rows = (
        await session.execute(
            select(Membership.organization_id, User.email)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id.in_(org_ids),
                Membership.role == "owner",
                Membership.status == "active",
            )
        )
    ).all()
    # If an org somehow has more than one active owner, the first one found wins — this is a
    # display convenience, not an access decision.
    out: dict[uuid.UUID, str] = {}
    for oid, email in rows:
        out.setdefault(oid, email)
    return out


def _org_status_label(
    org: Organization, used: int, extra: int, latest_cycle: BillingCycle | None, now: dt.datetime
) -> tuple[str, int | None]:
    """`(status, effective_max_messages)` — the computed plan status plus the enforced message
    cap, so callers building `OrgAdminOut` don't call `get_entitlements` a second time."""
    ent = get_entitlements(
        org.plan,
        trial_ends_at=org.trial_ends_at,
        plan_expires_at=org.plan_expires_at,
        messages_used=used,
        extra_messages=extra,
        now=now,
    )
    return ent.status, ent.effective_max_messages


def _matches_status_filter(
    status_filter: str,
    *,
    ent_status: str,
    messages_used: int,
    messages_limit: int | None,
    plan_expires_at: dt.datetime | None,
    payment_state: str | None,
    now: dt.datetime,
) -> bool:
    if status_filter == "expired":
        return ent_status in ("trial_expired", "plan_expired")
    if status_filter == "at_limit":
        return messages_limit is not None and messages_used >= messages_limit
    if status_filter == "near_limit":
        return (
            messages_limit is not None
            and messages_used < messages_limit
            and messages_used >= _NEAR_LIMIT_RATIO * messages_limit
        )
    if status_filter == "expiring":
        return (
            plan_expires_at is not None
            and now <= plan_expires_at <= now + dt.timedelta(days=_EXPIRING_SOON_DAYS)
        )
    if status_filter == "payment_pending":
        return payment_state == "pending"
    if status_filter == "overdue":
        return payment_state == "overdue"
    raise AppError("invalid_status_filter", f"Unknown status filter {status_filter!r}.", 400)


async def list_orgs(
    session: AsyncSession,
    limit: int = 100,
    *,
    include_deleted: bool = False,
    q: str | None = None,
    plan: str | None = None,
    status_filter: str | None = None,
    org_id: uuid.UUID | None = None,
) -> list[schemas.OrgAdminOut]:
    """Every organization on the platform.

    Soft-deleted orgs are **excluded by default**, matching `list_users` — which always had
    `deleted_at IS NULL` while this did not. That inconsistency was the bug: a client who
    deleted their workspace stayed in the staff console forever, indistinguishable at a glance
    from a live tenant, and the roster slowly filled with things that no longer exist.

    `include_deleted=True` keeps the audit view available, since "which client left, and when"
    is a real question — it is just not what the default list should be answering.
    """
    members = (
        select(Membership.organization_id, func.count().label("n"))
        .where(Membership.status == "active")
        .group_by(Membership.organization_id)
        .subquery()
    )
    agents = (
        select(Agent.organization_id, func.count().label("n")).group_by(Agent.organization_id).subquery()
    )
    # Agents with a draft newer than what's live. Clients can save but not publish, so this
    # is the signal that someone's changes are waiting — without opening each builder.
    latest_version = (
        select(AgentVersion.agent_id, func.max(AgentVersion.version).label("latest"))
        .group_by(AgentVersion.agent_id)
        .subquery()
    )
    pending = (
        select(Agent.organization_id, func.count().label("n"))
        .join(latest_version, latest_version.c.agent_id == Agent.id)
        .outerjoin(
            AgentVersion,
            (AgentVersion.id == Agent.current_version_id),
        )
        .where(
            Agent.deleted_at.is_(None),
            # Never published at all, or the newest draft is past the live version.
            or_(
                Agent.current_version_id.is_(None),
                latest_version.c.latest > AgentVersion.version,
            ),
        )
        .group_by(Agent.organization_id)
        .subquery()
    )
    # Workflows awaiting review (docs/17 Phase 4) — NOT the same "Workflow" as an n8n
    # automation (see `AutomationOut`); this is Vicero's own visual workflow builder entity.
    # Unlike Agent, `WorkflowVersion.status` carries a real `in_review` value (ADR-080), so this
    # counts that directly rather than aping Agent's "latest draft is ahead of published" proxy
    # — a more honest signal now that one actually exists to read.
    latest_workflow_version = (
        select(WorkflowVersion.workflow_id, func.max(WorkflowVersion.version).label("latest"))
        .group_by(WorkflowVersion.workflow_id)
        .subquery()
    )
    workflows_review = (
        select(Workflow.organization_id, func.count().label("n"))
        .join(latest_workflow_version, latest_workflow_version.c.workflow_id == Workflow.id)
        .join(
            WorkflowVersion,
            (WorkflowVersion.workflow_id == Workflow.id)
            & (WorkflowVersion.version == latest_workflow_version.c.latest),
        )
        .where(Workflow.deleted_at.is_(None), WorkflowVersion.status == "in_review")
        .group_by(Workflow.organization_id)
        .subquery()
    )
    stmt = (
        select(
            Organization,
            func.coalesce(members.c.n, 0),
            func.coalesce(agents.c.n, 0),
            func.coalesce(pending.c.n, 0),
            func.coalesce(workflows_review.c.n, 0),
        )
        .outerjoin(members, members.c.organization_id == Organization.id)
        .outerjoin(agents, agents.c.organization_id == Organization.id)
        .outerjoin(pending, pending.c.organization_id == Organization.id)
        .outerjoin(workflows_review, workflows_review.c.organization_id == Organization.id)
        .order_by(Organization.created_at.desc())
        .limit(limit)
    )
    if not include_deleted:
        stmt = stmt.where(Organization.deleted_at.is_(None))
    if plan is not None:
        stmt = stmt.where(Organization.plan == plan)
    if org_id is not None:
        # The single-org views reuse this function rather than repeating its count subqueries.
        stmt = stmt.where(Organization.id == org_id)
    rows = (await session.execute(stmt)).all()

    # One query for every org's membership, then grouped in Python — a per-row query here
    # would be N+1 across the whole platform roster.
    org_ids = [org.id for org, *_ in rows]
    by_org: dict[uuid.UUID, list[schemas.OrgMemberOut]] = {}
    if org_ids:
        member_rows = (
            await session.execute(
                select(Membership.organization_id, User.email, Membership.role, Membership.status, User.is_staff)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.organization_id.in_(org_ids),
                    Membership.status == "active",
                    User.deleted_at.is_(None),
                )
                .order_by(Membership.role, User.email)
            )
        ).all()
        for oid, email, role, status, staff in member_rows:
            by_org.setdefault(oid, []).append(
                schemas.OrgMemberOut(email=email, role=role, status=status, is_staff=bool(staff))
            )

    billing = await _billing_snapshot(session, org_ids)
    owners = await _owner_emails(session, org_ids)
    now = dt.datetime.now(tz=dt.UTC)

    out: list[schemas.OrgAdminOut] = []
    for org, m, a, p, wr in rows:
        b = billing[org.id]
        ent_status, messages_limit = _org_status_label(
            org, b.messages_used, b.extra_messages, b.latest_cycle, now
        )
        payment_state = (
            billing_cycles.payment_state(b.latest_cycle, now) if b.latest_cycle is not None else None
        )
        row = schemas.OrgAdminOut(
            id=org.id,
            name=org.name,
            slug=org.slug,
            members=int(m),
            member_list=by_org.get(org.id, []),
            agents=int(a),
            agents_with_unpublished_changes=int(p),
            workflows_awaiting_review=int(wr),
            created_at=org.created_at,
            deleted=org.deleted_at is not None,
            plan=org.plan,
            status=ent_status,
            plan_source=org.plan_source,
            plan_expires_at=org.plan_expires_at,
            plan_note=org.plan_note,
            messages_used=b.messages_used,
            messages_limit=messages_limit,
            unanswered_messages=b.unanswered_messages,
            storage_bytes_used=b.storage_bytes_used,
            storage_bytes_limit=get_entitlements(org.plan, plan_expires_at=org.plan_expires_at, now=now).storage_bytes,
            payment_state=payment_state,
            owner_email=owners.get(org.id),
        )
        if q:
            needle = q.strip().lower()
            haystack = f"{row.name} {row.owner_email or ''}".lower()
            if needle not in haystack:
                continue
        if status_filter and not _matches_status_filter(
            status_filter,
            ent_status=ent_status,
            messages_used=b.messages_used,
            messages_limit=messages_limit,
            plan_expires_at=org.plan_expires_at,
            payment_state=payment_state,
            now=now,
        ):
            continue
        out.append(row)
    return out


async def list_users(
    session: AsyncSession, limit: int = 100, *, include_system: bool = False
) -> list[schemas.UserAdminOut]:
    """Human accounts. Machine logins are hidden unless `include_system=True`."""
    orgs = (
        select(Membership.user_id, func.count().label("n"))
        .where(Membership.status == "active")
        .group_by(Membership.user_id)
        .subquery()
    )
    stmt = (
        select(User, func.coalesce(orgs.c.n, 0))
        .outerjoin(orgs, orgs.c.user_id == User.id)
        .where(User.deleted_at.is_(None))
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    if not include_system:
        # `provision@vicero.dev` is a working credential, not a person. Deleting it to tidy
        # this list would break `make provision`; hiding it keeps the roster to actual humans.
        stmt = stmt.where(User.is_system.is_(False))
    rows = (await session.execute(stmt)).all()

    user_ids = [u.id for u, _ in rows]
    memberships: dict[uuid.UUID, list[schemas.UserMembershipOut]] = {}
    if user_ids:
        mrows = (
            await session.execute(
                select(Membership.user_id, Organization.id, Organization.name, Membership.role)
                .join(Organization, Organization.id == Membership.organization_id)
                .where(
                    Membership.user_id.in_(user_ids),
                    Membership.status == "active",
                    Organization.deleted_at.is_(None),
                )
                .order_by(Organization.name)
            )
        ).all()
        for uid, oid, oname, role in mrows:
            memberships.setdefault(uid, []).append(
                schemas.UserMembershipOut(organization_id=oid, organization_name=oname, role=role)
            )
    return [
        schemas.UserAdminOut(
            id=u.id,
            email=u.email,
            is_staff=u.is_staff,
            is_system=u.is_system,
            is_active=u.is_active,
            orgs=int(n),
            memberships=memberships.get(u.id, []),
            created_at=u.created_at,
        )
        for u, n in rows
    ]


async def platform_usage(session: AsyncSession) -> schemas.PlatformUsageOut:
    counts = await asyncio.gather(
        session.scalar(select(func.count()).select_from(Organization).where(Organization.deleted_at.is_(None))),
        session.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))),
        session.scalar(select(func.count()).select_from(Agent)),
        session.scalar(select(func.count()).select_from(Conversation)),
        session.scalar(select(func.count()).select_from(Message)),
    )
    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.tokens_prompt), 0),
                func.coalesce(func.sum(UsageRecord.tokens_completion), 0),
                func.coalesce(func.sum(UsageRecord.cost_micros), 0),
            )
        )
    ).one()
    top_stmt = (
        select(
            Organization.id,
            Organization.name,
            func.coalesce(func.sum(UsageRecord.tokens_prompt), 0),
            func.coalesce(func.sum(UsageRecord.tokens_completion), 0),
            func.coalesce(func.sum(UsageRecord.requests), 0),
            func.coalesce(func.sum(UsageRecord.cost_micros), 0),
        )
        .join(UsageRecord, UsageRecord.organization_id == Organization.id)
        .group_by(Organization.id, Organization.name)
        .order_by(func.sum(UsageRecord.tokens_prompt).desc())
        .limit(10)
    )
    top = [
        schemas.OrgUsageRow(
            organization_id=oid,
            name=name,
            tokens_prompt=int(tp),
            tokens_completion=int(tc),
            requests=int(rq),
            cost_micros=int(cm),
        )
        for oid, name, tp, tc, rq, cm in (await session.execute(top_stmt)).all()
    ]
    return schemas.PlatformUsageOut(
        organizations=int(counts[0] or 0),
        users=int(counts[1] or 0),
        agents=int(counts[2] or 0),
        conversations=int(counts[3] or 0),
        messages=int(counts[4] or 0),
        tokens_prompt=int(totals[0]),
        tokens_completion=int(totals[1]),
        cost_micros=int(totals[2]),
        top_orgs=top,
    )


async def health(session: AsyncSession) -> schemas.HealthOut:
    db_ok, redis_ok, orgs, users, convos, msgs = await asyncio.gather(
        check_database(),
        check_redis(),
        session.scalar(select(func.count()).select_from(Organization).where(Organization.deleted_at.is_(None))),
        session.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))),
        session.scalar(select(func.count()).select_from(Conversation)),
        session.scalar(select(func.count()).select_from(Message)),
    )
    return schemas.HealthOut(
        database=db_ok,
        redis=redis_ok,
        organizations=int(orgs or 0),
        users=int(users or 0),
        conversations=int(convos or 0),
        messages=int(msgs or 0),
        guard_injection_available=guard_models.is_available(),
        guard_injection_reason=guard_models.unavailable_reason(),
        guard_injection_model=settings.guard_injection_model,
    )


async def automations_overview(session: AsyncSession) -> schemas.AutomationsOverviewOut:
    """Every n8n workflow, resolved to the org its tags scope it to.

    The one place staff can see all clients' automations without switching org. It is also
    the working view for the tagging backlog: under deny-by-default an untagged workflow is
    invisible to everyone, so `untagged` here means "nobody can use this yet", and
    `unknown-org` means the tag doesn't match any organization slug — usually a typo.
    """
    slugs = {
        slug: name
        for slug, name in (
            await session.execute(
                select(Organization.slug, Organization.name).where(Organization.deleted_at.is_(None))
            )
        ).all()
    }

    client = get_n8n_client()
    try:
        raw = await client.list_workflows()
    except AppError as exc:
        # n8n being down or keyless is a normal operational state, not a 500 for the console.
        return schemas.AutomationsOverviewOut(workflows=[], error=exc.message)

    bindings = await _n8n_bindings(session)

    out: list[schemas.AutomationOut] = []
    for wf in raw:
        wf_id = str(wf.get("id"))
        name = str(wf.get("name", "workflow"))
        tags = sorted(client.extract_tags(wf))
        owner, kind, org_name = _resolve_owner(tags, name, slugs)
        out.append(
            schemas.AutomationOut(
                id=wf_id,
                name=name,
                active=bool(wf.get("active", False)),
                tags=tags,
                owner=owner,
                owner_kind=kind,
                organization_name=org_name,
                webhook_url=client.extract_webhook_url(wf),
                bindings=bindings.get(wf_id, []),
            )
        )
    # Unowned first: this list is a to-do, so what needs attention sorts to the top.
    order = {"untagged": 0, "unknown-org": 1, "org": 2, "shared-template": 3, "internal": 4}
    out.sort(key=lambda w: (order.get(w.owner_kind, 9), w.name.lower()))
    return schemas.AutomationsOverviewOut(workflows=out)


async def n8n_signature_audit(session: AsyncSession) -> schemas.N8nSignatureAuditOut:
    """Every bound n8n tool, across all orgs, judged by the bind-time signature check (R15).

    Bind-time enforcement only covers binds made after it shipped. This is the sweep over the
    ones that predate it — and over any workflow edited *after* it was bound. Disabled tools
    are included: a disabled tool is one click from live.
    """
    client = get_n8n_client()
    try:
        workflows = await client.list_workflows()
    except AppError as exc:
        return schemas.N8nSignatureAuditOut(error=exc.message)
    by_id = {str(w.get("id")): w for w in workflows}

    stmt = (
        select(Tool, Organization.slug, Organization.name, Agent.name)
        .join(Organization, Organization.id == Tool.organization_id)
        .outerjoin(Agent, Agent.id == Tool.agent_id)
        .where(Tool.type == "n8n")
        .order_by(Organization.slug, Tool.name)
    )
    out = schemas.N8nSignatureAuditOut(fix_hint=FIX_HINT)
    for tool, org_slug, org_name, agent_name in (await session.execute(stmt)).all():
        config = tool.config or {}
        wf_id = config.get("workflow_id")
        url = config.get("webhook_url")
        # The URL is what Vicero actually calls, so it decides — same rule as the bind check.
        wf = N8nClient.match_workflow_by_webhook_url(workflows, url) if url else None
        if wf is None and wf_id and not url:
            wf = by_id.get(str(wf_id))
        reason: str | None
        if wf is None:
            status, reason = "unresolved", "no workflow in n8n serves this tool's webhook, so it cannot be checked"
        else:
            reason = unverified_reason(wf)
            status = "verified" if reason is None else "unverified"
        if status == "verified":
            out.verified += 1
            continue
        if status == "unverified":
            out.unverified += 1
        else:
            out.unresolved += 1
        out.findings.append(
            schemas.N8nSignatureFindingOut(
                organization_slug=org_slug,
                organization_name=org_name,
                agent_name=agent_name,
                tool_name=tool.name,
                enabled=bool(tool.enabled),
                workflow_id=str(wf.get("id")) if wf else (str(wf_id) if wf_id else None),
                workflow_name=str(wf.get("name")) if wf else None,
                status=status,
                reason=reason,
            )
        )
    out.findings.sort(key=lambda f: (f.status != "unverified", f.organization_slug, f.tool_name))
    return out


def _resolve_owner(
    tags: list[str], name: str, slugs: dict[str, str]
) -> tuple[str, str, str | None]:
    """Mirror `tools.service.workflow_visible_to_org`'s precedence, but report rather than
    filter — staff need to see the internal and unowned workflows a client never would."""
    if set(tags) & INTERNAL_TAGS or name.strip().lower().startswith(("shared —", "shared -")):
        return "internal", "internal", None
    if SHARED_TEMPLATE_TAG in tags:
        return "shared-template", "shared-template", None
    for tag in tags:
        if tag in slugs:
            return tag, "org", slugs[tag]
    if not tags:
        return "untagged", "untagged", None
    return tags[0], "unknown-org", None


async def _n8n_bindings(session: AsyncSession) -> dict[str, list[schemas.AutomationBindingOut]]:
    """Which agents bind each workflow, keyed by the n8n workflow id on the tool's config."""
    stmt = (
        select(Tool, Organization.slug, Organization.name, Agent.name)
        .join(Organization, Organization.id == Tool.organization_id)
        .outerjoin(Agent, Agent.id == Tool.agent_id)
        .where(Tool.type == "n8n")
    )
    found: dict[str, list[schemas.AutomationBindingOut]] = {}
    for tool, org_slug, org_name, agent_name in (await session.execute(stmt)).all():
        config = tool.config or {}
        wf_id = config.get("workflow_id")
        if not wf_id:
            continue
        found.setdefault(str(wf_id), []).append(
            schemas.AutomationBindingOut(
                organization_slug=org_slug,
                organization_name=org_name,
                agent_name=agent_name,
                tool_name=tool.name,
                enabled=bool(tool.enabled),
                mode=config.get("mode"),
            )
        )
    return found


async def list_flags(session: AsyncSession) -> list[schemas.FeatureFlagOut]:
    rows = (await session.execute(select(FeatureFlag).order_by(FeatureFlag.key))).scalars().all()
    return [
        schemas.FeatureFlagOut(key=f.key, enabled=f.enabled, description=f.description, updated_at=f.updated_at)
        for f in rows
    ]


async def upsert_flag(
    session: AsyncSession, key: str, data: schemas.FeatureFlagUpdate
) -> schemas.FeatureFlagOut:
    stmt = (
        pg_insert(FeatureFlag)
        .values(key=key, enabled=data.enabled, description=data.description)
        .on_conflict_do_update(
            index_elements=[FeatureFlag.key],
            set_={"enabled": data.enabled, "description": data.description},
        )
        .returning(FeatureFlag)
    )
    flag = (await session.execute(stmt)).scalar_one()
    return schemas.FeatureFlagOut(
        key=flag.key, enabled=flag.enabled, description=flag.description, updated_at=flag.updated_at
    )


# ── billing: grant / revoke / packs / payment ledger (docs/22 §8.2) ─────────────
#
# Every mutation below is staff-only (`require_staff` on the route) and leaves two records:
# a `plan_grants` row (the billing view) and a `write_audit` entry (the security view).
#
# One deliberate non-duplication: for a paid plan, `cycles.open_cycle()` already writes the
# `granted` row — so `grant_plan` does not write a second one, and the from→to plan change is
# carried in the audit meta instead. A non-paid grant (trial/legacy) opens no cycle, so there
# it writes its own. Exactly one `granted` row per grant, either way.


def _require_note(note: str | None, field: str = "note") -> str:
    """Billing evidence with no reason is not evidence (docs/22 §8.2 rule 1)."""
    cleaned = (note or "").strip()
    if not cleaned:
        raise AppError("note_required", "Say why — the client name or an invoice reference.", 400)
    return cleaned


async def _get_org(session: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None or org.deleted_at is not None:
        raise AppError("org_not_found", "Workspace not found.", 404)
    return org


async def _org_out(session: AsyncSession, org_id: uuid.UUID) -> schemas.OrgAdminOut:
    rows = await list_orgs(session, limit=1, include_deleted=True, org_id=org_id)
    if not rows:
        raise AppError("org_not_found", "Workspace not found.", 404)
    return rows[0]


def _cycle_out(
    cycle: BillingCycle, now: dt.datetime, org_name: str | None = None
) -> schemas.BillingCycleOut:
    return schemas.BillingCycleOut(
        id=cycle.id,
        organization_id=cycle.organization_id,
        organization_name=org_name,
        plan=cycle.plan,
        period_start=cycle.period_start,
        period_end=cycle.period_end,
        amount_usd_cents=cycle.amount_usd_cents,
        status=cycle.status,
        payment_state=billing_cycles.payment_state(cycle, now),
        paid_at=cycle.paid_at,
        method=cycle.method,
        reference=cycle.reference,
        note=cycle.note,
        created_at=cycle.created_at,
    )


async def get_org_detail(session: AsyncSession, org_id: uuid.UUID) -> schemas.OrgAdminDetailOut:
    """One workspace, plus the billing evidence behind it — what staff open during a dispute."""
    base = await _org_out(session, org_id)
    now = dt.datetime.now(tz=dt.UTC)

    grants = (
        (
            await session.execute(
                select(PlanGrant)
                .where(PlanGrant.organization_id == org_id)
                .order_by(PlanGrant.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    actor_ids = [g.actor_id for g in grants if g.actor_id is not None]
    actors: dict[uuid.UUID, str] = {}
    if actor_ids:
        actors = {
            uid: email
            for uid, email in (
                await session.execute(select(User.id, User.email).where(User.id.in_(actor_ids)))
            ).all()
        }

    cycles = (
        (
            await session.execute(
                select(BillingCycle)
                .where(BillingCycle.organization_id == org_id)
                .order_by(BillingCycle.period_start.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    return schemas.OrgAdminDetailOut(
        **base.model_dump(),
        recent_grants=[
            schemas.PlanGrantOut(
                id=g.id,
                action=g.action,
                from_plan=g.from_plan,
                to_plan=g.to_plan,
                expires_at=g.expires_at,
                extra_messages=g.extra_messages,
                amount_usd_cents=g.amount_usd_cents,
                note=g.note,
                actor_email=actors.get(g.actor_id) if g.actor_id else None,
                invoiced=bool(g.invoiced),
                created_at=g.created_at,
            )
            for g in grants
        ],
        billing_cycles=[_cycle_out(c, now, base.name) for c in cycles],
    )


async def grant_plan(
    session: AsyncSession,
    org_id: uuid.UUID,
    data: schemas.GrantPlanIn,
    *,
    staff: User,
    ip: str | None = None,
) -> schemas.OrgAdminOut:
    """Put a workspace on a plan. Takes effect on the org's very next request — `get_entitlements`
    reads the row every time, so there is no cache to invalidate and no restart to wait for.
    """
    now = dt.datetime.now(tz=dt.UTC)

    # Validate everything before touching the row, so a rejected grant writes nothing at all.
    if data.plan not in GRANTABLE_PLANS:
        raise AppError("invalid_plan", f"Unknown plan {data.plan!r}.", 400)
    if data.expires_at is not None and data.expires_at <= now:
        raise AppError("invalid_expiry", "Expiry must be in the future.", 400)
    note = _require_note(data.note)
    org = await _get_org(session, org_id)
    previous = org.plan

    org.plan = data.plan
    org.plan_source = "admin"
    org.plan_expires_at = data.expires_at
    org.plan_granted_at = now
    org.plan_granted_by = staff.id
    org.plan_note = note

    await write_audit(
        session,
        org_id,
        staff.id,
        "billing.plan_granted",
        target_type="organization",
        target_id=str(org_id),
        meta={
            "from": previous,
            "to": data.plan,
            "expires_at": data.expires_at.isoformat() if data.expires_at else None,
            "payment_received": data.payment_received,
        },
        ip=ip,
    )

    if data.plan in PAID_PLANS:
        # Restart the message window: a client upgrading on day 28 of a trial must not inherit
        # a period that dies in two days (docs/22 §8.2 rule 4).
        await billing_usage.start_period(session, org_id, days=30)
        # Commits the whole transaction, including the org mutation above, and writes the
        # `granted` row — which is why this function does not add one of its own.
        await billing_cycles.open_cycle(
            session,
            org_id,
            plan=data.plan,
            paid=data.payment_received,
            method=data.method,
            reference=data.reference,
            note=note,
            marked_by=staff.id,
        )
    else:
        session.add(
            PlanGrant(
                organization_id=org_id,
                action="granted",
                from_plan=previous,
                to_plan=data.plan,
                expires_at=data.expires_at,
                note=note,
                actor_id=staff.id,
            )
        )
        await session.commit()
    return await _org_out(session, org_id)


async def revoke_plan(
    session: AsyncSession,
    org_id: uuid.UUID,
    data: schemas.RevokePlanIn,
    *,
    staff: User,
    ip: str | None = None,
) -> schemas.OrgAdminOut:
    """Stop a workspace without destroying it (docs/22 §16.1).

    Revoking expires the plan rather than rewriting it: `plan` stays put so the meter still
    reads `8,412 / 10,000`, and `get_entitlements` computes `plan_expired` from the lapsed
    date. **Nothing is deleted** — every agent, document and conversation survives, read-only,
    so re-granting on payment restores the workspace whole.
    """
    now = dt.datetime.now(tz=dt.UTC)
    note = _require_note(data.note)
    org = await _get_org(session, org_id)

    if org.plan in PAID_PLANS:
        org.plan_expires_at = now
    elif org.plan == "trial":
        org.trial_ends_at = now
    else:
        # `legacy` is unlimited-by-design (your own and demo workspaces). It has no expiry to
        # lapse, so silently doing nothing would be the worst outcome here.
        raise AppError(
            "nothing_to_revoke",
            "This workspace is on `legacy` (unlimited, internal). Grant it a paid plan first "
            "if you want it to be revocable.",
            400,
        )

    session.add(
        PlanGrant(
            organization_id=org_id,
            action="revoked",
            from_plan=org.plan,
            to_plan=org.plan,
            expires_at=now,
            note=note,
            actor_id=staff.id,
        )
    )
    # Open cycles stop chasing a client who is no longer served.
    open_cycles = (
        (
            await session.execute(
                select(BillingCycle).where(
                    BillingCycle.organization_id == org_id, BillingCycle.status == "pending"
                )
            )
        )
        .scalars()
        .all()
    )
    for cycle in open_cycles:
        cycle.status = "waived"
        cycle.note = note
        cycle.marked_by = staff.id

    await write_audit(
        session,
        org_id,
        staff.id,
        "billing.plan_revoked",
        target_type="organization",
        target_id=str(org_id),
        meta={"plan": org.plan, "cycles_waived": len(open_cycles)},
        ip=ip,
    )
    await session.commit()
    return await _org_out(session, org_id)


async def add_packs(
    session: AsyncSession,
    org_id: uuid.UUID,
    data: schemas.AddPacksIn,
    *,
    staff: User,
    ip: str | None = None,
) -> schemas.OrgAdminOut:
    """Sell extra conversations (docs/22 §7).

    The client says how many **packs**; the amount and the message count are computed from the
    org's *current* plan and never read from the request, so a caller can never set its own
    price. Packs apply to the current period only — a rollover resets them to zero.
    """
    if data.packs < 1:
        raise AppError("invalid_packs", "Number of packs must be at least 1.", 400)
    note = _require_note(data.note)
    org = await _get_org(session, org_id)

    spec = PLANS.get(org.plan)
    if spec is None or spec.extra_message_pack_size is None or spec.extra_message_pack_usd is None:
        raise AppError(
            "packs_not_sold",
            f"The {org.plan!r} plan has no extra-message pack rate. Grant a paid plan first.",
            400,
        )

    messages = spec.extra_message_pack_size * data.packs
    amount_cents = spec.extra_message_pack_usd * data.packs * 100

    await billing_usage.add_extra_messages(session, org_id, messages)
    session.add(
        PlanGrant(
            organization_id=org_id,
            action="pack_added",
            to_plan=org.plan,
            extra_messages=messages,
            amount_usd_cents=amount_cents,
            note=note,
            actor_id=staff.id,
        )
    )
    await write_audit(
        session,
        org_id,
        staff.id,
        "billing.pack_added",
        target_type="organization",
        target_id=str(org_id),
        meta={"packs": data.packs, "messages": messages, "amount_usd_cents": amount_cents},
        ip=ip,
    )
    await session.commit()
    return await _org_out(session, org_id)


async def list_billing_cycles(
    session: AsyncSession, *, status: str | None = None, limit: int = 200
) -> list[schemas.BillingCycleOut]:
    """The payment ledger. `?status=overdue` is the collections list — who is using Vicero
    without having paid for the current period."""
    now = dt.datetime.now(tz=dt.UTC)
    rows = (
        await session.execute(
            select(BillingCycle, Organization.name)
            .join(Organization, Organization.id == BillingCycle.organization_id)
            .order_by(BillingCycle.period_end.desc())
            .limit(limit)
        )
    ).all()
    out = [_cycle_out(cycle, now, name) for cycle, name in rows]
    if status is None:
        return out
    if status not in ("pending", "paid", "waived", "overdue"):
        raise AppError("invalid_status", f"Unknown cycle status {status!r}.", 400)
    # Filtered on the computed state, not the stored one — `overdue` exists only as a function
    # of the clock, so it cannot be a WHERE clause on `status`.
    return [c for c in out if c.payment_state == status]


async def mark_cycle_paid(
    session: AsyncSession, cycle_id: uuid.UUID, data: schemas.MarkCyclePaidIn, *, staff: User
) -> schemas.BillingCycleOut:
    """Record a payment that arrived outside the app. `renew=True` is the monthly button: it
    also pushes `plan_expires_at` out 30 days and opens the next cycle, in one transaction."""
    try:
        cycle = await billing_cycles.mark_paid(
            session,
            cycle_id,
            method=data.method,
            reference=data.reference,
            note=data.note,
            marked_by=staff.id,
            renew=data.renew,
        )
    except ValueError as exc:
        raise AppError("cycle_not_found", "Billing cycle not found.", 404) from exc
    return _cycle_out(cycle, dt.datetime.now(tz=dt.UTC))


async def waive_cycle(
    session: AsyncSession, cycle_id: uuid.UUID, data: schemas.WaiveCycleIn, *, staff: User
) -> schemas.BillingCycleOut:
    """Comps, demos and your own workspaces — so a free org never sits in the overdue list."""
    note = _require_note(data.note)
    try:
        cycle = await billing_cycles.waive(session, cycle_id, note=note, marked_by=staff.id)
    except ValueError as exc:
        raise AppError("cycle_not_found", "Billing cycle not found.", 404) from exc
    return _cycle_out(cycle, dt.datetime.now(tz=dt.UTC))


async def list_uninvoiced_packs(
    session: AsyncSession, *, invoiced: bool = False, limit: int = 200
) -> list[schemas.PackOut]:
    """Packs sold but not yet reflected in an invoice — the monthly reconciliation view."""
    rows = (
        await session.execute(
            select(PlanGrant, Organization.name)
            .join(Organization, Organization.id == PlanGrant.organization_id)
            .where(PlanGrant.action == "pack_added", PlanGrant.invoiced.is_(invoiced))
            .order_by(PlanGrant.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        schemas.PackOut(
            id=g.id,
            organization_id=g.organization_id,
            organization_name=name,
            extra_messages=g.extra_messages,
            amount_usd_cents=g.amount_usd_cents,
            note=g.note,
            invoiced=bool(g.invoiced),
            created_at=g.created_at,
        )
        for g, name in rows
    ]


async def mark_pack_invoiced(
    session: AsyncSession, pack_id: uuid.UUID, data: schemas.PackInvoicedIn
) -> schemas.PackOut:
    """The one field on an otherwise append-only table that may change: whether you have
    actually billed this pack. The pack itself — what was sold, when, for how much — never does.
    """
    grant = await session.get(PlanGrant, pack_id)
    if grant is None or grant.action != "pack_added":
        raise AppError("pack_not_found", "Pack not found.", 404)
    grant.invoiced = data.invoiced
    await session.commit()
    name = await session.scalar(
        select(Organization.name).where(Organization.id == grant.organization_id)
    )
    return schemas.PackOut(
        id=grant.id,
        organization_id=grant.organization_id,
        organization_name=name,
        extra_messages=grant.extra_messages,
        amount_usd_cents=grant.amount_usd_cents,
        note=grant.note,
        invoiced=bool(grant.invoiced),
        created_at=grant.created_at,
    )
