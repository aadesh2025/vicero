"""Egress guard v2 (docs/28 §12): the SSRF matrix, DNS pinning, redirects, body cap."""

from __future__ import annotations

import httpx
import pytest

from app.core.egress import EgressBlocked, EgressError, host_allowed, safe_request


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"host": request.headers["host"], "url": str(request.url)})


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://192.168.0.9/",
        "http://172.16.5.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f000001/",  # hex
        "http://0177.0.0.1/",  # octal
        "http://127.1/",  # short form
        "http://localhost/",
        "http://localhost./",  # trailing dot
        "http://api.localhost/",
    ],
)
async def test_private_destinations_are_refused(url: str, dns: dict[str, list[str]]) -> None:
    with pytest.raises(EgressBlocked):
        await safe_request("GET", url, transport=httpx.MockTransport(_ok))


@pytest.mark.parametrize("url", ["ftp://example.com/", "file:///etc/passwd", "gopher://example.com/", "http:///x"])
async def test_non_http_schemes_are_refused(url: str, dns: dict[str, list[str]]) -> None:
    with pytest.raises(EgressBlocked):
        await safe_request("GET", url, transport=httpx.MockTransport(_ok))


async def test_credentials_in_url_are_refused(dns: dict[str, list[str]]) -> None:
    with pytest.raises(EgressBlocked):
        await safe_request("GET", "http://user:pw@example.com/", transport=httpx.MockTransport(_ok))


async def test_a_name_resolving_to_loopback_is_refused(dns: dict[str, list[str]]) -> None:
    dns["evil.example"] = ["127.0.0.1"]
    with pytest.raises(EgressBlocked):
        await safe_request("GET", "https://evil.example/", transport=httpx.MockTransport(_ok))


async def test_one_private_record_among_public_ones_is_refused(dns: dict[str, list[str]]) -> None:
    dns["mixed.example"] = ["93.184.216.34", "10.0.0.7"]
    with pytest.raises(EgressBlocked):
        await safe_request("GET", "https://mixed.example/", transport=httpx.MockTransport(_ok))


async def test_the_request_is_pinned_to_the_resolved_ip(dns: dict[str, list[str]]) -> None:
    """Rebinding defence: the connection goes to the IP we validated, with the original Host."""
    dns["api.example.com"] = ["93.184.216.34"]
    resp = await safe_request("GET", "https://api.example.com/v1/x?a=1", transport=httpx.MockTransport(_ok))
    body = resp.json()
    assert body["host"] == "api.example.com"
    assert "93.184.216.34" in body["url"] and "api.example.com" not in body["url"]
    assert body["url"].endswith("/v1/x?a=1")


async def test_redirects_are_not_followed_by_default(dns: dict[str, list[str]]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    resp = await safe_request("GET", "https://example.com/", transport=httpx.MockTransport(handler))
    assert resp.status_code == 302 and resp.redirects == []


async def test_every_redirect_hop_is_revalidated(dns: dict[str, list[str]]) -> None:
    dns["internal.example"] = ["10.0.0.5"]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["host"])
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})

    with pytest.raises(EgressBlocked):
        await safe_request("GET", "https://example.com/", max_redirects=3, transport=httpx.MockTransport(handler))
    assert seen == ["example.com"]  # the private hop was never requested


async def test_credentials_are_dropped_on_a_cross_host_redirect(dns: dict[str, list[str]]) -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        if request.headers["host"] == "a.example.com":
            return httpx.Response(302, headers={"location": "https://b.example.com/x"})
        return httpx.Response(200, json={})

    await safe_request(
        "GET",
        "https://a.example.com/",
        headers={"Authorization": "Bearer s3cret", "X-Other": "1"},
        max_redirects=2,
        transport=httpx.MockTransport(handler),
    )
    assert seen[0]["authorization"] == "Bearer s3cret"
    assert "authorization" not in seen[1] and seen[1]["x-other"] == "1"


async def test_redirect_loop_is_bounded(dns: dict[str, list[str]]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/again"})

    resp = await safe_request("GET", "https://example.com/", max_redirects=99, transport=httpx.MockTransport(handler))
    assert resp.status_code == 302 and len(resp.redirects) == 3


async def test_body_is_cut_at_max_bytes(dns: dict[str, list[str]]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 10_000)

    resp = await safe_request("GET", "https://example.com/", max_bytes=100, transport=httpx.MockTransport(handler))
    assert len(resp.body) == 100 and resp.truncated is True


async def test_header_injection_is_stripped(dns: dict[str, list[str]]) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    await safe_request(
        "GET",
        "https://example.com/",
        headers={"X-A": "ok\r\nX-Evil: 1", "Host": "evil.example", "Content-Length": "9"},
        transport=httpx.MockTransport(handler),
    )
    assert "x-evil" not in seen
    assert seen["host"] == "example.com"
    assert seen["x-a"] == "okX-Evil: 1"


async def test_timeout_is_an_egress_error(dns: dict[str, list[str]]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(EgressError):
        await safe_request("GET", "https://example.com/", transport=httpx.MockTransport(handler))


def test_allowed_hosts_matching() -> None:
    assert host_allowed("api.example.com", None)
    assert host_allowed("api.example.com", ["api.example.com"])
    assert host_allowed("API.example.com.", ["api.example.com"])
    assert not host_allowed("evil.com", ["api.example.com"])
    assert host_allowed("a.example.com", ["*.example.com"])
    assert not host_allowed("example.com", ["*.example.com"])
    assert not host_allowed("badexample.com", ["*.example.com"])


async def test_allowed_hosts_never_bypass_the_ip_check(dns: dict[str, list[str]]) -> None:
    with pytest.raises(EgressBlocked):
        await safe_request(
            "GET", "http://127.0.0.1/", allowed_hosts=["127.0.0.1"], transport=httpx.MockTransport(_ok)
        )


async def test_host_outside_allowlist_is_refused(dns: dict[str, list[str]]) -> None:
    with pytest.raises(EgressBlocked):
        await safe_request(
            "GET", "https://other.example/", allowed_hosts=["api.example.com"], transport=httpx.MockTransport(_ok)
        )
