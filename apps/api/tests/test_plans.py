"""The entitlements table (docs/18 §6) — pure, no database."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.plans import MESSAGES_PER_EXCHANGE, PLANS, get_entitlements, plan_limit

NOW = dt.datetime(2026, 3, 10, 12, 0, tzinfo=dt.UTC)
TRIAL_DAYS = PLANS["trial"].trial_days
assert TRIAL_DAYS is not None


def _trial(**kw):  # type: ignore[no-untyped-def]
    ends = kw.pop("ends", NOW + dt.timedelta(days=TRIAL_DAYS))
    return get_entitlements("trial", trial_ends_at=ends, now=NOW, **kw)


def test_a_fresh_trial_matches_the_spec_table() -> None:
    ent = _trial()
    assert ent.status == "trial"
    assert (ent.max_workspaces, ent.max_agents, ent.max_messages) == (1, 1, 500)
    assert ent.playground_per_day == 50
    assert not ent.allows("workflows")
    assert not ent.allows("n8n")
    assert not ent.allows("tool_calling")
    assert ent.bot_replies
    assert ent.days_left(NOW) == 10


def test_the_trial_is_ten_days_long() -> None:
    assert TRIAL_DAYS == 10


def test_a_trial_expires_by_time_without_any_cron() -> None:
    ent = get_entitlements(
        "trial", trial_ends_at=NOW - dt.timedelta(seconds=1), messages_used=3, now=NOW
    )
    assert ent.status == "trial_expired"
    assert ent.expired_reason == "time"
    assert not ent.bot_replies
    assert ent.max_messages == 0
    # The meter keeps showing the trial cap: "312 / 500", not "312 / 0".
    assert ent.meter_limit == 500


def test_the_boundary_instant_is_expired() -> None:
    assert get_entitlements("trial", trial_ends_at=NOW, now=NOW).status == "trial_expired"


def test_a_trial_expires_when_the_messages_are_spent() -> None:
    ent = _trial(messages_used=500)
    assert ent.status == "trial_expired"
    assert ent.expired_reason == "messages"
    assert not ent.bot_replies


def test_one_message_left_cannot_answer_a_visitor() -> None:
    """A reply is reserved as a pair, so 499/500 is stuck unless it is treated as spent."""
    ent = _trial(messages_used=500 - (MESSAGES_PER_EXCHANGE - 1))
    assert ent.status == "trial"
    assert not ent.bot_replies


def test_the_last_full_exchange_is_answered() -> None:
    assert _trial(messages_used=500 - MESSAGES_PER_EXCHANGE).bot_replies


def test_an_expired_trial_keeps_its_agent_read_only() -> None:
    ent = _trial(ends=NOW - dt.timedelta(days=1))
    assert ent.max_agents == 1
    assert not ent.agents_writable
    assert not ent.allows("workflows")


def test_legacy_is_unlimited_and_never_expires() -> None:
    ent = get_entitlements("legacy", trial_ends_at=NOW - dt.timedelta(days=99), messages_used=10**9, now=NOW)
    assert ent.status == "legacy"
    assert not ent.is_metered
    assert ent.max_messages is None and ent.max_agents is None and ent.max_workspaces is None
    assert ent.bot_replies and ent.agents_writable
    assert ent.allows("workflows") and ent.allows("n8n") and ent.allows("tool_calling")
    assert ent.days_left(NOW) is None


@pytest.mark.parametrize("plan", [None, "", "free", "pro", "enterprise", "something-new"])
def test_anything_unrecognised_resolves_to_legacy_not_to_locked(plan: str | None) -> None:
    """A stored value this code doesn't know is far likelier a paid plan than a trial."""
    assert get_entitlements(plan).status == "legacy"


def test_days_left_rounds_up_and_floors_at_zero() -> None:
    almost = get_entitlements("trial", trial_ends_at=NOW + dt.timedelta(hours=1), now=NOW)
    assert almost.days_left(NOW) == 1
    over = get_entitlements("trial", trial_ends_at=NOW - dt.timedelta(hours=1), now=NOW)
    assert over.days_left(NOW) == 0


def test_naive_datetimes_are_read_as_utc() -> None:
    naive_end = (NOW + dt.timedelta(days=2)).replace(tzinfo=None)
    assert get_entitlements("trial", trial_ends_at=naive_end, now=NOW).status == "trial"


def test_an_unknown_feature_name_is_a_bug_not_a_silent_yes() -> None:
    with pytest.raises(ValueError):
        _trial().allows("teleportation")


def test_plan_limit_error_shape() -> None:
    err = plan_limit("workflows")
    assert err.status_code == 402
    assert err.code == "plan_limit"
    assert err.details == {"feature": "workflows"}
