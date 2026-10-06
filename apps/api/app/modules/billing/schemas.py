"""Public pricing + authenticated entitlements schemas (docs/22 §8.1, §8).

Everything here is *rendered from* `app.core.plans.PLANS` / `get_entitlements()`. No number,
price or feature flag is written down in this module — that is the whole point of the
one-place-for-limits rule, and a test asserts the endpoint and `PLANS` can never drift apart.
"""

from __future__ import annotations

import datetime as dt

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
    #: Always USD, whole dollars — kept so nothing that read the pre-currency shape breaks.
    price_usd_month: int
    #: Price in `PricingOut.currency`, INTEGER MINOR UNITS (cents / paise), excl. tax. Divide by
    #: 100 only for display (docs/22 §3, ADR-106).
    price_minor: int
    #: Extra conversations, sold in packs at this plan's rate (docs/22 §7).
    extra_message_pack_size: int
    #: Always USD, whole dollars (legacy field).
    extra_message_pack_usd: int
    #: One pack's price in `PricingOut.currency`, minor units.
    extra_message_pack_price_minor: int
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
    #: The currency every `*_minor` field below is in.
    currency: str = "USD"
    #: Why this currency: `query` (manual switcher), `geo` (country header) or `default`.
    #: Display only — never what anyone is charged.
    currency_source: str = "default"
    available_currencies: list[str] = ["USD", "EUR", "INR"]
    #: "excl. taxes" (USD) · "excl. VAT" (EUR) · "excl. GST" (INR). No tax is calculated.
    tax_note: str = "excl. taxes"
    contact_only: bool = True


# ── GET /v1/me/entitlements (docs/22 §8.1) ──────────────────────────────────────
class UsageCountsOut(BaseModel):
    """Live counts for this org, same shape as `PlanLimitsOut` so the two compare directly —
    but never `None`: a live count is always a number, unlike a cap."""

    agents: int
    messages: int
    knowledge_bases: int
    documents: int
    storage_bytes: int
    workflows: int
    tools: int
    webhooks: int
    team_members: int


class MessagesOut(BaseModel):
    """The message counter in detail — the one limit customers hit often enough to want to see
    the breakdown, not just a single number (docs/22 §6, §7)."""

    used: int
    #: The plan's own cap. `None` = unlimited. Display only — see `effective_limit`.
    plan_limit: int | None
    #: Extra messages bought this period (packs). Always 0 on an unmetered plan.
    extra: int
    #: **The cap actually enforced** — `plan_limit + extra`. Never read `plan_limit` alone to
    #: decide whether the org can still send a message; that is the exact bug class fixed in
    #: `app/chat/inbound.py` (commit 4aabae0) — a customer who bought a pack must see it reflected
    #: here, not the raw plan cap.
    effective_limit: int | None
    #: When the current window resets. `None` for `trial`/`legacy`, which never roll.
    period_end: dt.datetime | None
    unanswered: int


class EntitlementsOut(BaseModel):
    """`GET /v1/me/entitlements` (docs/22 §8.1) — what this org may do right now, plus usage.

    For rendering only, never for a security decision: every limit here is re-enforced
    server-side at the point of mutation, so a client that lies about this response still gets
    a 402. Deliberately carries **no payment information** — `payment_state`/`billing_cycles`
    are staff-only (docs/22 §5.1 rule 1); this answers "what can I do", not "have I paid".
    """

    plan: str
    #: trial | trial_expired | legacy | starter | pro | business | plan_expired — exactly what
    #: `get_entitlements()` returns. No new status values invented here.
    status: str
    plan_expires_at: dt.datetime | None
    #: Whole days remaining until `trial_ends_at`/`plan_expires_at`, rounded up. `None` when
    #: nothing is counting down (legacy, or a plan granted with no expiry).
    days_left: int | None
    limits: PlanLimitsOut
    usage: UsageCountsOut
    messages: MessagesOut
    features: PlanFeaturesOut
    #: `None` = every supported channel.
    channels: list[str] | None
    contact_url: str = "/billing"
