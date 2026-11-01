"""A provider failure must never reach a visitor as an empty reply.

The outage these pin (ADR-044): a malformed API key made Gemini reject every request, so
`run_turn` caught the `ProviderError`, left `result.content` at "", and the widget returned
HTTP 200 with an empty body. An empty string reads as a quiet bot, not an outage, so nobody
noticed — the published agent served nothing to every visitor for an unknown period.

`app/modules/public/service.public_chat_once` only accumulates `token` events, so the error
event it also emitted was dropped on the floor. Producing real text is what makes the failure
visible; the error is still recorded on the result for the log and the persisted message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.chat.runtime import TurnResult, run_turn
from app.llm.base import ProviderError
from app.llm.types import ChatRequest, Message, StreamEvent

pytestmark = pytest.mark.anyio

FALLBACK = "Sorry, I can't answer right now — a teammate will follow up."


class BrokenProvider:
    """Fails the way a rejected API key does: raises as soon as the stream is consumed."""

    name = "gemini"

    def supports_tools(self) -> bool:
        return False

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        raise ProviderError("provider returned 400: API key not valid")
        yield  # pragma: no cover - unreachable, makes this an async generator

    async def chat(self, req: ChatRequest) -> object:  # pragma: no cover - unused here
        raise ProviderError("provider returned 400: API key not valid")


class EmptyProvider:
    name = "groq"

    def supports_tools(self) -> bool:
        return False

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="done", finish_reason="stop")

    async def chat(self, req: ChatRequest) -> object:  # pragma: no cover - unused here
        raise AssertionError("stream path expected")


def _request() -> ChatRequest:
    return ChatRequest(
        model="gemini-1.5-flash",
        messages=[Message(role="user", content="what are your hours?")],
        stream=True,
    )


async def _run(**kwargs: object) -> tuple[TurnResult, list[StreamEvent]]:
    result = TurnResult()
    events = [ev async for ev in run_turn(BrokenProvider(), _request(), [], result, **kwargs)]  # type: ignore[arg-type]
    return result, events


async def test_visitor_gets_the_fallback_text_not_an_empty_reply() -> None:
    result, events = await _run(fallback_message=FALLBACK)

    assert result.content.strip() == FALLBACK
    # Streamed too, so the widget renders it live rather than showing an empty bubble.
    assert any(e.type == "token" and e.delta == FALLBACK for e in events)


async def test_the_real_error_is_still_recorded() -> None:
    """The visitor gets a calm sentence; the operator must still get the cause."""
    result, events = await _run(fallback_message=FALLBACK)

    assert result.error is not None
    assert "400" in result.error
    assert any(e.type == "error" for e in events)
    # ...and the provider's message never becomes the visitor-facing text: it can carry key
    # fragments and account details.
    assert "API key" not in result.content


async def test_the_playground_still_sees_a_bare_failure() -> None:
    """No fallback passed (the operator-facing path) still exposes the failure."""
    result, events = await _run()
    assert result.content == ""
    assert result.error is not None
    assert any(e.type == "error" for e in events)


async def test_empty_provider_completion_is_not_successful_eof() -> None:
    result = TurnResult()
    events = [
        ev
        async for ev in run_turn(
            EmptyProvider(), _request(), [], result  # type: ignore[arg-type]
        )
    ]

    assert result.content == ""
    assert result.error == "The model returned no assistant text."
    assert any(e.type == "error" for e in events)
    assert events[-1].type == "done"
    assert events[-1].finish_reason == "error"
