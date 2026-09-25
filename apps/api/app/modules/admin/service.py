"""Admin console service: cross-tenant aggregates (no org scoping — staff only)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import guard_models
from app.core.config import settings
from app.core.errors import AppError
from app.core.probes import check_database, check_redis
from app.integrations.n8n_client import N8nClient
from app.integrations.n8n_client import get_client as get_n8n_client
from app.integrations.n8n_signature import FIX_HINT, unverified_reason
from app.models import (
    Agent,
    AgentVersion,
    Conversation,
    FeatureFlag,
    Membership,
    Message,
    Organization,
    Tool,
    UsageRecord,
    User,
    Workflow,
    WorkflowVersion,
)
from app.modules.admin import schemas
from app.tools.service import INTERNAL_TAGS, SHARED_TEMPLATE_TAG


async def list_orgs(
    session: AsyncSession, limit: int = 100, *, include_deleted: bool = False
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
    # automation (see `AutomationOut`); this is BotForge's own visual workflow builder entity.
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

    return [
        schemas.OrgAdminOut(
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
        )
        for org, m, a, p, wr in rows
    ]


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
        # `provision@botforge.dev` is a working credential, not a person. Deleting it to tidy
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
        # The URL is what BotForge actually calls, so it decides — same rule as the bind check.
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
