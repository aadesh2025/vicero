"""Automations routes (docs/26).

* `/v1/automations`            tenant, read-only (plus "request an automation"); org-scoped, wrong org -> 404.
* `/v1/admin/automation-*`     platform staff only.
* `/internal/automations/runs` n8n -> Vicero, HMAC-signed. Not part of the public API surface.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import limiter, rate_limit
from app.db.session import get_session
from app.models import User
from app.modules.admin.deps import require_staff
from app.modules.automations import schemas, service
from app.modules.orgs.deps import OrgContext, current_org

log = get_logger("automations")

router = APIRouter(prefix="/v1/automations", tags=["automations"])
staff_router = APIRouter(prefix="/v1/admin", tags=["admin"])
internal_router = APIRouter(prefix="/internal/automations", tags=["internal"], include_in_schema=False)

RunStatus = Literal["success", "failed", "running"]


# ── tenant (static paths first, so they never collide with /{automation_id}) ──────────
@router.get("", response_model=list[schemas.AutomationCard])
async def list_automations(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> list[schemas.AutomationCard]:
    return await service.list_automations(session, ctx)


@router.get("/usage", response_model=schemas.UsageOut)
async def usage_summary(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> schemas.UsageOut:
    return await service.get_usage(session, ctx)


@router.get("/requests", response_model=list[schemas.RequestOut])
async def list_requests(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> list[schemas.RequestOut]:
    return await service.list_requests(session, ctx)


@router.post("/requests", response_model=schemas.RequestOut, status_code=201)
async def create_request(
    data: schemas.CreateRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.RequestOut:
    return await service.create_request(session, ctx, data)


@router.get("/{automation_id}", response_model=schemas.AutomationDetail)
async def get_automation(
    automation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.AutomationDetail:
    return await service.get_automation(session, ctx, automation_id)


@router.get("/{automation_id}/runs", response_model=schemas.RunsPage)
async def list_runs(
    automation_id: uuid.UUID,
    status: RunStatus | None = None,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.RunsPage:
    return await service.list_runs(session, ctx, automation_id, status=status, page=page, page_size=page_size)


# ── staff ─────────────────────────────────────────────────────────────────────────
@staff_router.get("/automation-requests", response_model=list[schemas.StaffRequestOut])
async def staff_list_requests(
    status: str | None = None,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.StaffRequestOut]:
    return await service.staff_list_requests(session, status)


@staff_router.patch("/automation-requests/{request_id}", response_model=schemas.RequestOut)
async def staff_update_request(
    request_id: uuid.UUID,
    data: schemas.StaffRequestUpdate,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.RequestOut:
    return await service.staff_update_request(session, staff, request_id, data)


@staff_router.get("/automation-registry", response_model=list[schemas.StaffAutomationOut])
async def staff_list_automations(
    organization_id: uuid.UUID | None = None,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.StaffAutomationOut]:
    return await service.staff_list_automations(session, organization_id)


@staff_router.post("/automation-registry", response_model=schemas.StaffAutomationOut, status_code=201)
async def staff_register(
    data: schemas.RegisterAutomation,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.StaffAutomationOut:
    return await service.staff_register(session, staff, data)


@staff_router.patch("/automation-registry/{automation_id}", response_model=schemas.StaffAutomationOut)
async def staff_update(
    automation_id: uuid.UUID,
    data: schemas.UpdateAutomation,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.StaffAutomationOut:
    return await service.staff_update(session, staff, automation_id, data)


@staff_router.get("/automation-registry/{automation_id}/runs", response_model=schemas.StaffRunsPage)
async def staff_runs(
    automation_id: uuid.UUID,
    status: RunStatus | None = None,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(25, ge=1, le=100),
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.StaffRunsPage:
    return await service.staff_runs(session, automation_id, status=status, page=page, page_size=page_size)


# ── ingestion: n8n -> Vicero ──────────────────────────────────────────────────────
MAX_REPORT_BYTES = 32 * 1024
REPORT_WINDOW_SECONDS = 300


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


@internal_router.post(
    "/runs",
    response_model=schemas.ReportAck,
    dependencies=[Depends(rate_limit("automation_report", limit=300, window=60))],
)
async def report_run(request: Request, session: AsyncSession = Depends(get_session)) -> schemas.ReportAck:
    secret = (settings.automation_report_secret or "").strip()
    if not secret:
        # Fail closed: no secret configured means nothing can be verified, so nothing is recorded.
        raise AppError("automations.not_configured", "Run reporting is not configured.", 503)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REPORT_BYTES:
        raise AppError("automations.too_large", "Report too large.", 413)
    body = await request.body()
    if len(body) > MAX_REPORT_BYTES:
        raise AppError("automations.too_large", "Report too large.", 413)

    signature = request.headers.get("X-Vicero-Signature") or ""
    timestamp = request.headers.get("X-Vicero-Timestamp") or ""
    try:
        fresh = abs(int(time.time()) - int(timestamp)) <= REPORT_WINDOW_SECONDS
    except ValueError:
        fresh = False
    if not fresh or not hmac.compare_digest(_sign(secret, timestamp, body), signature):
        log.warning("automation_report_bad_signature", fresh=fresh)
        raise AppError("automations.bad_signature", "Invalid signature.", 401)

    # A signed message is accepted once: the exact same signature again inside the window is a replay.
    first_time, _ = await limiter.hit(f"automation_report_sig:{signature}", 1, REPORT_WINDOW_SECONDS * 2)
    if not first_time:
        log.warning("automation_report_replay")
        raise AppError("automations.replay", "Message already received.", 409)

    try:
        report = schemas.RunReport.model_validate(json.loads(body))
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise AppError("automations.bad_report", f"Invalid report (fields: {', '.join(fields)}).", 422) from exc
    except ValueError as exc:
        raise AppError("automations.bad_report", "Invalid report (not JSON).", 422) from exc
    return await service.ingest_report(session, report)
