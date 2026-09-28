"""Public pricing schemas (docs/22 §8.1).

Everything here is *rendered from* `app.core.plans.PLANS`. No number, price or feature flag is
written down in this module — that is the whole point of the one-place-for-limits rule, and a
test asserts the endpoint and `PLANS` can never drift apart.
"""

from __future__ import annotations

from pydantic import BaseModel


class PlanLimitsOut(BaseModel):
    """Caps for one plan. `None` means unlimited; `0` means the feature is not included."""

    workspaces: int | None
    agents: int | None
    messages: int | None
    knowledge_bases: int | None
    documents: int | None
    storage_bytes: int | None
    workflows: int | None
    tools: int | None
    webhooks: int | None
    team_members: int | None


class PlanFeaturesOut(BaseModel):
    """On/off capabilities, derived from the counts above (docs/22 §4.1)."""

    workflows: bool
    n8n: bool
    tool_calling: bool
    webhooks: bool
    api_write: bool
    analytics_advanced: bool
    analytics_export: bool
    remove_branding: bool


class PlanOut(BaseModel):
    id: str
    name: str
    price_usd_month: int
    #: Extra conversations, sold in packs at this plan's rate (docs/22 §7).
    extra_message_pack_size: int
    extra_message_pack_usd: int
    limits: PlanLimitsOut
    features: PlanFeaturesOut
    #: `None` = every supported channel.
    channels: list[str] | None
    support: str


class PricingOut(BaseModel):
    """The pricing page's entire data source.

    `contact_only` is true while plans are granted by staff rather than bought self-serve
    (docs/22 §1) — the page renders "Contact us", not "Buy". It flips when docs/23 ships.
    """

    plans: list[PlanOut]
    currency: str = "USD"
    contact_only: bool = True
