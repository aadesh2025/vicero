"""ADR-123: a model can fall back to another model of the SAME provider, and a failed model call is logged."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import structlog

from app.chat.runtime import TurnResult, run_turn
from app.llm.base import ProviderError
from app.llm.fallback import FallbackChatProvider
from app.llm.registry import get_chat_provider_chain
from app.llm.types import ChatRequest, Message, ModelInfo, StreamEvent


class _Provider:
    def __init__(self, name: str, *, fail: ProviderError | None = None, text: str = "ok") -> None:
        self.name = name
        self.active_model: str | None = None
        self.fail = fail
        self.text = text
        self.models_called: list[str | None] = []

    async def chat(self, req: ChatRequest) -> Any:  # pragma: no cover - not used
        raise NotImplementedError

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.models_called.append(req.model)
        if self.fail is not None:
            raise self.fail
        yield StreamEvent(type="token", delta=self.text)
        yield StreamEvent(type="done", finish_reason="stop")

    def supports_tools(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return []


def _req() -> ChatRequest:
    return ChatRequest(model="gpt-oss-120b", messages=[Message(role="user", content="hello")])


async def _chain(model_config: dict[str, Any]) -> Any:
    shared = _Provider("groq")

    async def resolve(_session: Any, _org: Any, name: str, *, agent_id: Any = None) -> Any:
        return shared

    return await get_chat_provider_chain(None, uuid.uuid4(), model_config, resolve=resolve)  # type: ignore[arg-type]


async def test_a_different_model_of_the_same_provider_is_a_fallback() -> None:
    chain = await _chain(
        {
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "fallbacks": [
                {"provider": "groq", "model": "llama-3.3-70b-versatile"},
                {"provider": "groq", "model": "openai/gpt-oss-20b"},
            ],
        }
    )
    assert isinstance(chain, FallbackChatProvider)
    assert [m for _p, m in chain._chain] == [
        "openai/gpt-oss-120b",
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-20b",
    ]


async def test_the_primary_model_is_not_its_own_fallback() -> None:
    chain = await _chain(
        {
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "fallbacks": [
                {"provider": "groq", "model": "openai/gpt-oss-120b"},  # same model: pointless
                {"provider": "groq"},  # same provider, no model: pointless
            ],
        }
    )
    assert not isinstance(chain, FallbackChatProvider)


async def test_a_rate_limited_model_falls_back_to_the_second_model() -> None:
    limited = ProviderError("provider returned 429: rate limit", retryable=True, status=429)

    class _PerModel(_Provider):
        async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
            self.models_called.append(req.model)
            if req.model == "openai/gpt-oss-120b":
                raise limited
            yield StreamEvent(type="token", delta="from the second model")
            yield StreamEvent(type="done", finish_reason="stop")

    p = _PerModel("groq")
    chain = FallbackChatProvider([(p, "openai/gpt-oss-120b"), (p, "llama-3.3-70b-versatile")])
    events = [e async for e in chain.stream(_req())]
    assert "".join(e.delta or "" for e in events if e.type == "token") == "from the second model"
    assert p.models_called == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    assert chain.active_model == "llama-3.3-70b-versatile"


@pytest.mark.parametrize(
    ("error", "kind", "has_detail"),
    [
        (ProviderError("provider returned 429: TPM limit 8000", status=429), "rate_limited", True),
        (ProviderError("provider returned 503: down", status=503), "provider_error", True),
        (ProviderError("network error: name resolution"), "network_or_timeout", True),
        (ProviderError("bad request: 'card 4111 1111 1111 1111'", retryable=False, status=400), "rejected", False),
    ],
)
async def test_a_failed_model_call_is_logged_without_customer_text(
    error: ProviderError, kind: str, has_detail: bool
) -> None:
    provider = _Provider("groq", fail=error)
    result = TurnResult()
    with structlog.testing.capture_logs() as logs:
        _ = [ev async for ev in run_turn(provider, _req(), [], result, fallback_message="fallback line")]
    failed = [e for e in logs if e["event"] == "llm_call_failed"]
    assert len(failed) == 1
    entry = failed[0]
    assert entry["kind"] == kind and entry["status"] == error.status and entry["provider"] == "groq"
    assert bool(entry["detail"]) is has_detail
    assert "4111" not in str(entry), "a 400 can echo the customer's text; it must not reach the log"
    assert result.content == "fallback line"
