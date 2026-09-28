"""The paid plans — Starter / Pro / Business (docs/22 §3, §4). Pure, no database.

Every number here is copied from the pricing table in `docs/22-BILLING-PAID-PLANS.md §3`. If a
test in this file fails after a deliberate price change, change the doc in the same commit —
these assertions exist so the table and the code can never drift apart silently.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.plans import (
    FEATURES,
    GATE_FEATURES,
    GRANTABLE_PLANS,
    LIMIT_FEATURES,
    MESSAGES_PER_EXCHANGE,
    PAID_PLANS,
    PLANS,
    Entitlements,
    PlanSpec,
    get_entitlements,
)

NOW = dt.datetime(2026, 3, 10, 12, 0, tzinfo=dt.UTC)
_MB = 1024 * 1024
_GB = 1024 * _MB


def _paid(plan: str, **kw: object) -> Entitlements:
    return get_entitlements(plan, now=NOW, **kw)  # type: ignore[arg-type]


# ── The pricing table, asserted row by row ───────────────────────────────────────
#  plan        agents  messages  KBs  docs  storage   workspaces  team  price
TABLE = [
    ("starter", 3, 2_000, 1, 20, 500 * _MB, 1, 1, 49),
    ("pro", 10, 10_000, 5, 100, 5 * _GB, 2, 5, 99),
    ("business", 30, 30_000, 20, 500, 10 * _GB, 5, 15, 199),
]


@pytest.mark.parametrize(
    ("plan", "agents", "messages", "kbs", "docs", "storage", "workspaces", "team", "price"), TABLE
)
def test_each_paid_plan_matches_the_pricing_table(
    plan: str,
    agents: int,
    messages: int,
    kbs: int,
    docs: int,
    storage: int,
    workspaces: int,
    team: int,
    price: int,
) -> None:
    ent = _paid(plan)
    assert ent.status == plan, "status is the plan name, so the UI can badge it"
    assert ent.max_agents == agents
    assert ent.max_messages == messages
    assert ent.max_knowledge_bases == kbs
    assert ent.max_documents == docs
    assert ent.storage_bytes == storage
    assert ent.max_workspaces == workspaces
    assert ent.max_team_members == team
    assert ent.spec.price_usd_month == price


@pytest.mark.parametrize(
    ("plan", "pack_usd"), [("starter", 6), ("pro", 5), ("business", 4)]
)
def test_extra_message_pack_rates(plan: str, pack_usd: int) -> None:
    spec = PLANS[plan]
    assert spec.extra_message_pack_size == 500
    assert spec.extra_message_pack_usd == pack_usd


# ── Starter: a chatbot, and nothing else ─────────────────────────────────────────
def test_starter_has_no_automation_at_all() -> None:
    ent = _paid("starter")
    assert not ent.allows("workflows")
    assert not ent.allows("n8n")
    assert not ent.allows("tool_calling")
    assert not ent.allows("webhooks")
    assert not ent.allows("api_write"), "Starter's API is read-only"
    assert not ent.allows("analytics_advanced")
    assert not ent.allows("remove_branding")


def test_starter_is_web_chat_only() -> None:
    ent = _paid("starter")
    assert ent.allows_channel("web")
    assert not ent.allows_channel("whatsapp")
    assert not ent.allows_channel("instagram")


# ── Pro: counted automation ──────────────────────────────────────────────────────
def test_pro_counts_are_counts_not_just_on() -> None:
    ent = _paid("pro")
    assert ent.limit_for("workflows") == 10
    assert ent.limit_for("tools") == 8
    assert ent.limit_for("webhooks") == 5
    assert ent.allows("workflows") and ent.allows("tool_calling") and ent.allows("webhooks")


def test_pro_opens_the_meta_channels() -> None:
    ent = _paid("pro")
    for kind in ("web", "whatsapp", "instagram", "facebook"):
        assert ent.allows_channel(kind), kind
    assert not ent.allows_channel("telegram"), "not in Pro's set"


def test_pro_has_advanced_analytics_but_not_export() -> None:
    ent = _paid("pro")
    assert ent.allows("analytics_advanced")
    assert not ent.allows("analytics_export")


# ── Business: unlimited where it costs nothing ───────────────────────────────────
def test_business_is_unlimited_where_the_table_says_unlimited() -> None:
    ent = _paid("business")
    assert ent.limit_for("workflows") is None
    assert ent.limit_for("tools") is None
    assert ent.limit_for("webhooks") is None
    assert ent.allows("analytics_export")
    assert ent.allows("remove_branding")


def test_business_allows_every_channel() -> None:
    ent = _paid("business")
    for kind in ("web", "whatsapp", "instagram", "facebook", "telegram", "slack", "anything"):
        assert ent.allows_channel(kind), kind


def test_business_still_caps_messages_and_storage() -> None:
    """Unlimited automation is free to us; messages and disk are not."""
    ent = _paid("business")
    assert ent.max_messages == 30_000
    assert ent.storage_bytes == 10 * _GB
    assert ent.is_metered


# ── The zero/None convention ─────────────────────────────────────────────────────
def test_zero_means_off_and_none_means_unlimited() -> None:
    assert PlanSpec.workflows.fget is not None  # property, not a stored field
    assert PLANS["starter"].max_workflows == 0 and PLANS["starter"].workflows is False
    assert PLANS["pro"].max_workflows == 10 and PLANS["pro"].workflows is True
    assert PLANS["business"].max_workflows is None and PLANS["business"].workflows is True


# ── Expiry: computed, never stored, never a cron ─────────────────────────────────
def test_a_granted_plan_expires_on_time_with_no_cron() -> None:
    ent = _paid("pro", plan_expires_at=NOW - dt.timedelta(seconds=1))
    assert ent.status == "plan_expired"
    assert ent.expired_reason == "time"
    assert not ent.bot_replies
    assert not ent.agents_writable


def test_the_expiry_boundary_instant_is_expired() -> None:
    assert _paid("pro", plan_expires_at=NOW).status == "plan_expired"


def test_a_plan_with_no_expiry_never_lapses() -> None:
    ent = _paid("business", messages_used=10)
    assert ent.status == "business"
    assert ent.plan_expires_at is None
    assert ent.days_left(NOW) is None


def test_an_expired_plan_keeps_everything_read_only() -> None:
    """Revoking or lapsing must never destroy a client's work (docs/22 §11 rule 2)."""
    ent = _paid("business", plan_expires_at=NOW - dt.timedelta(days=1))
    assert ent.is_expired
    assert not ent.agents_writable
    assert not ent.allows("workflows")
    assert ent.max_messages == 0, "the bot stops"
    assert ent.meter_limit == 30_000, "the meter still shows what the plan was"


def test_days_left_counts_down_a_granted_plan() -> None:
    ent = _paid("pro", plan_expires_at=NOW + dt.timedelta(days=3, hours=1))
    assert ent.days_left(NOW) == 4  # rounds up, as for trials


# ── Extra-message packs (docs/22 §7) ─────────────────────────────────────────────
def test_packs_raise_the_enforced_cap() -> None:
    ent = _paid("pro", extra_messages=1_500)
    assert ent.max_messages == 10_000, "the plan's own allowance is unchanged"
    assert ent.effective_max_messages == 11_500
    assert ent.meter_limit == 11_500


def test_a_pack_lets_a_blocked_workspace_reply_again() -> None:
    blocked = _paid("pro", messages_used=10_000)
    assert not blocked.bot_replies
    assert blocked.messages_remaining == 0

    topped_up = _paid("pro", messages_used=10_000, extra_messages=1_500)
    assert topped_up.bot_replies
    assert topped_up.messages_remaining == 1_500


def test_the_last_full_exchange_is_answered_on_a_paid_plan() -> None:
    assert _paid("starter", messages_used=2_000 - MESSAGES_PER_EXCHANGE).bot_replies
    assert not _paid("starter", messages_used=2_000 - (MESSAGES_PER_EXCHANGE - 1)).bot_replies


def test_packs_do_nothing_on_an_unlimited_plan() -> None:
    ent = get_entitlements("legacy", extra_messages=5_000, now=NOW)
    assert ent.effective_max_messages is None
    assert ent.messages_remaining is None


def test_an_expired_plan_ignores_its_packs() -> None:
    ent = _paid("pro", extra_messages=1_500, plan_expires_at=NOW - dt.timedelta(days=1))
    assert not ent.bot_replies
    assert ent.extra_messages == 0


# ── Contracts that stop a future edit breaking the UI ────────────────────────────
def test_every_on_off_feature_exists_on_planspec() -> None:
    """`allows()` does a getattr — a name in FEATURES with no attribute is a 500, not a 402."""
    for feature in FEATURES:
        for name, spec in PLANS.items():
            assert isinstance(getattr(spec, feature), bool), f"{name}.{feature}"


def test_every_countable_feature_resolves_to_a_limit() -> None:
    ent = _paid("pro")
    for feature in LIMIT_FEATURES:
        if feature == "channels":
            continue  # a set, not a number — use allows_channel()
        assert ent.limit_for(feature) is None or isinstance(ent.limit_for(feature), int), feature


def test_gate_features_is_the_union_and_has_no_duplicates() -> None:
    assert GATE_FEATURES == FEATURES + LIMIT_FEATURES
    assert len(set(GATE_FEATURES)) == len(GATE_FEATURES)


def test_allows_rejects_a_countable_feature_name() -> None:
    """`agents` is a cap, not a switch — asking `allows("agents")` is a bug worth a loud error."""
    with pytest.raises(ValueError):
        _paid("pro").allows("agents")


def test_limit_for_rejects_an_on_off_feature_name() -> None:
    with pytest.raises(ValueError):
        _paid("pro").limit_for("remove_branding")


def test_paid_plans_are_ordered_cheapest_first() -> None:
    prices = [PLANS[p].price_usd_month for p in PAID_PLANS]
    assert prices == sorted(p for p in prices if p is not None)


def test_grantable_plans_all_exist() -> None:
    for plan in GRANTABLE_PLANS:
        assert plan in PLANS, plan


def test_only_paid_plans_carry_a_price() -> None:
    for name, spec in PLANS.items():
        if name in PAID_PLANS:
            assert spec.price_usd_month is not None, name
        else:
            assert spec.price_usd_month is None, f"{name} is not sold"


def test_a_paid_plan_is_never_mistaken_for_legacy() -> None:
    """Before docs/22 every non-trial plan resolved to `legacy` (unlimited). Not any more."""
    for plan in PAID_PLANS:
        ent = get_entitlements(plan, now=NOW)
        assert ent.status != "legacy"
        assert ent.is_paid
        assert ent.is_metered, f"{plan} must count messages"
