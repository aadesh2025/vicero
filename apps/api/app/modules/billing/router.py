"""Billing routes under /v1/billing (docs/22 §8.1).

`app/modules/billing/` is the HTTP surface; `app/billing/` is the internal metering package
(counters, gates). Same word, different jobs — this module never counts anything.

`GET /v1/billing/plans` is deliberately **unauthenticated**: it is what the public pricing page
renders, and it exposes nothing but the price list that is already public. `GET /v1/me/
entitlements` is authenticated (any org member) — the frontend's single source of truth for
what the signed-in org may do right now.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.billing import schemas, service
from app.modules.orgs.deps import OrgContext, current_org

router = APIRouter(tags=["billing"])


@router.get("/v1/billing/plans", response_model=schemas.PricingOut)
async def list_plans() -> schemas.PricingOut:
    """The pricing table, rendered from `app.core.plans.PLANS`.

    The marketing page must call this rather than hardcoding numbers, so a plan change is one
    edit in `plans.py` and never a stale price on a public page.
    """
    return service.pricing_table()


@router.get("/v1/me/entitlements", response_model=schemas.EntitlementsOut)
async def get_entitlements(
    ctx: OrgContext = Depends(current_org),
    session: AsyncSession = Depends(get_session),
) -> schemas.EntitlementsOut:
    """What the caller's active org (`X-Org-Id`) may do right now, plus its current usage.

    For rendering, not security — every limit is re-enforced server-side at the point of
    mutation (docs/22 §8.1). Carries no payment information; that is staff-only (docs/22 §5.1).
    """
    return await service.entitlements(session, ctx)
