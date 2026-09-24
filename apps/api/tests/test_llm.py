"""Phase 5: LLM provider layer — fakes, OpenAI-compatible, Gemini/Anthropic, pricing, fallback."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import AppError
from app.llm.anthropic import AnthropicProvider, to_anthropic_payload
from app.llm.fake import FailingProvider, FakeChatProvider
from app.llm.gemini import GeminiProvider, to_gemini_payload
from app.llm.openai_compatible import GroqProvider
from app.llm.pricing import compute_cost_micros, price_for
from app.llm.registry import run_with_fallback
from app.llm.types import ChatRequest, Message, Usage


def _req(content: str = "hello world", **kw: object) -> ChatRequest:
    return ChatRequest(model="m", messages=[Message(role="user", content=content)], **kw)  # type: ignore[arg-type]


def _mock(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


# ── Fakes ─────────────────────────────────────────────────────────────────────
async def test_fake_chat_and_stream() -> None:
    provider = FakeChatProvider()
    out = await provider.chat(_req("hi"))
    assert out.content == "echo: hi"

    events = [e async for e in provider.stream(_req("hi there"))]
    text = "".join(e.delta or "" for e in events if e.type == "token")
    assert text.strip() == "echo: hi there"
    assert events[-1].type == "done"
    assert events[-1].usage is not None


# ── OpenAI-compatible (Groq) ──────────────────────────────────────────────────
async def test_openai_compatible_chat() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-oss-120b",
                "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    provider = GroqProvider("secret-key", transport=_mock(handler))
    out = await provider.chat(_req("hey"))
    assert out.content == "hi there"
    assert out.provider == "groq"
    assert out.usage.completion_tokens == 2
    assert captured["auth"] == "Bearer secret-key"
    assert captured["body"]["messages"][0] == {"role": "user", "content": "hey"}


async def test_openai_compatible_tool_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": '{"city": "SF"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    provider = GroqProvider("k", transport=_mock(handler))
    out = await provider.chat(_req("weather?"))
    assert out.tool_calls[0].name == "get_weather"
    assert out.tool_calls[0].arguments == {"city": "SF"}


async def test_openai_compatible_stream() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    provider = GroqProvider("k", transport=_mock(handler))
    events = [e async for e in provider.stream(_req("hi"))]
    text = "".join(e.delta or "" for e in events if e.type == "token")
    assert text == "Hello"
    assert events[-1].type == "done"
    assert events[-1].usage is not None and events[-1].usage.prompt_tokens == 3


async def test_stream_error_frame_raises_instead_of_empty_reply() -> None:
    """A provider can report failure *inside* a 200 stream (Groq does this for
    tool_use_failed). That frame carries no `choices`, so it must not be skipped —
    skipping it ended the turn as a silent empty reply with no error."""
    from app.llm.base import ProviderError

    sse = (
        "event: error\n"
        'data: {"error":{"message":"tool call validation failed: parameters for tool echo_tool '
        'did not match schema","type":"invalid_request_error","code":"tool_use_failed",'
        '"status_code":400}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    provider = GroqProvider("k", transport=_mock(handler))
    with pytest.raises(ProviderError) as exc:
        [e async for e in provider.stream(_req("hi"))]
    assert "tool call validation failed" in str(exc.value)
    assert exc.value.retryable is False


async def test_stream_error_frame_marks_rate_limit_retryable() -> None:
    from app.llm.base import ProviderError

    sse = 'data: {"error":{"message":"rate limited","status_code":429}}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    provider = GroqProvider("k", transport=_mock(handler))
    with pytest.raises(ProviderError) as exc:
        [e async for e in provider.stream(_req("hi"))]
    assert exc.value.retryable is True


async def test_openai_compatible_5xx_raises_provider_error() -> None:
    from app.llm.base import ProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    provider = GroqProvider("k", transport=_mock(handler))
    with pytest.raises(ProviderError):
        await provider.chat(_req())


# ── Gemini / Anthropic translation ────────────────────────────────────────────
def test_gemini_payload_translation() -> None:
    req = ChatRequest(
        model="gemini-1.5-flash",
        messages=[
            Message(role="system", content="be nice"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
        ],
    )
    payload = to_gemini_payload(req)
    assert payload["systemInstruction"]["parts"][0]["text"] == "be nice"
    assert payload["contents"][0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert payload["contents"][1]["role"] == "model"


async def test_gemini_chat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("key") == "gkey"
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 1},
            },
        )

    provider = GeminiProvider("gkey", transport=_mock(handler))
    out = await provider.chat(_req("hey"))
    assert out.content == "hi"
    assert out.usage.prompt_tokens == 4


def test_anthropic_payload_translation() -> None:
    req = ChatRequest(
        model="claude-sonnet-5",
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
    )
    payload = to_anthropic_payload(req)
    assert payload["system"] == "sys"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["max_tokens"] == req.max_tokens


async def test_anthropic_chat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "akey"
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "stop_reason": "end_turn",
            },
        )

    provider = AnthropicProvider("akey", transport=_mock(handler))
    out = await provider.chat(_req("hi"))
    assert out.content == "hello"
    assert out.usage.completion_tokens == 2


# ── Pricing ───────────────────────────────────────────────────────────────────
def test_pricing() -> None:
    assert price_for("groq", "anything") == (0, 0)
    assert compute_cost_micros("openai", "gpt-4o-mini", Usage(prompt_tokens=1000, completion_tokens=1000)) == 750
    assert compute_cost_micros("groq", "llama", Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000)) == 0


# ── Fallback ──────────────────────────────────────────────────────────────────
async def test_fallback_serves_from_second_provider() -> None:
    out = await run_with_fallback([FailingProvider(), FakeChatProvider()], _req("hi"))
    assert out.content == "echo: hi"


async def test_fallback_all_fail_raises() -> None:
    with pytest.raises(AppError):
        await run_with_fallback([FailingProvider(), FailingProvider()], _req("hi"))


# ── Streaming fallback chain (NFR-4) ──────────────────────────────────────────
class _StreamProvider:
    """Minimal ChatProvider: streams `tokens`, or raises before/after emitting."""

    def __init__(self, name: str, tokens: list[str] | None = None, fail: Exception | None = None,
                 fail_after: int = 0) -> None:
        self.name = name
        self._tokens = tokens or []
        self._fail = fail
        self._fail_after = fail_after
        self.calls = 0

    def supports_tools(self) -> bool:
        return True

    async def list_models(self):  # pragma: no cover - unused in these tests
        return []

    async def chat(self, req: ChatRequest):
        self.calls += 1
        if self._fail:
            raise self._fail
        from app.llm.types import ChatResponse

        return ChatResponse(content="".join(self._tokens), model=req.model, provider=self.name)

    async def stream(self, req: ChatRequest):
        self.calls += 1
        from app.llm.types import StreamEvent

        for i, tok in enumerate(self._tokens):
            if self._fail and i == self._fail_after:
                raise self._fail
            yield StreamEvent(type="token", delta=tok)
        if self._fail and self._fail_after >= len(self._tokens):
            raise self._fail
        yield StreamEvent(type="done", usage=Usage(prompt_tokens=1, completion_tokens=1))


async def test_fallback_switches_when_primary_fails_before_output() -> None:
    from app.llm.base import ProviderError
    from app.llm.fallback import FallbackChatProvider

    primary = _StreamProvider(
        "groq", ["never"], fail=ProviderError("503", retryable=True, status=503), fail_after=0
    )
    backup = _StreamProvider("gemini", ["Hel", "lo"])
    chain = FallbackChatProvider([(primary, "m1"), (backup, "m2")])

    events = [e async for e in chain.stream(_req("hi"))]
    assert "".join(e.delta or "" for e in events if e.type == "token") == "Hello"
    assert backup.calls == 1
    # Usage must be attributed to the provider that actually answered.
    assert chain.name == "gemini"
    assert chain.active_model == "m2"


async def test_fallback_does_not_switch_after_output_started() -> None:
    """Restarting mid-reply would duplicate or contradict text the user already saw."""
    from app.llm.base import ProviderError
    from app.llm.fallback import FallbackChatProvider

    primary = _StreamProvider(
        "groq", ["par", "tial"], fail=ProviderError("boom", retryable=True, status=503), fail_after=1
    )
    backup = _StreamProvider("gemini", ["full"])
    chain = FallbackChatProvider([(primary, None), (backup, None)])

    with pytest.raises(ProviderError):
        [e async for e in chain.stream(_req("hi"))]
    assert backup.calls == 0


async def test_fallback_skipped_for_request_level_errors() -> None:
    """A 400 is a malformed request — it fails identically everywhere, so don't burn the chain."""
    from app.llm.base import ProviderError
    from app.llm.fallback import FallbackChatProvider

    primary = _StreamProvider(
        "groq", ["x"], fail=ProviderError("bad request", retryable=False, status=400), fail_after=0
    )
    backup = _StreamProvider("gemini", ["ok"])
    chain = FallbackChatProvider([(primary, None), (backup, None)])

    with pytest.raises(ProviderError):
        [e async for e in chain.stream(_req("hi"))]
    assert backup.calls == 0


async def test_fallback_switches_on_auth_failure() -> None:
    """A 401 is not retryable on the same provider but is exactly when the backup should run."""
    from app.llm.base import ProviderError
    from app.llm.fallback import FallbackChatProvider

    primary = _StreamProvider(
        "openai", ["x"], fail=ProviderError("401 bad key", retryable=False, status=401), fail_after=0
    )
    backup = _StreamProvider("groq", ["hi"])
    chain = FallbackChatProvider([(primary, None), (backup, None)])

    events = [e async for e in chain.stream(_req("hi"))]
    assert "".join(e.delta or "" for e in events if e.type == "token") == "hi"
    assert chain.name == "groq"


async def test_fallback_reports_tool_support_conservatively() -> None:
    from app.llm.fallback import FallbackChatProvider, build_fallback_chain

    class _NoTools(_StreamProvider):
        def supports_tools(self) -> bool:
            return False

    both = FallbackChatProvider([(_StreamProvider("a"), None), (_StreamProvider("b"), None)])
    assert both.supports_tools() is True
    mixed = FallbackChatProvider([(_StreamProvider("a"), None), (_NoTools("b"), None)])
    assert mixed.supports_tools() is False

    # No fallbacks configured → the primary is returned untouched (no wrapper overhead).
    primary = _StreamProvider("solo")
    assert build_fallback_chain(primary, "m", []) is primary
