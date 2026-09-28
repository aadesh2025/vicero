"""Platform-staff admin console routes under /v1/admin.

Every endpoint is gated by `require_staff` (user.is_staff). These routes are
deliberately org-agnostic — staff span all tenants, so there is no X-Org-Id.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import User
from app.modules.admin import schemas, service
from app.modules.admin.deps import require_staff

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/orgs", response_model=list[schemas.OrgAdminOut])
async def list_orgs(
    include_deleted: bool = False,
    q: str | None = None,
    plan: str | None = None,
    status: str | None = None,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.OrgAdminOut]:
    """Live organizations. `?include_deleted=true` for the audit view.

    `?q=` searches name and owner email. `?status=` is the panel's filter chips:
    `at_limit` (sell a pack) · `near_limit` (warn them first) · `expiring` (chase the renewal)
    · `expired` · `payment_pending` · `overdue` (the collections list).
    """
    return await service.list_orgs(
        session, include_deleted=include_deleted, q=q, plan=plan, status_filter=status
    )


@router.get("/users", response_model=list[schemas.UserAdminOut])
async def list_users(
    include_system: bool = False,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.UserAdminOut]:
    """Human accounts. `?include_system=true` also returns machine logins."""
    return await service.list_users(session, include_system=include_system)


@router.get("/usage", response_model=schemas.PlatformUsageOut)
async def platform_usage(
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.PlatformUsageOut:
    return await service.platform_usage(session)


@router.get("/health", response_model=schemas.HealthOut)
async def health(
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.HealthOut:
    return await service.health(session)


@router.get("/automations", response_model=schemas.AutomationsOverviewOut)
async def automations(
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.AutomationsOverviewOut:
    """Every n8n workflow across every tenant, with its tag-derived owner and bindings."""
    return await service.automations_overview(session)


@router.get("/n8n-signature-audit", response_model=schemas.N8nSignatureAuditOut)
async def n8n_signature_audit(
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.N8nSignatureAuditOut:
    """Which bound n8n tools point at a workflow that would accept an unsigned call (R15)."""
    return await service.n8n_signature_audit(session)


@router.get("/feature-flags", response_model=list[schemas.FeatureFlagOut])
async def list_flags(
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.FeatureFlagOut]:
    return await service.list_flags(session)


@router.put("/feature-flags/{key}", response_model=schemas.FeatureFlagOut)
async def upsert_flag(
    key: str,
    data: schemas.FeatureFlagUpdate,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.FeatureFlagOut:
    return await service.upsert_flag(session, key, data)


# ── billing: grant / revoke / packs / payment ledger (docs/22 §8.2) ─────────────
# Staff-only, like every route above — `require_staff` is the real boundary; the frontend
# redirect is convenience. There is no self-serve path to any of this.
@router.get("/orgs/{org_id}", response_model=schemas.OrgAdminDetailOut)
async def get_org(
    org_id: uuid.UUID,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.OrgAdminDetailOut:
    """One workspace with its grant history and payment ledger — the dispute view."""
    return await service.get_org_detail(session, org_id)


@router.post("/orgs/{org_id}/plan", response_model=schemas.OrgAdminOut)
async def grant_plan(
    org_id: uuid.UUID,
    data: schemas.GrantPlanIn,
    request: Request,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.OrgAdminOut:
    """Put a workspace on a plan. Live on the client's next request — no restart, no cache."""
    return await service.grant_plan(
        session, org_id, data, staff=staff, ip=request.client.host if request.client else None
    )


@router.delete("/orgs/{org_id}/plan", response_model=schemas.OrgAdminOut)
async def revoke_plan(
    org_id: uuid.UUID,
    data: schemas.RevokePlanIn,
    request: Request,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.OrgAdminOut:
    """Expire the plan. Keeps every agent, document and conversation — read-only, not deleted."""
    return await service.revoke_plan(
        session, org_id, data, staff=staff, ip=request.client.host if request.client else None
    )


@router.post("/orgs/{org_id}/messages", response_model=schemas.OrgAdminOut)
async def add_packs(
    org_id: uuid.UUID,
    data: schemas.AddPacksIn,
    request: Request,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.OrgAdminOut:
    """Sell extra conversations. The price comes from the org's plan, never from the request."""
    return await service.add_packs(
        session, org_id, data, staff=staff, ip=request.client.host if request.client else None
    )


@router.get("/billing/cycles", response_model=list[schemas.BillingCycleOut])
async def list_billing_cycles(
    status: str | None = None,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.BillingCycleOut]:
    """The payment ledger. `?status=overdue` is the collections list."""
    return await service.list_billing_cycles(session, status=status)


@router.post("/billing/cycles/{cycle_id}/paid", response_model=schemas.BillingCycleOut)
async def mark_cycle_paid(
    cycle_id: uuid.UUID,
    data: schemas.MarkCyclePaidIn,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.BillingCycleOut:
    """Record a payment. `renew: true` also extends the plan 30 days and opens the next cycle."""
    return await service.mark_cycle_paid(session, cycle_id, data, staff=staff)


@router.post("/billing/cycles/{cycle_id}/waive", response_model=schemas.BillingCycleOut)
async def waive_cycle(
    cycle_id: uuid.UUID,
    data: schemas.WaiveCycleIn,
    staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.BillingCycleOut:
    """Comps, demos and your own workspaces — keeps them out of the overdue list."""
    return await service.waive_cycle(session, cycle_id, data, staff=staff)


@router.get("/billing/packs", response_model=list[schemas.PackOut])
async def list_packs(
    invoiced: bool = False,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.PackOut]:
    """Packs sold but not yet invoiced — the monthly reconciliation view."""
    return await service.list_uninvoiced_packs(session, invoiced=invoiced)


@router.patch("/billing/packs/{pack_id}", response_model=schemas.PackOut)
async def mark_pack_invoiced(
    pack_id: uuid.UUID,
    data: schemas.PackInvoicedIn,
    _staff: User = Depends(require_staff),
    session: AsyncSession = Depends(get_session),
) -> schemas.PackOut:
    """The one mutable field on an append-only table: whether you have billed this pack yet."""
    return await service.mark_pack_invoiced(session, pack_id, data)
