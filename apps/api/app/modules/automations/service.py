"""Client-visible automations: tenant reads, staff registry, run ingestion, caps, retention (docs/26).

Tenant rule: EVERY query here filters on `organization_id`, and a row that exists but belongs to another org is
indistinguishable from one that does not exist (404). n8n is never contacted on behalf of a tenant.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.billing.automations import RUN_LIMIT_MESSAGE, check_agent_call, month_start, runs_this_month
from app.core import rbac
from app.core.audit import write_audit
from app.core.crypto import encrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Agent, Automation, AutomationRequest, AutomationRun, Organization, User
from app.modules.automations import sanitize, schemas
from app.modules.orgs.deps import OrgContext

log = get_logger("automations")

RETENTION_DAYS = 30
_NOT_FOUND = ("automations.not_found", "Automation not found.", 404)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _not_found() -> AppError:
    return AppError(*_NOT_FOUND)


def _run_out(run: AutomationRun) -> schemas.RunOut:
    if run.status == "failed":
        message = run.error_summary or sanitize.friendly_error("failed") or "Failed"
    elif run.status == "running":
        message = "Running"
    else:
        message = "Completed"
    return schemas.RunOut(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        message=message,
        over_cap=run.over_cap,
    )


# ── scoped lookups: the only way a tenant reaches a row ─────────────────────────────
async def _get_automation(session: AsyncSession, org_id: uuid.UUID, automation_id: uuid.UUID) -> Automation:
    row = (
        await session.execute(
            select(Automation).where(Automation.id == automation_id, Automation.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise _not_found()
    return row


async def _cards(
    session: AsyncSession, org_id: uuid.UUID, automation_ids: list[uuid.UUID] | None = None
) -> list[schemas.AutomationCard]:
    stmt = (
        select(Automation, Agent.name)
        .outerjoin(Agent, Agent.id == Automation.agent_id)
        .where(Automation.organization_id == org_id)
    )
    if automation_ids is not None:
        stmt = stmt.where(Automation.id.in_(automation_ids))
    rows = (await session.execute(stmt.order_by(Automation.created_at.desc()))).all()
    if not rows:
        return []
    ids = [a.id for a, _ in rows]
    start = month_start()
    agg = (
        await session.execute(
            select(
                AutomationRun.automation_id,
                func.count().label("n"),
                func.sum(case((AutomationRun.status == "success", 1), else_=0)).label("ok"),
                func.sum(case((AutomationRun.status == "failed", 1), else_=0)).label("bad"),
            )
            .where(
                AutomationRun.organization_id == org_id,
                AutomationRun.automation_id.in_(ids),
                AutomationRun.started_at >= start,
            )
            .group_by(AutomationRun.automation_id)
        )
    ).all()
    month = {r.automation_id: (int(r.n), int(r.ok or 0), int(r.bad or 0)) for r in agg}
    latest = (
        await session.execute(
            select(AutomationRun.automation_id, func.max(AutomationRun.started_at))
            .where(AutomationRun.organization_id == org_id, AutomationRun.automation_id.in_(ids))
            .group_by(AutomationRun.automation_id)
        )
    ).all()
    last_at = {aid: ts for aid, ts in latest}
    last_status: dict[uuid.UUID, str] = {}
    for aid, ts in last_at.items():
        status = (
            (
                await session.execute(
                    select(AutomationRun.status).where(
                        AutomationRun.organization_id == org_id,
                        AutomationRun.automation_id == aid,
                        AutomationRun.started_at == ts,
                    )
                )
            )
            .scalars()
            .first()
        )
        if status:
            last_status[aid] = status
    cards = []
    for automation, agent_name in rows:
        n, ok, bad = month.get(automation.id, (0, 0, 0))
        finished = ok + bad
        cards.append(
            schemas.AutomationCard(
                id=automation.id,
                name=automation.name,
                status=automation.status,
                agent_id=automation.agent_id,
                agent_name=agent_name,
                last_run_at=last_at.get(automation.id),
                last_status=last_status.get(automation.id),
                runs_this_month=n,
                success_rate=(ok / finished) if finished else None,
            )
        )
    return cards


# ── tenant: read ──────────────────────────────────────────────────────────────────
async def list_automations(session: AsyncSession, ctx: OrgContext) -> list[schemas.AutomationCard]:
    rbac.require_permission(ctx.role, rbac.READ)
    return await _cards(session, ctx.org.id)


async def get_automation(session: AsyncSession, ctx: OrgContext, automation_id: uuid.UUID) -> schemas.AutomationDetail:
    rbac.require_permission(ctx.role, rbac.READ)
    automation = await _get_automation(session, ctx.org.id, automation_id)
    (card,) = await _cards(session, ctx.org.id, [automation.id])
    base = (AutomationRun.organization_id == ctx.org.id, AutomationRun.automation_id == automation.id)
    total = int((await session.execute(select(func.count()).select_from(AutomationRun).where(*base))).scalar_one())
    start = month_start()
    counts = (
        await session.execute(
            select(
                func.sum(case((AutomationRun.status == "success", 1), else_=0)),
                func.sum(case((AutomationRun.status == "failed", 1), else_=0)),
            ).where(*base, AutomationRun.started_at >= start)
        )
    ).one()
    last = (
        await session.execute(select(AutomationRun).where(*base).order_by(AutomationRun.started_at.desc()).limit(1))
    ).scalar_one_or_none()
    failure = (
        await session.execute(
            select(AutomationRun)
            .where(*base, AutomationRun.status == "failed")
            .order_by(AutomationRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return schemas.AutomationDetail(
        **card.model_dump(),
        runs_total=total,
        success_this_month=int(counts[0] or 0),
        failed_this_month=int(counts[1] or 0),
        last_run=_run_out(last) if last else None,
        last_failure=_run_out(failure) if failure else None,
    )


async def list_runs(
    session: AsyncSession,
    ctx: OrgContext,
    automation_id: uuid.UUID,
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> schemas.RunsPage:
    rbac.require_permission(ctx.role, rbac.READ)
    automation = await _get_automation(session, ctx.org.id, automation_id)
    where = [AutomationRun.organization_id == ctx.org.id, AutomationRun.automation_id == automation.id]
    if status:
        where.append(AutomationRun.status == status)
    total = int((await session.execute(select(func.count()).select_from(AutomationRun).where(*where))).scalar_one())
    rows = (
        (
            await session.execute(
                select(AutomationRun)
                .where(*where)
                .order_by(AutomationRun.started_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return schemas.RunsPage(items=[_run_out(r) for r in rows], total=total, page=page, page_size=page_size)


async def get_usage(session: AsyncSession, ctx: OrgContext) -> schemas.UsageOut:
    rbac.require_permission(ctx.role, rbac.READ)
    ent = await usage.load_entitlements(session, ctx.org)
    limit = ent.limit_for("automations")
    live = int(
        (
            await session.execute(
                select(func.count()).select_from(Automation).where(Automation.organization_id == ctx.org.id)
            )
        ).scalar_one()
    )
    queued = int(
        (
            await session.execute(
                select(func.count())
                .select_from(AutomationRequest)
                .where(
                    AutomationRequest.organization_id == ctx.org.id,
                    AutomationRequest.status.in_(("requested", "building")),
                )
            )
        ).scalar_one()
    )
    included = limit != 0 and not ent.is_expired
    return schemas.UsageOut(
        plan=ctx.org.plan,
        automations_limit=limit,
        automations_used=live + queued,
        runs_limit=ent.spec.max_automation_runs,
        runs_this_month=await runs_this_month(session, ctx.org.id),
        included=included,
        can_request=included and (limit is None or live + queued < limit),
    )


# ── tenant: request ───────────────────────────────────────────────────────────────
def _request_out(row: AutomationRequest) -> schemas.RequestOut:
    return schemas.RequestOut(
        id=row.id,
        description=row.description,
        agent_id=row.agent_id,
        status=row.status,
        staff_note=row.staff_note,
        created_at=row.created_at,
    )


async def list_requests(session: AsyncSession, ctx: OrgContext) -> list[schemas.RequestOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    rows = (
        (
            await session.execute(
                select(AutomationRequest)
                .where(AutomationRequest.organization_id == ctx.org.id)
                .order_by(AutomationRequest.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_request_out(r) for r in rows]


async def create_request(session: AsyncSession, ctx: OrgContext, data: schemas.CreateRequest) -> schemas.RequestOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_automation_slot(session, ctx.org)
    if data.agent_id is not None:
        agent = (
            await session.execute(
                select(Agent.id).where(
                    Agent.id == data.agent_id, Agent.organization_id == ctx.org.id, Agent.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if agent is None:
            raise AppError("agents.not_found", "Agent not found.", 404)
    row = AutomationRequest(
        organization_id=ctx.org.id,
        agent_id=data.agent_id,
        requested_by=ctx.user.id,
        description=data.description.strip(),
        status="requested",
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "automation.requested",
        target_type="automation_request",
        target_id=str(row.id),
    )
    return _request_out(row)


# ── staff ─────────────────────────────────────────────────────────────────────────
async def staff_list_requests(session: AsyncSession, status: str | None) -> list[schemas.StaffRequestOut]:
    stmt = (
        select(AutomationRequest, Organization.name, User.email)
        .join(Organization, Organization.id == AutomationRequest.organization_id)
        .outerjoin(User, User.id == AutomationRequest.requested_by)
        .order_by(AutomationRequest.created_at.desc())
        .limit(500)
    )
    if status:
        stmt = stmt.where(AutomationRequest.status == status)
    out = []
    for req, org_name, email in (await session.execute(stmt)).all():
        out.append(
            schemas.StaffRequestOut(
                **_request_out(req).model_dump(),
                organization_id=req.organization_id,
                organization_name=org_name,
                requested_by_email=email,
            )
        )
    return out


async def staff_update_request(
    session: AsyncSession, staff: User, request_id: uuid.UUID, data: schemas.StaffRequestUpdate
) -> schemas.RequestOut:
    req = await session.get(AutomationRequest, request_id)
    if req is None:
        raise AppError("automations.request_not_found", "Request not found.", 404)
    req.status = data.status
    req.staff_note = data.staff_note
    await write_audit(
        session,
        req.organization_id,
        staff.id,
        "automation.request_updated",
        target_type="automation_request",
        target_id=str(req.id),
        meta={"status": data.status},
    )
    return _request_out(req)


async def _staff_card(session: AsyncSession, automation: Automation) -> schemas.StaffAutomationOut:
    (card,) = await _cards(session, automation.organization_id, [automation.id])
    org = await session.get(Organization, automation.organization_id)
    return schemas.StaffAutomationOut(
        **card.model_dump(),
        organization_id=automation.organization_id,
        organization_name=org.name if org else "",
        n8n_workflow_id=automation.n8n_workflow_id,
        webhook_path=automation.webhook_path,
        has_config=bool(automation.config_encrypted),
    )


async def staff_register(
    session: AsyncSession, staff: User, data: schemas.RegisterAutomation
) -> schemas.StaffAutomationOut:
    org = await session.get(Organization, data.organization_id)
    if org is None:
        raise AppError("org.not_found", "Organization not found.", 404)
    if data.agent_id is not None:
        owns = (
            await session.execute(select(Agent.id).where(Agent.id == data.agent_id, Agent.organization_id == org.id))
        ).scalar_one_or_none()
        if owns is None:
            raise AppError("agents.not_found", "That agent does not belong to this organization.", 404)
    request: AutomationRequest | None = None
    if data.request_id is not None:
        request = await session.get(AutomationRequest, data.request_id)
        if request is None or request.organization_id != org.id:
            raise AppError("automations.request_not_found", "Request not found.", 404)
    automation = Automation(
        organization_id=org.id,
        agent_id=data.agent_id,
        request_id=data.request_id,
        n8n_workflow_id=data.n8n_workflow_id,
        webhook_path=data.webhook_path,
        name=data.name,
        status="active",
        config_encrypted=encrypt(json.dumps(data.config)) if data.config else None,
    )
    session.add(automation)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        raise AppError(
            "automations.workflow_registered", "That n8n workflow is already registered to an automation.", 409
        ) from None
    if request is not None:
        request.status = "active"
    await write_audit(
        session,
        org.id,
        staff.id,
        "automation.registered",
        target_type="automation",
        target_id=str(automation.id),
        meta={"workflow": data.n8n_workflow_id},
    )
    return await _staff_card(session, automation)


async def staff_update(
    session: AsyncSession, staff: User, automation_id: uuid.UUID, data: schemas.UpdateAutomation
) -> schemas.StaffAutomationOut:
    automation = await session.get(Automation, automation_id)
    if automation is None:
        raise _not_found()
    if data.status is not None:
        automation.status = data.status
    if data.name is not None:
        automation.name = data.name
    if data.webhook_path is not None:
        automation.webhook_path = data.webhook_path
    if data.config is not None:
        automation.config_encrypted = encrypt(json.dumps(data.config))
    await write_audit(
        session,
        automation.organization_id,
        staff.id,
        "automation.updated",
        target_type="automation",
        target_id=str(automation.id),
        meta={"status": data.status},
    )
    return await _staff_card(session, automation)


async def staff_list_automations(
    session: AsyncSession, organization_id: uuid.UUID | None
) -> list[schemas.StaffAutomationOut]:
    stmt = select(Automation).order_by(Automation.created_at.desc()).limit(500)
    if organization_id is not None:
        stmt = stmt.where(Automation.organization_id == organization_id)
    return [await _staff_card(session, a) for a in (await session.execute(stmt)).scalars().all()]


async def staff_runs(
    session: AsyncSession, automation_id: uuid.UUID, *, status: str | None, page: int, page_size: int
) -> schemas.StaffRunsPage:
    automation = await session.get(Automation, automation_id)
    if automation is None:
        raise _not_found()
    where = [AutomationRun.automation_id == automation.id]
    if status:
        where.append(AutomationRun.status == status)
    total = int((await session.execute(select(func.count()).select_from(AutomationRun).where(*where))).scalar_one())
    rows = (
        (
            await session.execute(
                select(AutomationRun)
                .where(*where)
                .order_by(AutomationRun.started_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        schemas.StaffRunOut(
            **_run_out(r).model_dump(),
            n8n_execution_id=r.n8n_execution_id,
            input_summary=r.input_summary,
            output_summary=r.output_summary,
        )
        for r in rows
    ]
    return schemas.StaffRunsPage(items=items, total=total, page=page, page_size=page_size)


# ── ingestion ─────────────────────────────────────────────────────────────────────
def _reject(reason: str, **fields: Any) -> AppError:
    """One generic answer for every refusal (the sender learns nothing about which workflows exist)."""
    log.warning("automation_report_rejected", reason=reason, **fields)
    return AppError("automations.report_rejected", "Report rejected.", 404)


async def record_run(
    session: AsyncSession,
    *,
    automation: Automation,
    execution_id: str,
    status: str,
    started_at: dt.datetime,
    finished_at: dt.datetime | None,
    raw_error: str | None,
    input_value: Any = None,
    output_value: Any = None,
    update_existing: bool = True,
) -> tuple[AutomationRun, bool]:
    """Store one run for an already-verified automation. Idempotent on (automation, execution id).

    Returns (row, created). An existing row is updated (a "running" report followed by its final status), never
    duplicated. A NEW run recorded at or past the plan's monthly run limit is kept and flagged `over_cap`.
    """
    error_summary = sanitize.friendly_error(raw_error) if status == "failed" else None
    duration = int((finished_at - started_at).total_seconds() * 1000) if finished_at else None
    if duration is not None and duration < 0:
        duration = None
    existing = (
        await session.execute(
            select(AutomationRun).where(
                AutomationRun.automation_id == automation.id, AutomationRun.n8n_execution_id == execution_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not update_existing:
            return existing, False
        return _update_run(existing, status, finished_at, duration, error_summary, input_value, output_value), False

    org = await session.get(Organization, automation.organization_id)
    cap: int | None = None
    if org is not None:
        cap = (await usage.load_entitlements(session, org)).spec.max_automation_runs
    used = await runs_this_month(session, automation.organization_id)
    run = AutomationRun(
        organization_id=automation.organization_id,
        automation_id=automation.id,
        n8n_execution_id=execution_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration,
        error_summary=error_summary,
        input_summary=sanitize.summarize(input_value),
        output_summary=sanitize.summarize(output_value),
        over_cap=cap is not None and used >= cap,
    )
    try:
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError:
        # A concurrent report for the same execution won the insert: treat ours as the update.
        existing = (
            await session.execute(
                select(AutomationRun).where(
                    AutomationRun.automation_id == automation.id, AutomationRun.n8n_execution_id == execution_id
                )
            )
        ).scalar_one()
        if not update_existing:
            return existing, False
        return _update_run(existing, status, finished_at, duration, error_summary, input_value, output_value), False
    return run, True


def _update_run(
    run: AutomationRun,
    status: str,
    finished_at: dt.datetime | None,
    duration: int | None,
    error_summary: str | None,
    input_value: Any,
    output_value: Any,
) -> AutomationRun:
    # A final status is never walked back to "running" by a late or replayed report.
    if run.status == "running" or status != "running":
        run.status = status
        run.finished_at = finished_at or run.finished_at
        run.duration_ms = duration if duration is not None else run.duration_ms
        run.error_summary = error_summary if status == "failed" else run.error_summary
        run.output_summary = sanitize.summarize(output_value) or run.output_summary
        run.input_summary = run.input_summary or sanitize.summarize(input_value)
    return run


async def ingest_report(session: AsyncSession, report: schemas.RunReport) -> schemas.ReportAck:
    """Handle a signed push from n8n. The signature is already verified; the CLAIMS are checked here."""
    automation = (
        await session.execute(select(Automation).where(Automation.n8n_workflow_id == report.workflow_id))
    ).scalar_one_or_none()
    if automation is None:
        raise _reject("unregistered_workflow", workflow=report.workflow_id)
    if automation.organization_id != report.org_id:
        # The payload names an org that does not own this workflow. Security event; nothing is written.
        raise _reject(
            "org_mismatch",
            workflow=report.workflow_id,
            claimed_org=str(report.org_id),
            owner_org=str(automation.organization_id),
        )
    _, created = await record_run(
        session,
        automation=automation,
        execution_id=report.execution_id,
        status=report.status,
        started_at=report.started_at,
        finished_at=report.finished_at,
        raw_error=report.error,
        input_value=report.input,
        output_value=report.output,
    )
    return schemas.ReportAck(created=created)


# ── retention ─────────────────────────────────────────────────────────────────────
async def purge_old_runs(session: AsyncSession, now: dt.datetime | None = None) -> int:
    cutoff = (now or _now()) - dt.timedelta(days=RETENTION_DAYS)
    result = await session.execute(delete(AutomationRun).where(AutomationRun.started_at < cutoff))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ── pull (backup for the push) ─────────────────────────────────────────────────────
_N8N_STATUS = {
    "success": "success",
    "error": "failed",
    "crashed": "failed",
    "failed": "failed",
    "canceled": "failed",
    "cancelled": "failed",
    "running": "running",
    "new": "running",
    "waiting": "running",
}


def _parse_ts(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


async def sync_from_n8n(session: AsyncSession, client: Any, per_workflow: int = 20) -> int:
    """Fill gaps in the run log from n8n's own execution list. Returns how many runs were ADDED.

    Only REGISTERED workflows are asked about (the loop is over our own table), the org always comes from the
    registry row, and a run already reported by the push is left exactly as the push recorded it.
    """
    added = 0
    automations = (await session.execute(select(Automation))).scalars().all()
    for automation in automations:
        try:
            executions = await client.list_executions(automation.n8n_workflow_id, per_workflow)
        except AppError as exc:
            log.warning("automation_pull_failed", workflow=automation.n8n_workflow_id, error=exc.message)
            continue
        for ex in executions:
            started = _parse_ts(ex.get("startedAt"))
            status = _N8N_STATUS.get(str(ex.get("status", "")).lower())
            if started is None or status is None or ex.get("id") is None:
                continue
            _, created = await record_run(
                session,
                automation=automation,
                execution_id=str(ex["id"]),
                status=status,
                started_at=started,
                finished_at=_parse_ts(ex.get("stoppedAt")),
                raw_error="error" if status == "failed" else None,
                update_existing=False,
            )
            added += int(created)
    return added


__all__ = ["RUN_LIMIT_MESSAGE", "check_agent_call", "month_start", "runs_this_month"]
