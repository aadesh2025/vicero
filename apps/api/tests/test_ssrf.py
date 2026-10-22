"""SSRF guard: which destinations are refused, and that a redirect cannot smuggle one past it."""

from __future__ import annotations

import httpx
import pytest

from app.core.ssrf import is_blocked_host
from app.rag import crawler
from app.rag.loaders import LoaderError, load_url


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "::1", "0.0.0.0"],
)
def test_private_loopback_and_metadata_addresses_are_blocked(host: str) -> None:
    assert is_blocked_host(host) is True


def test_public_address_is_allowed() -> None:
    assert is_blocked_host("8.8.8.8") is False


@pytest.fixture
def internal_host_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real guard skips DNS when a test transport is injected; block one hostname by name."""

    async def fake(host: str, transport: httpx.AsyncBaseTransport | None) -> bool:
        return host == "internal.example"

    # Every URL fetch (single page and whole-site crawl) goes through crawler.fetch -> crawler._blocked.
    monkeypatch.setattr(crawler, "_blocked", fake)


async def test_redirect_to_a_blocked_host_is_refused(internal_host_blocked: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        return httpx.Response(200, text="SECRET", headers={"content-type": "text/plain"})

    with pytest.raises(LoaderError, match="private/loopback"):
        await load_url("https://public.example/start", transport=httpx.MockTransport(handler))
    assert seen == ["https://public.example/start"]  # the blocked hop was never requested


async def test_relative_redirect_to_a_public_host_is_followed(internal_host_blocked: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/final"})
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    text = await load_url("https://public.example/start", transport=httpx.MockTransport(handler))
    assert text == "ok"


async def test_redirect_loop_is_capped(internal_host_blocked: None) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://public.example/again"})

    with pytest.raises(LoaderError, match="Too many redirects"):
        await load_url("https://public.example/again", transport=httpx.MockTransport(handler))


async def test_redirect_to_a_non_http_scheme_is_refused(internal_host_blocked: None) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    with pytest.raises(LoaderError, match="http"):
        await load_url("https://public.example/x", transport=httpx.MockTransport(handler))
