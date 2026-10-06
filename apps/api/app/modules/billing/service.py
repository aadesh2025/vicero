"""Pricing table rendering (docs/22 §8.1) + the authenticated entitlements endpoint (docs/22 §8).

`pricing_table()` is pure — no database, no request context — kept separate from the router so
the mapping from `PLANS` to the wire format can be unit-tested without an app, and so the same
function can feed the admin panel's plan picker later. `entitlements()` is the one function here
that touches the database: one org's live usage against its own plan.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.usage import load_entitlements, unanswered_messages
from app.core.plans import (
    DEFAULT_CURRENCY,
    FEATURES,
    PAID_PLANS,
    PLANS,
    SUPPORTED_CURRENCIES,
    PlanSpec,
    pack_price_for,
    price_for,
)
from app.models import (
    Agent,
    Document,
    KnowledgeBase,
    Membership,
    OrgMessageUsage,
    OrgStorageUsage,
    Tool,
    WebhookEndpoint,
    Workflow,
)
from app.modules.billing import schemas
from app.modules.orgs.deps import OrgContext


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


#: What the price excludes, per currency. Text only — no tax is calculated anywhere.
TAX_NOTE = {"USD": "excl. taxes", "EUR": "excl. VAT", "INR": "excl. GST"}


def plan_out(plan_id: str, currency: str = DEFAULT_CURRENCY) -> schemas.PlanOut:
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
        price_minor=price_for(plan_id, currency),
        extra_message_pack_size=spec.extra_message_pack_size,
        extra_message_pack_usd=spec.extra_message_pack_usd,
        extra_message_pack_price_minor=pack_price_for(plan_id, currency),
        limits=_limits(spec),
        features=_features(spec),
        channels=None if spec.channels is None else sorted(spec.channels),
        support=spec.support,
    )


def pricing_table(
    currency: str = DEFAULT_CURRENCY, currency_source: str = "default"
) -> schemas.PricingOut:
    """Every plan that is for sale, cheapest first (the order `PAID_PLANS` is declared in), priced
    in `currency`. Limits and features are identical in every currency."""
    return schemas.PricingOut(
        plans=[plan_out(plan_id, currency) for plan_id in PAID_PLANS],
        currency=currency,
        currency_source=currency_source,
        available_currencies=list(SUPPORTED_CURRENCIES),
        tax_note=TAX_NOTE[currency],
    )


async def _usage_counts(session: AsyncSession, org_id: uuid.UUID, messages_used: int) -> schemas.UsageCountsOut:
    """Live counts against the same keys `PlanLimitsOut` caps — one org, so plain sequential
    counts rather than the batch-then-group-in-Python style the admin roster needs across many.
    """
    agents = await session.scalar(
        select(func.count()).select_from(Agent).where(Agent.organization_id == org_id, Agent.deleted_at.is_(None))
    )
    knowledge_bases = await session.scalar(
        select(func.count())
        .select_from(KnowledgeBase)
        .where(KnowledgeBase.organization_id == org_id, KnowledgeBase.deleted_at.is_(None))
    )
    documents = await session.scalar(
        select(func.count()).select_from(Document).where(Document.organization_id == org_id)
    )
    workflows = await session.scalar(
        select(func.count())
        .select_from(Workflow)
        .where(Workflow.organization_id == org_id, Workflow.deleted_at.is_(None))
    )
    tools = await session.scalar(select(func.count()).select_from(Tool).where(Tool.organization_id == org_id))
    webhooks = await session.scalar(
        select(func.count()).select_from(WebhookEndpoint).where(WebhookEndpoint.organization_id == org_id)
    )
    team_members = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.organization_id == org_id, Membership.status == "active")
    )
    storage_bytes = await session.scalar(
        select(OrgStorageUsage.bytes_used).where(OrgStorageUsage.organization_id == org_id)
    )
    return schemas.UsageCountsOut(
        agents=int(agents or 0),
        messages=messages_used,
        knowledge_bases=int(knowledge_bases or 0),
        documents=int(documents or 0),
        storage_bytes=int(storage_bytes or 0),
        workflows=int(workflows or 0),
        tools=int(tools or 0),
        webhooks=int(webhooks or 0),
        team_members=int(team_members or 0),
    )


async def entitlements(session: AsyncSession, ctx: OrgContext) -> schemas.EntitlementsOut:
    """`GET /v1/me/entitlements` (docs/22 §8.1): what `ctx.org` may do right now, plus usage.

    Every number here comes from `load_entitlements()` (the same DB-backed resolver the chat
    path and the admin console use) or a live `COUNT(*)` — nothing is recomputed by hand, and
    the message cap shown is always `effective_max_messages` (plan cap + packs), never the raw
    plan cap, which is the exact bug class `app/chat/inbound.py` was fixed for (commit 4aabae0).
    """
    now = dt.datetime.now(tz=dt.UTC)
    ent = await load_entitlements(session, ctx.org, now=now)

    period_end = await session.scalar(
        select(OrgMessageUsage.period_end).where(OrgMessageUsage.organization_id == ctx.org.id)
    )
    unanswered = await unanswered_messages(session, ctx.org.id)
    usage = await _usage_counts(session, ctx.org.id, ent.messages_used)

    return schemas.EntitlementsOut(
        plan=ctx.org.plan,
        status=ent.status,
        plan_expires_at=ent.plan_expires_at,
        days_left=ent.days_left(now),
        limits=_limits(ent.spec),
        usage=usage,
        messages=schemas.MessagesOut(
            used=ent.messages_used,
            # `ent.meter_limit`, not `ent.spec.max_messages`: on an expired plan `spec` becomes
            # the zeroed `_EXPIRED` spec (the actually-enforced cap), but the meter should still
            # read "312 / 500", not "312 / 0" — the same reason `orgs.plan_status` uses it.
            plan_limit=ent.meter_limit,
            extra=ent.extra_messages,
            effective_limit=ent.effective_max_messages,
            period_end=period_end,
            unanswered=unanswered,
        ),
        features=_features(ent.spec),
        channels=None if ent.spec.channels is None else sorted(ent.spec.channels),
    )
