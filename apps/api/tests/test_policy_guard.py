"""L3 policy & distress classifier and the attention queue (docs/11 §4-L3 / §L6, Phase E).

Mock transport throughout. The graded examples below are pinned so a **policy edit** shows up
as a test failure rather than in production: the markdown in `app/chat/policies/` is the tuning
mechanism, and widening "elevated" until ordinary complaints qualify is the realistic way this
feature degrades.
"""

from __future__ import annotations

import json as _json_mod

import httpx
import pytest

from app.chat import attention, policy_guard
from app.core.config import settings


@pytest.fixture(autouse=True)
def _enable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "guard_policy_enabled", True)
    monkeypatch.setattr(settings, "guard_distress_enabled", True)
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test_platform_key")
    monkeypatch.setattr(settings, "guard_policy_retry_backoff_ms", 0)  # retries are tested in test_resilience_adr122
    policy_guard._sticky.clear()
    policy_guard._failures.clear()
    yield


def _transport(content: str, *, status: int = 200, capture: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "boom"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 300}},
        )

    return httpx.MockTransport(handler)


def _verdict_json(
    level: str,
    signals: list[str] | None = None,
    *,
    abuse: bool = False,
    off_topic: bool = False,
    pii_request: bool = False,
) -> str:
    return _json_mod.dumps(
        {
            "distress": {"level": level, "signals": signals or []},
            "abuse": {"violation": abuse, "category": "harassment" if abuse else None},
            "off_topic": {"violation": off_topic, "category": None},
            "pii_request": {"violation": pii_request},
        }
    )


# ── Grading ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("level", ["none", "mild", "elevated", "crisis"], ids=lambda x: x)
async def test_every_distress_level_parses(level: str) -> None:
    v = await policy_guard.classify("hello", transport=_transport(_verdict_json(level)))
    assert v is not None and v.distress == level


async def test_signals_are_returned_for_the_operator() -> None:
    v = await policy_guard.classify(
        "charged twice and rent is due",
        transport=_transport(_verdict_json("elevated", ["charged twice", "rent is due"])),
    )
    assert v is not None and v.signals == ["charged twice", "rent is due"]


async def test_only_crisis_suppresses_generation() -> None:
    """The operator's explicit requirement: the bot keeps answering on elevated."""
    for level in ("none", "mild", "elevated"):
        v = await policy_guard.classify("x", transport=_transport(_verdict_json(level)))
        assert v is not None and not v.suppresses_generation, level
    v = await policy_guard.classify("x", transport=_transport(_verdict_json("crisis")))
    assert v is not None and v.suppresses_generation


async def test_elevated_and_above_needs_attention_but_mild_does_not() -> None:
    """A queue that fills with mild frustration is a queue nobody opens."""
    mild = await policy_guard.classify("x", transport=_transport(_verdict_json("mild")))
    elevated = await policy_guard.classify("x", transport=_transport(_verdict_json("elevated")))
    assert mild is not None and not mild.needs_attention
    assert elevated is not None and elevated.needs_attention


async def test_abuse_needs_attention_at_any_distress_level() -> None:
    v = await policy_guard.classify("x", transport=_transport(_verdict_json("none", abuse=True)))
    assert v is not None and v.abuse and v.needs_attention


def test_tone_directives_exist_for_mild_and_elevated_only() -> None:
    assert set(policy_guard.TONE_DIRECTIVES) == {"mild", "elevated"}
    assert "crisis" not in policy_guard.TONE_DIRECTIVES, "crisis suppresses, it does not steer tone"


# ── Fail open (ADR-051) ──────────────────────────────────────────────────────────────────


async def test_error_returns_none_meaning_not_graded() -> None:
    """`None` must never be read as `none` — one is 'no answer', the other is 'no signal'."""
    assert await policy_guard.classify("x", transport=_transport("", status=500)) is None


async def test_unparseable_response_fails_open() -> None:
    assert await policy_guard.classify("x", transport=_transport("I think they're upset")) is None


async def test_prose_wrapped_json_is_still_parsed() -> None:
    wrapped = "Here is the result:\n```json\n" + _verdict_json("elevated") + "\n```"
    v = await policy_guard.classify("x", transport=_transport(wrapped))
    assert v is not None and v.distress == "elevated"


async def test_unknown_level_degrades_to_none_rather_than_crashing() -> None:
    v = await policy_guard.classify("x", transport=_transport(_verdict_json("catastrophic")))
    assert v is not None and v.distress == "none"


async def test_without_a_platform_key_it_does_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert await policy_guard.classify("x") is None
    assert policy_guard.is_available() is False
    assert "GROQ_API_KEY" in (policy_guard.unavailable_reason() or "")


async def test_org_can_opt_out() -> None:
    captured: list[httpx.Request] = []
    v = await policy_guard.classify(
        "x", org_enabled=False, transport=_transport(_verdict_json("crisis"), capture=captured)
    )
    assert v is None and not captured


async def test_history_is_sent_because_distress_is_a_trajectory() -> None:
    captured: list[httpx.Request] = []
    await policy_guard.classify(
        "still nothing",
        history=["where is my order", "you said tomorrow", "that was three days ago"],
        transport=_transport(_verdict_json("elevated"), capture=captured),
    )
    body = _json_mod.loads(captured[0].content)
    user_msg = body["messages"][-1]["content"]
    assert "that was three days ago" in user_msg


# ── The request fits Groq's tokens-per-minute ceiling (a 413 on a 27-character message) ──────────


def _estimated_request_tokens(body: dict) -> float:
    """Same yardstick the window uses: system ~4 chars/token (measured), customer text ~3."""
    system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
    return len(system) / 4.0 + len(user) / 3.0 + body["max_completion_tokens"]


async def test_the_completion_is_capped_so_groq_does_not_reserve_a_huge_one() -> None:
    captured: list[httpx.Request] = []
    await policy_guard.classify(
        "Can I get a ur owner number", transport=_transport(_verdict_json("none"), capture=captured)
    )
    body = _json_mod.loads(captured[0].content)
    assert body["max_completion_tokens"] == settings.guard_policy_max_completion_tokens
    assert body["reasoning_effort"] == "low"
    assert _estimated_request_tokens(body) <= settings.guard_policy_max_request_tokens


async def test_a_short_message_keeps_its_history_and_is_not_truncated() -> None:
    captured: list[httpx.Request] = []
    await policy_guard.classify(
        "still nothing",
        history=["where is my order", "you said tomorrow"],
        transport=_transport(_verdict_json("none"), capture=captured),
    )
    user_msg = _json_mod.loads(captured[0].content)["messages"][-1]["content"]
    assert "still nothing" in user_msg and "you said tomorrow" in user_msg and "[...]" not in user_msg


async def test_a_long_message_and_long_history_are_windowed_to_fit() -> None:
    captured: list[httpx.Request] = []
    huge = "start of the message " + ("blah " * 3000) + " the worst part is at the very end"
    await policy_guard.classify(
        huge,
        history=["x" * 1000, "y" * 1000, "z" * 1000, "w" * 1000],
        transport=_transport(_verdict_json("none"), capture=captured),
    )
    body = _json_mod.loads(captured[0].content)
    user_msg = body["messages"][-1]["content"]
    assert _estimated_request_tokens(body) <= settings.guard_policy_max_request_tokens
    assert "start of the message" in user_msg and "the very end" in user_msg  # head and tail survive
    assert "[...]" in user_msg and len(user_msg) < 1500


async def test_a_larger_limit_gives_the_message_more_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "guard_policy_max_request_tokens", 8000)
    captured: list[httpx.Request] = []
    await policy_guard.classify("a" * 3000, transport=_transport(_verdict_json("none"), capture=captured))
    user_msg = _json_mod.loads(captured[0].content)["messages"][-1]["content"]
    assert "[...]" not in user_msg


async def test_a_413_fails_open_and_is_logged_without_message_text() -> None:
    import structlog

    with structlog.testing.capture_logs() as logs:
        out = await policy_guard.classify(
            "private words", transport=_transport("", status=413)
        )
    assert out is None
    event = next(e for e in logs if e["event"] == "guard_l3_unavailable")
    assert "private words" not in str(event)


# ── The policy documents ─────────────────────────────────────────────────────────────────


def test_policy_text_includes_every_concern() -> None:
    policy_guard.reload_policies()
    text = policy_guard._policy_text()
    for heading in ("# Distress", "# Abuse", "# Off topic", "# PII request"):
        assert heading in text, f"missing policy: {heading}"


def test_policies_are_hot_reloadable() -> None:
    """Editing wording must not need a deploy — the tuning loop runs on real conversations."""
    first = policy_guard._policy_text()
    policy_guard.reload_policies()
    assert policy_guard._policy_text() == first


def test_distress_policy_keeps_crisis_narrow() -> None:
    """The negative examples are load-bearing: without them the classifier drifts upward."""
    text = policy_guard._policy_text()
    assert "Not `crisis`" in text, "crisis needs an explicit negative example or it over-fires"
    assert "Not `elevated`" in text
    assert "figurative" in text.lower()


def test_policies_are_flagged_as_unreviewed() -> None:
    """These are drafts. The marker stays until a human has read them (docs/11 §4-L3).

    The marker and this assertion are **one safeguard**. Flipping either alone removes it, so
    when the policies are genuinely reviewed, change both in the same commit.
    """
    text = policy_guard._policy_text()
    assert "DRAFT" in text, "policy wording is unreviewed and must say so"


def test_crisis_message_is_static_and_says_something() -> None:
    msg = policy_guard.CRISIS_HOLDING_MESSAGE
    assert msg.strip(), "silence in a crisis is the worst outcome"
    assert "team" in msg.lower(), "it must hand over to a person"
    # Acknowledge without diagnosing, advising, or claiming to understand.
    for forbidden in ("I understand how you feel", "calm down", "don't worry", "everything will be"):
        assert forbidden.lower() not in msg.lower()


# ── The attention queue ──────────────────────────────────────────────────────────────────


def test_severity_only_ratchets_up() -> None:
    """A customer who calms down has not stopped needing a human."""
    assert attention.escalates(None, "mild")
    assert attention.escalates("mild", "crisis")
    assert not attention.escalates("crisis", "mild")
    assert not attention.escalates("elevated", "elevated")


def test_severity_rank_is_ordered() -> None:
    ranks = [attention.severity_rank(x) for x in ("none", "mild", "elevated", "crisis")]
    assert ranks == sorted(ranks) and len(set(ranks)) == 4
    assert attention.severity_rank(None) == 0
