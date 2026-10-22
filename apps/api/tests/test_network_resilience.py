"""DNS cache, provider connect retry and readable provider errors (2026-10-21 production incident).

The VPS loses 10-22% of packets upstream. Visitors saw
``network error: [Errno -3] Temporary failure in name resolution`` and slow replies. These tests pin
the three mitigations: retry + stale-serve name resolution, retry a failed connect exactly once,
and say what happened in words a person can act on.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.core import dnscache
from app.core.config import settings
from app.llm import http as llm_http
from app.llm.base import ProviderError, readable_error
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.types import ChatRequest, Message

_ADDR = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.38.236", 443))]


class FakeResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_next = 0
        self.errno = socket.EAI_AGAIN

    def __call__(self, host: Any, port: Any, *args: Any) -> Any:
        self.calls.append(host)
        if self.fail_next:
            self.fail_next -= 1
            raise socket.gaierror(self.errno, "Temporary failure in name resolution")
        return _ADDR


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeResolver]:
    fake = FakeResolver()
    dnscache.uninstall()
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    dnscache.install(ttl=300, stale_ttl=86400, retries=2, retry_delay=0)
    yield fake
    dnscache.uninstall()


def test_fresh_answer_is_served_from_memory(resolver: FakeResolver) -> None:
    assert socket.getaddrinfo("api.groq.com", 443) == _ADDR
    assert socket.getaddrinfo("api.groq.com", 443) == _ADDR
    assert resolver.calls == ["api.groq.com"]


def test_transient_failure_is_retried(resolver: FakeResolver) -> None:
    resolver.fail_next = 2  # two drops, then an answer: within the 2 retries
    assert socket.getaddrinfo("api.groq.com", 443) == _ADDR
    assert len(resolver.calls) == 3


def test_stale_answer_beats_a_failed_reply(resolver: FakeResolver, monkeypatch: pytest.MonkeyPatch) -> None:
    socket.getaddrinfo("api.groq.com", 443)
    monkeypatch.setattr(dnscache, "_ttl", 0.0)  # the cached answer is now "expired"
    resolver.fail_next = 10
    assert socket.getaddrinfo("api.groq.com", 443) == _ADDR


def test_without_a_previous_answer_the_failure_still_raises(resolver: FakeResolver) -> None:
    resolver.fail_next = 10
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("api.groq.com", 443)
    assert len(resolver.calls) == 3


def test_a_name_that_does_not_exist_is_not_retried(resolver: FakeResolver) -> None:
    resolver.fail_next = 1
    resolver.errno = socket.EAI_NONAME
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("no-such-host.example", 443)
    assert len(resolver.calls) == 1


@pytest.mark.parametrize("host", ["postgres", "redis", "localhost", "10.0.0.5", "::1"])
def test_service_names_and_literals_bypass_the_cache(resolver: FakeResolver, host: str) -> None:
    socket.getaddrinfo(host, 5432)
    socket.getaddrinfo(host, 5432)
    assert resolver.calls == [host, host]


def test_install_is_idempotent_and_uninstall_restores(resolver: FakeResolver) -> None:
    assert dnscache.install() is False
    dnscache.uninstall()
    assert socket.getaddrinfo is resolver


def test_install_from_settings_respects_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    dnscache.uninstall()
    monkeypatch.setattr(settings, "dns_cache_enabled", False)
    assert dnscache.install_from_settings() is False
    assert not dnscache.is_installed()


# ── Provider connect retry ──────────────────────────────────────────────────────────────────
def _ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}},
    )


async def test_a_failed_connect_is_retried_once_and_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution", request=request)
        assert request.headers["authorization"] == "Bearer k"
        assert str(request.url) == "https://api.example.com/v1/chat/completions"
        return _ok()

    provider = OpenAICompatibleProvider("https://api.example.com/v1", "k", transport=httpx.MockTransport(handler))
    resp = await provider.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert resp.content == "hi"
    assert calls["n"] == 2


async def test_connect_retry_gives_up_and_raises_a_network_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    provider = OpenAICompatibleProvider("https://api.example.com/v1", "k", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        await provider.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert str(info.value).startswith("network error")
    assert info.value.retryable


async def test_a_read_error_after_sending_is_not_retried_here() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadError("reset", request=request)

    provider = OpenAICompatibleProvider("https://api.example.com/v1", "k", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        await provider.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert calls["n"] == 1  # a sent request may already be billed; the fallback chain decides


async def test_stream_retries_the_connect_phase() -> None:
    calls = {"n": 0}
    sse = (
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"llo"},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("syn lost", request=request)
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    provider = OpenAICompatibleProvider("https://api.example.com/v1", "k", transport=httpx.MockTransport(handler))
    tokens = [
        ev.delta
        async for ev in provider.stream(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
        if ev.type == "token"
    ]
    assert "".join(t or "" for t in tokens) == "hello"
    assert calls["n"] == 2


async def test_shared_client_is_reused_within_a_loop() -> None:
    first = llm_http.shared_client()
    assert llm_http.shared_client() is first


def test_connect_phase_has_its_own_short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_connect_timeout_seconds", 5.0)
    t = llm_http.timeout(60.0)
    assert t.connect == 5.0 and t.read == 60.0


# ── Readable errors ─────────────────────────────────────────────────────────────────────────
def test_network_errors_read_as_words_not_errno() -> None:
    msg = readable_error(ProviderError("network error: [Errno -3] Temporary failure in name resolution"))
    assert "Errno" not in msg and "network problem" in msg


def test_rate_limit_and_too_large_say_what_to_do() -> None:
    assert "shorten the system prompt" in readable_error(ProviderError("x", status=429))
    assert "knowledge base" in readable_error(ProviderError("x", status=413))


def test_other_errors_pass_through() -> None:
    assert readable_error(ProviderError("provider returned 401: bad key", status=401)) == (
        "provider returned 401: bad key"
    )
