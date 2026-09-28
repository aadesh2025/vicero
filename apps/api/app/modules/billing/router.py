"""Billing routes under /v1/billing (docs/22 §8.1).

`app/modules/billing/` is the HTTP surface; `app/billing/` is the internal metering package
(counters, gates). Same word, different jobs — this module never counts anything.

`GET /v1/billing/plans` is deliberately **unauthenticated**: it is what the public pricing page
renders, and it exposes nothing but the price list that is already public. Every other billing
route added later (entitlements, admin grants) is authenticated.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.billing import schemas, service

router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.get("/plans", response_model=schemas.PricingOut)
async def list_plans() -> schemas.PricingOut:
    """The pricing table, rendered from `app.core.plans.PLANS`.

    The marketing page must call this rather than hardcoding numbers, so a plan change is one
    edit in `plans.py` and never a stale price on a public page.
    """
    return service.pricing_table()
