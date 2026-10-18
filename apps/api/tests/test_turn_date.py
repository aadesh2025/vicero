"""ADR-127: every turn tells the model today's date, weekday and time in the business timezone."""

from __future__ import annotations

import datetime as dt

import pytest

from app.chat.assembly import build_messages, current_time_line
from app.core.config import settings
from app.llm.types import Message


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.UTC)


def test_the_line_names_the_weekday_in_the_business_timezone() -> None:
    # 20:00 UTC on Friday 16 Oct is already 01:30 on Saturday 17 Oct in India.
    line = current_time_line(_utc(2026, 10, 16, 20, 0))
    assert "Saturday, 17 October 2026, 01:30" in line and "Asia/Kolkata" in line


def test_every_weekday_name_is_right() -> None:
    start = _utc(2026, 10, 12, 6, 0)  # a Monday, 11:30 in India
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for offset, name in enumerate(names):
        line = current_time_line(start + dt.timedelta(days=offset))
        assert f"{name}, {12 + offset:02d} October 2026" in line, line


def test_it_follows_the_configured_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "booking_timezone", "America/New_York")
    assert "Friday, 16 October 2026, 16:00 (America/New_York)" in current_time_line(_utc(2026, 10, 16, 20, 0))


def _build(**over: object) -> list[Message]:
    args: dict[str, object] = {
        "system_prompt": "You are the assistant.",
        "context_block": "CONTEXT BLOCK",
        "memory_summary": None,
        "history": [],
        "user_message": "Can u book meeting on Saturday 11 am",
        "window_messages": 6,
        "now": _utc(2026, 10, 14, 6, 30),  # Wednesday
    }
    args.update(over)
    return build_messages(**args)  # type: ignore[arg-type]


def test_the_date_rides_on_the_persona_message_and_leaves_the_rest_alone() -> None:
    messages = _build()
    assert [m.role for m in messages] == ["system", "system", "user"]
    assert messages[0].content is not None and messages[0].content.startswith("You are the assistant.")
    assert "Wednesday, 14 October 2026, 12:00" in messages[0].content and "Sat 17 Oct" in messages[0].content
    assert messages[1].content == "CONTEXT BLOCK", "the retrieved-context message is untouched"


def test_an_agent_without_a_prompt_still_gets_the_date() -> None:
    messages = _build(system_prompt=None, context_block="")
    assert messages[0].role == "system" and "Wednesday, 14 October 2026" in (messages[0].content or "")


def test_next_saturday_from_a_wednesday_is_three_days_out() -> None:
    """The arithmetic the model is now able to do: Wednesday 14 Oct -> Saturday 17 Oct."""
    today = dt.date(2026, 10, 14)
    saturday = today + dt.timedelta(days=(5 - today.weekday()) % 7)
    assert saturday == dt.date(2026, 10, 17) and saturday.strftime("%A") == "Saturday"
    assert "Wednesday, 14 October 2026" in current_time_line(_utc(2026, 10, 14, 6, 30))


def test_the_next_seven_days_are_spelled_out_with_the_right_weekdays() -> None:
    # Sunday 18 Oct 2026 in India: "Saturday" is the 24th (the model once said the 23rd, a Friday).
    line = current_time_line(_utc(2026, 10, 18, 4, 0))
    assert "Sunday, 18 October 2026" in line
    assert "The next days: Mon 19 Oct, Tue 20 Oct, Wed 21 Oct, Thu 22 Oct, Fri 23 Oct, Sat 24 Oct, Sun 25 Oct." in line


def test_the_listed_days_always_match_the_calendar() -> None:
    import re

    for offset in range(0, 40, 3):
        now = _utc(2026, 10, 1, 6, 0) + dt.timedelta(days=offset)
        line = current_time_line(now)
        listed = re.search(r"The next days: (.*?)\. Use", line)
        assert listed is not None
        local = now.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
        for n, part in enumerate(listed.group(1).split(", "), start=1):
            expected = local + dt.timedelta(days=n)
            assert part == f"{expected:%a %d %b}", (offset, part)
