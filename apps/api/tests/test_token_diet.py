"""ADR-125: smaller turns, one Retry-After backoff on a 429, L3 on its own provider, tokens logged per turn."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import structlog

from app.chat import policy_guard
from app.chat.runtime import TurnResult, run_turn
from app.core.config import settings
from app.llm import fallback as fallback_module
from app.llm.base import ProviderError, parse_retry_after
from app.llm.fallback import FallbackChatProvider, build_fallback_chain
from app.llm.types import ChatRequest, Message, ModelInfo, StreamEvent, ToolSpec, Usage
from app.modules.bookings import schemas as booking_schemas
from app.tools.compact import compact_description, compact_schema


def _req(**over: Any) -> ChatRequest:
    base: dict[str, Any] = {"model": "m1", "messages": [Message(role="user", content="hello")]}
    return ChatRequest(**{**base, **over})


class _Script:
    """A provider whose calls follow a script: one entry per call, an Exception to raise or text to emit."""

    def __init__(self, name: str, script: list[Any]) -> None:
        self.name = name
        self.active_model: str | None = None
        self.script = list(script)
        self.models: list[str | None] = []

    async def chat(self, req: ChatRequest) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.models.append(req.model)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        yield StreamEvent(type="token", delta=step)
        yield StreamEvent(type="done", usage=Usage(prompt_tokens=100, completion_tokens=20), finish_reason="stop")

    def supports_tools(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return []


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(fallback_module.asyncio, "sleep", fake_sleep)
    return waited


def _429(retry_after: float | None = None) -> ProviderError:
    return ProviderError("provider returned 429: rate limit", retryable=True, status=429, retry_after=retry_after)


async def _drain(provider: Any) -> str:
    return "".join([e.delta or "" async for e in provider.stream(_req()) if e.type == "token"])


# ── 4. one short Retry-After backoff, then the next model ────────────────────────────────────────────────
async def test_a_429_waits_for_retry_after_and_retries_the_same_model_once(sleeps: list[float]) -> None:
    p = _Script("groq", [_429(1.5), "answered"])
    chain = FallbackChatProvider([(p, "m1"), (p, "m2")])
    assert await _drain(chain) == "answered"
    assert sleeps == [1.5]
    assert p.models == ["m1", "m1"], "same model again, not the fallback"


async def test_a_429_without_the_header_uses_the_short_default(
    sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_retry_default_seconds", 0.7)
    p = _Script("groq", [_429(None), "ok"])
    assert await _drain(FallbackChatProvider([(p, "m1")])) == "ok"
    assert sleeps == [0.7]


async def test_a_long_retry_after_goes_straight_to_the_next_model(sleeps: list[float]) -> None:
    p = _Script("groq", [_429(30.0), "from the second model"])
    chain = FallbackChatProvider([(p, "m1"), (p, "m2")])
    assert await _drain(chain) == "from the second model"
    assert sleeps == [] and p.models == ["m1", "m2"]


async def test_each_model_is_retried_only_once(sleeps: list[float]) -> None:
    p = _Script("groq", [_429(0.1)])  # always 429
    chain = FallbackChatProvider([(p, "m1"), (p, "m2")])
    with pytest.raises(ProviderError):
        await _drain(chain)
    assert p.models == ["m1", "m1", "m2", "m2"]
    assert sleeps == [0.1, 0.1]


async def test_a_non_429_failure_is_not_retried(sleeps: list[float]) -> None:
    p = _Script("groq", [ProviderError("provider returned 503", status=503), "fine"])
    chain = FallbackChatProvider([(p, "m1"), (p, "m2")])
    assert await _drain(chain) == "fine"
    assert sleeps == [] and p.models == ["m1", "m2"]


async def test_nothing_is_retried_after_output_has_started(sleeps: list[float]) -> None:
    class _Late(_Script):
        async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type="token", delta="partial ")
            raise _429(0.1)

    with pytest.raises(ProviderError):
        await _drain(FallbackChatProvider([(_Late("groq", ["x"]), "m1")]))
    assert sleeps == []


def test_parse_retry_after() -> None:
    assert parse_retry_after({"retry-after": "2"}) == 2.0
    assert parse_retry_after({"retry-after": "0.5"}) == 0.5
    assert parse_retry_after({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert parse_retry_after({}) is None
    assert parse_retry_after(None) is None


def test_a_single_provider_chain_is_wrapped_too() -> None:
    chain = build_fallback_chain(_Script("solo", ["x"]), "m", [])
    assert isinstance(chain, FallbackChatProvider) and chain.name == "solo"


# ── 1. smaller turns ───────────────────────────────────────────────────────────────────────────────────
def test_defaults_send_less_per_turn() -> None:
    assert settings.memory_window_messages == 6
    assert settings.rag_context_char_budget == 3000


def test_compact_schema_drops_value_constraints_but_keeps_property_names() -> None:
    schema = {
        "type": "object",
        "title": "Args",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 120, "description": "A  title\n  to book."},
            "default": {"type": "string", "default": "x"},
        },
        "required": ["title"],
    }
    out = compact_schema(schema)
    assert out == {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "A title to book."},
            "default": {"type": "string"},
        },
        "required": ["title"],
    }


def test_compact_description_cuts_at_a_sentence() -> None:
    text = "First sentence here. " + "Second sentence is long and keeps going. " * 8
    out = compact_description(text, 80)
    assert out.endswith(".") and len(out) <= 80 and out.startswith("First sentence here.")
    assert compact_description("short  and\nsweet") == "short and sweet"


def test_the_booking_tool_definitions_got_smaller() -> None:
    def size(specs: list[ToolSpec]) -> int:
        return len(json.dumps([s.model_dump() for s in specs]))

    raw = [
        ToolSpec(name=t["name"], description=t["description"], parameters=t["input_schema"])
        for t in booking_schemas.TOOL_SPECS
    ]
    compact = [
        ToolSpec(
            name=s.name, description=compact_description(s.description), parameters=compact_schema(s.parameters)
        )
        for s in raw
    ]
    assert size(compact) < size(raw) * 0.9
    book = next(s for s in compact if s.name == "book_appointment")
    assert book.parameters["required"] == ["name", "contact", "start_iso"]
    assert "minLength" not in json.dumps(book.parameters)


# ── 5. tokens per turn, logged ─────────────────────────────────────────────────────────────────────────
async def test_the_turn_logs_its_real_token_counts_and_sizes_but_no_text() -> None:
    provider = _Script("groq", ["a reply"])
    req = _req(
        messages=[
            Message(role="system", content="S" * 400),
            Message(role="user", content="my secret question"),
        ],
        tools=[ToolSpec(name="t", description="d", parameters={"type": "object"})],
        max_tokens=400,
    )
    result = TurnResult()
    with structlog.testing.capture_logs() as logs:
        _ = [ev async for ev in run_turn(provider, req, [], result)]
    entry = next(e for e in logs if e["event"] == "turn_tokens")
    assert entry["prompt_tokens"] == 100 and entry["completion_tokens"] == 20 and entry["total_tokens"] == 120
    assert entry["system_chars"] == 400 and entry["history_msgs"] == 1 and entry["max_tokens"] == 400
    assert entry["tools_chars"] > 0 and entry["tool_rounds"] == 0
    assert "secret" not in str(entry) and "S" * 50 not in str(entry)


# ── 2. L3 on its own provider/model ────────────────────────────────────────────────────────────────────
CALM = (
    '{"distress":{"level":"none","signals":[]},"abuse":{"violation":false},'
    '"off_topic":{"violation":false},"pii_request":{"violation":false}}'
)


@pytest.fixture
def l3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "guard_policy_enabled", True)
    monkeypatch.setattr(settings, "groq_api_key", "gsk_groq")
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    monkeypatch.setattr(settings, "guard_policy_retry_backoff_ms", 0)
    policy_guard._sticky.clear()
    policy_guard._failures.clear()


def _capture(status: dict[str, int] | None = None) -> tuple[httpx.MockTransport, list[tuple[str, str, str]]]:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.host, body["model"], request.headers["authorization"]))
        code = (status or {}).get(request.url.host, 200)
        if code != 200:
            return httpx.Response(code, json={"error": {"message": "x"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": CALM}}], "usage": {"total_tokens": 9}})

    return httpx.MockTransport(handler), seen


async def test_l3_defaults_to_its_own_groq_model_not_the_chat_model(l3: None) -> None:
    assert settings.guard_policy_provider == "groq"
    assert settings.guard_policy_model == "openai/gpt-oss-20b"
    assert settings.guard_policy_model != "openai/gpt-oss-120b"
    transport, seen = _capture()
    assert await policy_guard.classify("hello", transport=transport) is not None
    assert seen == [("api.groq.com", "openai/gpt-oss-20b", "Bearer gsk_groq")]


async def test_l3_can_sit_on_gemini_with_the_gemini_key(l3: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "guard_policy_provider", "gemini")
    monkeypatch.setattr(settings, "guard_policy_model", "gemini-2.5-flash-lite")
    transport, seen = _capture()
    assert await policy_guard.classify("hello", transport=transport) is not None
    assert seen == [("generativelanguage.googleapis.com", "gemini-2.5-flash-lite", "Bearer g-key")]


async def test_a_rate_limited_gemini_l3_falls_back_to_the_groq_model(l3: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "guard_policy_provider", "gemini")
    monkeypatch.setattr(settings, "guard_policy_model", "gemini-2.5-flash-lite")
    monkeypatch.setattr(settings, "guard_policy_retries", 0)
    transport, seen = _capture({"generativelanguage.googleapis.com": 429})
    assert await policy_guard.classify("hello", transport=transport) is not None
    assert [h for h, _m, _a in seen] == ["generativelanguage.googleapis.com", "api.groq.com"]
    assert seen[1][1] == settings.guard_policy_fallback_model and seen[1][2] == "Bearer gsk_groq"


async def test_l3_without_its_providers_key_is_unavailable_and_says_why(
    l3: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "guard_policy_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert policy_guard.is_available() is False
    assert "GEMINI_API_KEY" in (policy_guard.unavailable_reason() or "")
    transport, seen = _capture()
    assert await policy_guard.classify("hello", transport=transport) is None
    assert seen == []


# ── a tool turn does not re-send the retrieved context on its follow-up calls (ADR-126) ─────────────────
class _ToolThenText:
    """First call asks for a tool, the second answers. Records the messages each call received."""

    name = "groq"
    active_model: str | None = None

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def chat(self, req: ChatRequest) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        from app.llm.types import ToolCall

        self.calls.append(list(req.messages))
        if len(self.calls) == 1:
            yield StreamEvent(type="tool_call", tool_call=ToolCall(id="c1", name="check_availability", arguments={}))
            yield StreamEvent(
                type="done", usage=Usage(prompt_tokens=10, completion_tokens=1), finish_reason="tool_calls"
            )
        else:
            yield StreamEvent(type="token", delta="Here are the slots.")
            yield StreamEvent(type="done", usage=Usage(prompt_tokens=10, completion_tokens=3), finish_reason="stop")

    def supports_tools(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return []


async def _tool_turn() -> _ToolThenText:
    from app.rag.context import CONTEXT_MARKER

    provider = _ToolThenText()

    async def executor(_call: Any) -> dict[str, Any]:
        return {"output": {"ok": True, "slots": []}, "status": "success", "error": None}

    req = _req(
        messages=[
            Message(role="system", content="PERSONA prompt"),
            Message(role="system", content=f"{CONTEXT_MARKER} [1] pricing text"),
            Message(role="user", content="book me"),
        ],
        tools=[ToolSpec(name="check_availability", description="d", parameters={"type": "object"})],
    )
    _ = [ev async for ev in run_turn(provider, req, [], TurnResult(), executor=executor, max_iters=3)]
    return provider


async def test_the_follow_up_call_of_a_tool_turn_leaves_the_retrieved_context_out() -> None:
    provider = await _tool_turn()
    first, second = provider.calls
    assert any("pricing text" in (m.content or "") for m in first), "the first call has the context"
    assert not any("pricing text" in (m.content or "") for m in second)
    assert any(m.content == "PERSONA prompt" for m in second), "the persona prompt stays"
    assert any(m.role == "tool" for m in second), "and so does the tool result"


async def test_the_context_stays_when_the_setting_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tool_followup_drops_context", False)
    provider = await _tool_turn()
    assert any("pricing text" in (m.content or "") for m in provider.calls[1])
