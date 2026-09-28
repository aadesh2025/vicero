"""Pricing table rendering (docs/22 §8.1). Pure — no database, no request context.

Kept separate from the router so the mapping from `PLANS` to the wire format can be unit-tested
without an app, and so the same function can feed the admin panel's plan picker later.
"""

from __future__ import annotations

from app.core.plans import FEATURES, PAID_PLANS, PLANS, PlanSpec
from app.modules.billing import schemas


def _title(plan_id: str) -> str:
    """`business` → `Business`. Derived, so a display name can never drift from the plan key."""
    return plan_id.replace("_", " ").title()


def _limits(spec: PlanSpec) -> schemas.PlanLimitsOut:
    return schemas.PlanLimitsOut(
        workspaces=spec.max_workspaces,
        agents=spec.max_agents,
        messages=spec.max_messages,
        knowledge_bases=spec.max_knowledge_bases,
        documents=spec.max_documents,
        storage_bytes=spec.storage_bytes,
        workflows=spec.max_workflows,
        tools=spec.max_tools,
        webhooks=spec.max_webhooks,
        team_members=spec.max_team_members,
    )


def _features(spec: PlanSpec) -> schemas.PlanFeaturesOut:
    # Read through FEATURES rather than naming each flag, so a capability added to the plan table
    # cannot be silently missing from the pricing page.
    return schemas.PlanFeaturesOut(**{name: bool(getattr(spec, name)) for name in FEATURES})


def plan_out(plan_id: str) -> schemas.PlanOut:
    spec = PLANS[plan_id]
    # Guaranteed by `test_only_paid_plans_carry_a_price`; asserted here so a None can never reach
    # the wire as a price.
    assert spec.price_usd_month is not None, f"{plan_id} is not a sold plan"
    assert spec.extra_message_pack_size is not None
    assert spec.extra_message_pack_usd is not None
    return schemas.PlanOut(
        id=plan_id,
        name=_title(plan_id),
        price_usd_month=spec.price_usd_month,
        extra_message_pack_size=spec.extra_message_pack_size,
        extra_message_pack_usd=spec.extra_message_pack_usd,
        limits=_limits(spec),
        features=_features(spec),
        channels=None if spec.channels is None else sorted(spec.channels),
        support=spec.support,
    )


def pricing_table() -> schemas.PricingOut:
    """Every plan that is for sale, cheapest first (the order `PAID_PLANS` is declared in)."""
    return schemas.PricingOut(plans=[plan_out(plan_id) for plan_id in PAID_PLANS])
