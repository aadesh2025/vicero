"""CORS for the embeddable widget: /v1/public/* is open to any site, the rest of the API stays strict."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.modules.public import cors as public_cors
from app.modules.public import origins

DASHBOARD = "https://viceroai.duckdns.org"


@pytest.fixture(autouse=True)
def _dashboard_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cors_origins", DASHBOARD)


async def _agent(client: AsyncClient, email: str) -> tuple[str, dict[str, str], str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "Zq8-lunar-Kettle-41"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "CorsOrg"}, headers={"Authorization": f"Bearer {token}"})
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    agent = await client.post("/v1/agents", json={"name": "Cors Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": "fake", "model": "fake-1"}, "features": {"tools_enabled": False}},
        headers=headers,
    )
    return str(agent.json()["public_key"]), headers, aid


def _preflight(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }


# ── open to any site, without credentials ────────────────────────────────────────────


async def test_a_preflight_from_any_site_to_chat_is_allowed_without_credentials(client: AsyncClient) -> None:
    key, _, _ = await _agent(client, "cors.open@example.com")
    r = await client.options(f"/v1/public/agents/{key}/chat", headers=_preflight("https://example.com"))
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://example.com"
    assert "access-control-allow-credentials" not in r.headers
    assert r.headers["access-control-allow-methods"] == "GET, POST, OPTIONS"
    assert r.headers["access-control-allow-headers"] == "*"


async def test_the_actual_requests_carry_the_origin_and_never_credentials(client: AsyncClient) -> None:
    key, _, _ = await _agent(client, "cors.actual@example.com")
    cfg = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": "https://example.com"})
    assert cfg.status_code == 200
    assert cfg.headers["access-control-allow-origin"] == "https://example.com"
    assert "access-control-allow-credentials" not in cfg.headers
    chat = await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": "hello", "stream": False, "visitor": {"id": "v1"}},
        headers={"Origin": "https://example.com"},
    )
    assert chat.status_code == 200
    assert chat.headers["access-control-allow-origin"] == "https://example.com"
    assert "access-control-allow-credentials" not in chat.headers


async def test_a_dashboard_origin_on_a_public_route_gets_no_credentials_either(client: AsyncClient) -> None:
    key, _, _ = await _agent(client, "cors.dash.public@example.com")
    r = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": DASHBOARD})
    assert r.headers["access-control-allow-origin"] == DASHBOARD
    assert "access-control-allow-credentials" not in r.headers


async def test_cookies_and_authorization_never_reach_a_public_route() -> None:
    seen: dict[str, Any] = {}

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen["headers"] = dict(scope["headers"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/public/agents/k/widget-logo",
        "headers": [(b"origin", b"https://example.com"), (b"cookie", b"s=1"), (b"authorization", b"Bearer x")],
    }
    sent: list[dict[str, Any]] = []

    async def send(m: dict[str, Any]) -> None:
        sent.append(m)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    await public_cors.PublicCorsMiddleware(inner)(scope, receive, send)
    assert not ({b"cookie", b"authorization", b"origin"} & set(seen["headers"]))
    assert (b"access-control-allow-origin", b"https://example.com") in sent[0]["headers"]
    assert not any(k == b"access-control-allow-credentials" for k, _ in sent[0]["headers"])


# ── the rest of the API stays strict ─────────────────────────────────────────────────


async def test_the_login_preflight_is_still_strict(client: AsyncClient) -> None:
    r = await client.options("/v1/auth/login", headers=_preflight("https://example.com"))
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


async def test_the_dashboard_origin_still_works_on_the_strict_api(client: AsyncClient) -> None:
    r = await client.options("/v1/auth/login", headers=_preflight(DASHBOARD))
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == DASHBOARD
    assert r.headers["access-control-allow-credentials"] == "true"


# ── per-agent allowed websites ───────────────────────────────────────────────────────


async def _allow(client: AsyncClient, headers: dict[str, str], aid: str, sites: list[str] | None) -> Any:
    return await client.patch(f"/v1/agents/{aid}/widget-config", json={"allowedSites": sites}, headers=headers)


async def test_with_an_allow_list_only_listed_sites_get_through(client: AsyncClient) -> None:
    key, headers, aid = await _agent(client, "cors.list@example.com")
    saved = await _allow(client, headers, aid, ["https://Customer.com/pricing", "shop.example.org"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["allowedSites"] == ["customer.com", "shop.example.org"]  # normalised

    ok = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": "https://customer.com"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://customer.com"
    sub = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": "https://www.customer.com"})
    assert sub.status_code == 200  # subdomains of a listed site
    pre = await client.options(f"/v1/public/agents/{key}/chat", headers=_preflight("https://customer.com"))
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "https://customer.com"

    bad = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": "https://example.com"})
    assert bad.status_code == 403, bad.text
    assert bad.json()["error"]["code"] == "public.origin_not_allowed"
    assert "access-control-allow-origin" not in bad.headers
    bad_chat = await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": "hi", "stream": False},
        headers={"Origin": "https://example.com"},
    )
    assert bad_chat.status_code == 403
    bad_pre = await client.options(f"/v1/public/agents/{key}/chat", headers=_preflight("https://example.com"))
    assert bad_pre.status_code == 403 and "access-control-allow-origin" not in bad_pre.headers

    lookalike = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": "https://evilcustomer.com"})
    assert lookalike.status_code == 403  # a suffix match must respect the dot boundary


async def test_the_dashboard_and_origin_less_calls_still_pass_an_allow_list(client: AsyncClient) -> None:
    key, headers, aid = await _agent(client, "cors.list2@example.com")
    await _allow(client, headers, aid, ["customer.com"])
    dash = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": DASHBOARD})
    assert dash.status_code == 200
    server = await client.get(f"/v1/public/agents/{key}/config")  # no Origin: not a cross-site browser call
    assert server.status_code == 200


async def test_an_empty_list_means_any_site_and_clearing_it_works(client: AsyncClient) -> None:
    key, headers, aid = await _agent(client, "cors.clear@example.com")
    url = f"/v1/public/agents/{key}/config"
    other = {"Origin": "https://example.com"}
    await _allow(client, headers, aid, ["customer.com"])
    assert (await client.get(url, headers=other)).status_code == 403
    assert (await _allow(client, headers, aid, [])).status_code == 200
    assert (await client.get(url, headers=other)).status_code == 200


async def test_the_allow_list_is_not_published_to_the_widget(client: AsyncClient) -> None:
    key, headers, aid = await _agent(client, "cors.hidden@example.com")
    await _allow(client, headers, aid, ["customer.com"])
    cfg = await client.get(f"/v1/public/agents/{key}/config", headers={"Origin": "https://customer.com"})
    assert "customer.com" not in cfg.text.replace("access-control", "")


async def test_a_bad_website_entry_is_rejected(client: AsyncClient) -> None:
    _, headers, aid = await _agent(client, "cors.bad@example.com")
    r = await _allow(client, headers, aid, ["not a domain!"])
    assert r.status_code in (400, 422), r.text


async def test_an_unknown_key_is_a_normal_404_with_cors_headers(client: AsyncClient) -> None:
    r = await client.get("/v1/public/agents/nope/config", headers={"Origin": "https://example.com"})
    assert r.status_code == 404
    assert r.headers["access-control-allow-origin"] == "https://example.com"


# ── websockets ───────────────────────────────────────────────────────────────────────


async def _ws(
    monkeypatch: pytest.MonkeyPatch, sites: list[str], origin: str | None, path: str
) -> tuple[bool, list[str]]:
    reached: list[bool] = []
    sent: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(True)

    async def fake_sites(app: Any, key: str) -> list[str]:
        return sites

    monkeypatch.setattr(public_cors, "allowed_sites", fake_sites)
    headers = [(b"origin", origin.encode())] if origin else []
    scope = {"type": "websocket", "path": path, "headers": headers}

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(m: dict[str, Any]) -> None:
        sent.append(m["type"])

    await public_cors.PublicCorsMiddleware(inner)(scope, receive, send)
    return bool(reached), sent


async def test_the_subscribe_socket_checks_the_origin_against_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    path = "/v1/public/agents/k/subscribe"
    assert (await _ws(monkeypatch, ["customer.com"], "https://customer.com", path))[0] is True
    reached, sent = await _ws(monkeypatch, ["customer.com"], "https://example.com", path)
    assert reached is False and sent == ["websocket.close"]
    assert (await _ws(monkeypatch, [], "https://example.com", path))[0] is True  # empty list: any site
    assert (await _ws(monkeypatch, ["customer.com"], None, path))[0] is True  # not a browser


async def test_the_chat_socket_is_checked_too(monkeypatch: pytest.MonkeyPatch) -> None:
    reached, _ = await _ws(monkeypatch, ["customer.com"], "https://example.com", "/v1/public/agents/k/ws")
    assert reached is False


# ── the matching rules ───────────────────────────────────────────────────────────────


def test_site_normalisation_and_matching() -> None:
    assert origins.normalize_sites(["HTTPS://A.com:8443/x", "*.b.org", "a.com"]) == ["a.com", "b.org"]
    with pytest.raises(ValueError):
        origins.normalize_site("   ")
    with pytest.raises(ValueError):
        origins.normalize_site("a b")
    assert origins.origin_allowed("https://a.com", [])
    assert origins.origin_allowed("http://sub.a.com:3000", ["a.com"])
    assert not origins.origin_allowed("https://nota.com", ["a.com"])
    assert not origins.origin_allowed("null", ["a.com"])
