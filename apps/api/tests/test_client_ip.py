"""The client's real address behind a proxy (docs/SECURITY.md §11) — and the per-IP limits it feeds.

Two things must hold together: different visitors behind one proxy get separate limits, and a
caller who can reach the API directly cannot pick its own "IP" by sending a header.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clientip import _parse_networks, resolve_client
from app.core.config import settings
from app.db.session import get_session
from app.main import create_app
from tests.selfserve_helpers import STRONG, unique_email


def _req(peer: str | None, xff: str | None = None) -> SimpleNamespace:
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None,
        headers=SimpleNamespace(get=lambda k, d="": headers.get(k, d)),
    )


@pytest.fixture(autouse=True)
def _proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1,::1,10.0.0.0/8")
    _parse_networks.cache_clear()
    yield  # type: ignore[misc]
    _parse_networks.cache_clear()


# ── the resolver ─────────────────────────────────────────────────────────────
def test_a_trusted_proxy_vouches_for_the_forwarded_address() -> None:
    client = resolve_client(_req("10.1.2.3", "203.0.113.7"))
    assert (client.ip, client.known) == ("203.0.113.7", True)


def test_two_visitors_behind_the_same_proxy_are_told_apart() -> None:
    a = resolve_client(_req("10.1.2.3", "203.0.113.7"))
    b = resolve_client(_req("10.1.2.3", "198.51.100.9"))
    assert a.ip != b.ip


def test_the_header_is_ignored_from_a_peer_that_is_not_a_trusted_proxy() -> None:
    client = resolve_client(_req("198.51.100.50", "9.9.9.9"))
    assert (client.ip, client.known) == ("198.51.100.50", True)  # its own address, not the claim


def test_a_client_cannot_win_by_prepending_addresses() -> None:
    # The client sent "9.9.9.9"; our proxy appended the address it actually saw.
    assert resolve_client(_req("10.1.2.3", "9.9.9.9, 203.0.113.7")).ip == "203.0.113.7"


def test_our_own_proxies_are_skipped_from_the_right() -> None:
    # web app (10.x) forwarded a chain the edge proxy had already extended.
    assert resolve_client(_req("10.1.2.3", "203.0.113.7, 10.4.4.4, 10.5.5.5")).ip == "203.0.113.7"


def test_garbage_entries_are_skipped_not_believed() -> None:
    client = resolve_client(_req("10.1.2.3", "203.0.113.7, not-an-ip, <script>"))
    assert client.ip == "203.0.113.7"


def test_ports_and_ipv4_mapped_ipv6_are_normalised() -> None:
    assert resolve_client(_req("10.1.2.3", "203.0.113.7:51234")).ip == "203.0.113.7"
    assert resolve_client(_req("10.1.2.3", "::ffff:203.0.113.7")).ip == "203.0.113.7"
    assert resolve_client(_req("10.1.2.3", "[2001:db8::5]:443")).ip == "2001:db8::5"


def test_a_trusted_proxy_that_forwarded_nothing_leaves_the_client_unknown() -> None:
    client = resolve_client(_req("10.1.2.3"))
    assert (client.ip, client.known) == ("10.1.2.3", False)
    all_ours = resolve_client(_req("10.1.2.3", "10.9.9.9, 127.0.0.1"))
    assert all_ours.known is False


def test_a_direct_loopback_caller_is_not_a_known_client() -> None:
    assert resolve_client(_req("127.0.0.1")).known is False


def test_no_peer_is_unknown_not_an_error() -> None:
    assert resolve_client(_req(None)).known is False


# ── through the real limiters ────────────────────────────────────────────────
def _client_for(db_session: AsyncSession, peer: str) -> AsyncClient:
    app = create_app()

    async def _session():  # type: ignore[no-untyped-def]
        yield db_session

    app.dependency_overrides[get_session] = _session
    return AsyncClient(transport=ASGITransport(app=app, client=(peer, 5000)), base_url="http://t")


@pytest.fixture
async def api(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """A client whose TCP peer is `127.0.0.1` (a trusted proxy in these tests)."""
    async with _client_for(db_session, "127.0.0.1") as c:
        yield c


@pytest.fixture
async def stranger(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """A client reaching the API directly from a public address that is NOT a trusted proxy."""
    async with _client_for(db_session, "198.51.100.50") as c:
        yield c


def _xff(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


async def test_route_limits_are_per_visitor_not_per_proxy(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit", 2)
    for _ in range(2):
        r = await api.post("/v1/auth/password/forgot", json={"email": unique_email()}, headers=_xff("203.0.113.7"))
        assert r.status_code == 200
    blocked = await api.post("/v1/auth/password/forgot", json={"email": unique_email()}, headers=_xff("203.0.113.7"))
    assert blocked.status_code == 429  # visitor A is over its limit...
    other = await api.post("/v1/auth/password/forgot", json={"email": unique_email()}, headers=_xff("198.51.100.9"))
    assert other.status_code == 200  # ...and visitor B, behind the same proxy, is not affected


async def test_a_spoofed_header_from_an_untrusted_source_does_not_dodge_the_limit(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit", 2)
    monkeypatch.setattr(settings, "trusted_proxies", "10.9.9.9")  # the caller (127.0.0.1) is NOT trusted
    _parse_networks.cache_clear()
    codes = []
    for i in range(4):  # a different claimed "IP" every time
        headers = _xff(f"203.0.113.{i + 1}")
        r = await api.post("/v1/auth/password/forgot", json={"email": unique_email()}, headers=headers)
        codes.append(r.status_code)
    assert codes == [200, 200, 429, 429]


async def test_the_signup_cap_is_per_visitor(
    api: AsyncClient, self_serve: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "signups_per_ip_per_day", 2)
    codes = []
    for ip in ["203.0.113.7"] * 3 + ["198.51.100.9"]:
        r = await api.post(
            "/v1/auth/signup", json={"email": unique_email("cap"), "password": STRONG}, headers=_xff(ip)
        )
        codes.append(r.status_code)
    # Visitor A is capped on the third; visitor B, behind the same proxy, is untouched.
    assert codes == [200, 200, 429, 200]


async def test_the_signup_cap_ignores_a_spoofed_header_from_an_untrusted_source(
    stranger: AsyncClient, self_serve: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "signups_per_ip_per_day", 2)
    codes = []
    for i in range(1, 4):  # claims a fresh address every time; the header must be ignored
        r = await stranger.post(
            "/v1/auth/signup",
            json={"email": unique_email("spoof"), "password": STRONG},
            headers=_xff(f"192.0.2.{i}"),
        )
        codes.append(r.status_code)
    assert codes == [200, 200, 429]


async def test_an_unidentifiable_client_is_not_pooled_into_one_capped_bucket(
    api: AsyncClient, self_serve: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local dev: the web app sees no proxy, so the API can't tell visitors apart. Capping them
    all as one is what blocked every dev signup after three; the cap must step aside instead."""
    monkeypatch.setattr(settings, "signups_per_ip_per_day", 2)
    for _ in range(4):
        r = await api.post("/v1/auth/signup", json={"email": unique_email("dev"), "password": STRONG})
        assert r.status_code == 200
