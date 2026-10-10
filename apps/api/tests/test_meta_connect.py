"""Meta one-click connect (ADR-113): shared webhook, WhatsApp/Messenger/Instagram connect, disconnect,
deauthorize, token health and the outbound guard. The Graph API is mocked; nothing here reaches Meta."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel, meta_connect, meta_graph, meta_health, meta_session, meta_webhook
from app.core.config import settings
from app.core.crypto import decrypt
from app.core.errors import AppError
from app.models import AuditLog, Channel
from tests.test_channels import _capture_transport, _fake_agent, _headers

APP_ID = "424242"
SECRET = "meta-app-secret-for-tests"
VERIFY = "shared-verify-token"


# ── scaffolding ───────────────────────────────────────────────────────────────────
class FakeGraph:
    """Records every Graph call and answers from a (method, path) table."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], tuple[int, Any]] = {}
        self.calls: list[dict[str, Any]] = []

    def on(self, method: str, path: str, body: Any, status: int = 200) -> None:
        self.routes[(method, path)] = (status, body)

    def called(self, method: str, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if (c["method"], c["path"]) == (method, path)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = "/" + request.url.path.split("/", 2)[2]  # drop the /v23.0 version segment
        form = {k: v[0] for k, v in urllib.parse.parse_qs(request.content.decode()).items()}
        self.calls.append(
            {
                "method": request.method,
                "path": path,
                "params": dict(request.url.params),
                "form": form,
                "auth": request.headers.get("authorization", ""),
            }
        )
        status, body = self.routes.get(
            (request.method, path), (404, {"error": {"message": f"no route {path}", "code": 1}})
        )
        return httpx.Response(status, json=body)


@pytest.fixture
def graph() -> Iterator[FakeGraph]:
    fake = FakeGraph()
    meta_graph.transport = httpx.MockTransport(fake.handler)
    yield fake
    meta_graph.transport = None


@pytest.fixture(autouse=True)
def _meta_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, dict[str, Any]]]]:
    monkeypatch.setattr(settings, "meta_app_id", APP_ID)
    monkeypatch.setattr(settings, "meta_app_secret", SECRET)
    monkeypatch.setattr(settings, "meta_verify_token", VERIFY)
    monkeypatch.setattr(settings, "meta_embedded_signup_config_id", "CFG1")
    queued: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "app.channels.meta_router.enqueue_meta_inbound", lambda cid, payload: queued.append((cid, payload))
    )
    meta_session._memory.clear()
    yield queued


def _sig(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


async def _post_webhook(client: AsyncClient, payload: dict[str, Any], secret: str = SECRET) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(
        "/api/meta/webhook", content=body, headers={**_sig(body, secret), "Content-Type": "application/json"}
    )


def _wa_payload(phone_number_id: str, *messages: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1", "phone_number_id": phone_number_id},
                            "contacts": [{"profile": {"name": "Asha"}, "wa_id": "919999"}],
                            "messages": list(messages),
                        },
                    }
                ],
            }
        ],
    }


def _wa_msg(text: str = "hi") -> dict[str, Any]:
    return {"from": "919999", "id": f"wamid.{uuid.uuid4().hex}", "type": "text", "text": {"body": text}}


def _messaging_payload(obj: str, entry_id: str, text: str = "hello", *, echo: bool = False) -> dict[str, Any]:
    message: dict[str, Any] = {"mid": f"m_{uuid.uuid4().hex}", "text": text}
    if echo:
        message["is_echo"] = True
    return {
        "object": obj,
        "entry": [
            {
                "id": entry_id,
                "time": 1,
                "messaging": [{"sender": {"id": "USER1"}, "recipient": {"id": entry_id}, "message": message}],
            }
        ],
    }


async def _seed_channel(
    client: AsyncClient,
    db_session: AsyncSession,
    ctype: str,
    external_id: str,
    email: str,
    *,
    config: dict[str, Any] | None = None,
    status: str = "active",
) -> tuple[Channel, dict[str, str], str]:
    headers = await _headers(client, email)
    aid = await _fake_agent(client, headers)
    channel = Channel(
        organization_id=uuid.UUID(headers["X-Org-Id"]),
        agent_id=uuid.UUID(aid),
        type=ctype,
        enabled=True,
        external_id=external_id,
        status=status,
        config=config or {},
        webhook_secret="x",
    )
    db_session.add(channel)
    await db_session.flush()
    return channel, headers, aid


def _signed_request(payload: dict[str, Any], secret: str = SECRET) -> str:
    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    part = b64(json.dumps(payload).encode())
    return b64(hmac.new(secret.encode(), part.encode(), hashlib.sha256).digest()) + "." + part


# ── config endpoint ───────────────────────────────────────────────────────────────
async def test_config_enabled_has_no_secrets(client: AsyncClient) -> None:
    headers = await _headers(client, "cfg@example.com")
    r = await client.get("/v1/channels/meta/config", headers=headers)
    assert r.status_code == 200
    assert r.json() == {
        "enabled": True,
        "whatsapp_enabled": True,
        "app_id": APP_ID,
        "config_id": "CFG1",
        "graph_version": settings.meta_graph_version,
        "app_live": False,
    }
    assert SECRET not in r.text and VERIFY not in r.text


async def test_config_disabled_when_any_required_value_missing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _headers(client, "cfg2@example.com")
    monkeypatch.setattr(settings, "meta_verify_token", "")
    body = (await client.get("/v1/channels/meta/config", headers=headers)).json()
    assert body["enabled"] is False and body["app_id"] is None and body["config_id"] is None

    monkeypatch.setattr(settings, "meta_verify_token", VERIFY)
    monkeypatch.setattr(settings, "meta_embedded_signup_config_id", "")
    body = (await client.get("/v1/channels/meta/config", headers=headers)).json()
    assert body["enabled"] is True and body["whatsapp_enabled"] is False


async def test_connect_requires_auth_and_feature(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (await client.get("/v1/channels/meta/config")).status_code in (401, 403)
    headers = await _headers(client, "off@example.com")
    aid = await _fake_agent(client, headers)
    monkeypatch.setattr(settings, "meta_app_id", "")
    r = await client.post(
        "/v1/channels/meta/whatsapp/connect",
        json={"agent_id": aid, "code": "c", "waba_id": "1", "phone_number_id": "2"},
        headers=headers,
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "meta.not_configured"


# ── shared webhook: handshake + signature ─────────────────────────────────────────
async def test_webhook_handshake(client: AsyncClient) -> None:
    ok = await client.get(
        "/api/meta/webhook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1234"}
    )
    assert ok.status_code == 200 and ok.text == "1234"
    bad = await client.get(
        "/api/meta/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
    )
    assert bad.status_code == 403
    wrong_mode = await client.get(
        "/api/meta/webhook", params={"hub.mode": "x", "hub.verify_token": VERIFY, "hub.challenge": "1"}
    )
    assert wrong_mode.status_code == 403


async def test_webhook_handshake_fails_closed_without_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "meta_verify_token", "")
    r = await client.get(
        "/api/meta/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
    )
    assert r.status_code == 403


async def test_webhook_signature_valid_invalid_missing(client: AsyncClient) -> None:
    payload = _wa_payload("NOBODY", _wa_msg())
    assert (await _post_webhook(client, payload)).status_code == 200
    assert (await _post_webhook(client, payload, secret="wrong")).status_code == 401
    body = json.dumps(payload).encode()
    assert (await client.post("/api/meta/webhook", content=body)).status_code == 401
    assert (
        await client.post("/api/meta/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=bad"})
    ).status_code == 401


async def test_webhook_refuses_everything_without_app_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "meta_app_secret", "")
    body = b"{}"
    r = await client.post("/api/meta/webhook", content=body, headers=_sig(body, ""))
    assert r.status_code == 503


# ── shared webhook: routing ───────────────────────────────────────────────────────
async def test_routes_whatsapp_page_and_instagram(
    client: AsyncClient, db_session: AsyncSession, _meta_settings: list[tuple[str, dict[str, Any]]]
) -> None:
    wa, *_ = await _seed_channel(client, db_session, "whatsapp", "PN100", "r1@example.com")
    fb, *_ = await _seed_channel(client, db_session, "facebook", "PG100", "r2@example.com")
    ig, *_ = await _seed_channel(client, db_session, "instagram", "IG100", "r3@example.com")

    assert (await _post_webhook(client, _wa_payload("PN100", _wa_msg("a")))).status_code == 200
    assert (await _post_webhook(client, _messaging_payload("page", "PG100", "b"))).status_code == 200
    assert (await _post_webhook(client, _messaging_payload("instagram", "IG100", "c"))).status_code == 200

    routed = {cid: payload for cid, payload in _meta_settings}
    assert set(routed) == {str(wa.id), str(fb.id), str(ig.id)}
    # The routed payload is what the existing adapter parses, unchanged.
    assert get_channel("whatsapp").parse_inbound(wa, routed[str(wa.id)]).text == "a"  # type: ignore[union-attr]
    assert get_channel("facebook").parse_inbound(fb, routed[str(fb.id)]).text == "b"  # type: ignore[union-attr]
    assert get_channel("instagram").parse_inbound(ig, routed[str(ig.id)]).text == "c"  # type: ignore[union-attr]


async def test_object_decides_channel_type(
    client: AsyncClient, db_session: AsyncSession, _meta_settings: list[tuple[str, dict[str, Any]]]
) -> None:
    """The same id under another surface must not reach a channel of a different type."""
    await _seed_channel(client, db_session, "facebook", "SAME1", "t1@example.com")
    assert (await _post_webhook(client, _messaging_payload("instagram", "SAME1"))).status_code == 200
    assert _meta_settings == []


async def test_unknown_id_is_ignored_with_200(
    client: AsyncClient, _meta_settings: list[tuple[str, dict[str, Any]]]
) -> None:
    for payload in (
        _wa_payload("UNKNOWN", _wa_msg()),
        _messaging_payload("page", "UNKNOWN"),
        _messaging_payload("instagram", "UNKNOWN"),
        {"object": "weird", "entry": "not-a-list"},
        {"object": "page"},
    ):
        assert (await _post_webhook(client, payload)).status_code == 200
    body = b"not json"
    assert (await client.post("/api/meta/webhook", content=body, headers=_sig(body))).status_code == 200
    assert _meta_settings == []


async def test_echoes_receipts_and_statuses_are_ignored(
    client: AsyncClient, db_session: AsyncSession, _meta_settings: list[tuple[str, dict[str, Any]]]
) -> None:
    await _seed_channel(client, db_session, "facebook", "PG200", "e1@example.com")
    await _seed_channel(client, db_session, "whatsapp", "PN200", "e2@example.com")
    assert (await _post_webhook(client, _messaging_payload("page", "PG200", echo=True))).status_code == 200
    receipt = _messaging_payload("page", "PG200")
    receipt["entry"][0]["messaging"][0] = {
        "sender": {"id": "U"},
        "recipient": {"id": "PG200"},
        "read": {"watermark": 1},
    }
    assert (await _post_webhook(client, receipt)).status_code == 200
    status_only = _wa_payload("PN200")
    status_only["entry"][0]["changes"][0]["value"]["statuses"] = [{"id": "x", "status": "delivered"}]
    assert (await _post_webhook(client, status_only)).status_code == 200
    assert _meta_settings == []


async def test_duplicate_delivery_is_processed_once(
    client: AsyncClient, db_session: AsyncSession, _meta_settings: list[tuple[str, dict[str, Any]]]
) -> None:
    await _seed_channel(client, db_session, "whatsapp", "PN300", "d1@example.com")
    payload = _wa_payload("PN300", _wa_msg("once"))
    assert (await _post_webhook(client, payload)).status_code == 200
    assert (await _post_webhook(client, payload)).status_code == 200  # Meta retry
    assert len(_meta_settings) == 1


async def test_batched_messages_become_separate_events(
    client: AsyncClient, db_session: AsyncSession, _meta_settings: list[tuple[str, dict[str, Any]]]
) -> None:
    await _seed_channel(client, db_session, "whatsapp", "PN400", "b1@example.com")
    assert (await _post_webhook(client, _wa_payload("PN400", _wa_msg("one"), _wa_msg("two")))).status_code == 200
    assert len(_meta_settings) == 2


async def test_enqueue_failure_falls_back_to_inline(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    get_channel("facebook").transport = _capture_transport(calls)
    channel, *_ = await _seed_channel(
        client, db_session, "facebook", "PG500", "i1@example.com", config={"page_access_token": "tok"}
    )

    def boom(_cid: str, _payload: dict[str, Any]) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr("app.channels.meta_router.enqueue_meta_inbound", boom)
    try:
        assert (await _post_webhook(client, _messaging_payload("page", "PG500", "ping"))).status_code == 200
    finally:
        get_channel("facebook").transport = None
    sends = [c for c in calls if c["url"].endswith("/me/messages")]
    assert sends and "echo: ping" in json.loads(sends[-1]["body"])["message"]["text"]
    assert channel.status == "active"


async def test_handle_inbound_runs_the_existing_adapter_and_replies(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    calls: list[dict[str, Any]] = []
    get_channel("whatsapp").transport = _capture_transport(calls)
    channel, *_ = await _seed_channel(
        client, db_session, "whatsapp", "PN600", "h1@example.com",
        config={"phone_number_id": "PN600", "access_token": "plain-token"},
    )  # fmt: skip
    event = meta_webhook.split_events(_wa_payload("PN600", _wa_msg("hello wa")))[0]
    try:
        await meta_webhook.handle_inbound(db_session, channel.id, event.payload)
    finally:
        get_channel("whatsapp").transport = None
    sends = [c for c in calls if c["url"].endswith("/PN600/messages")]
    assert sends and "echo: hello wa" in json.loads(sends[-1]["body"])["text"]["body"]


async def test_handle_inbound_ignores_inactive_channels(client: AsyncClient, db_session: AsyncSession) -> None:
    calls: list[dict[str, Any]] = []
    get_channel("whatsapp").transport = _capture_transport(calls)
    channel, *_ = await _seed_channel(
        client, db_session, "whatsapp", "PN700", "h2@example.com",
        config={"phone_number_id": "PN700", "access_token": "t"}, status="needs_reconnect",
    )  # fmt: skip
    event = meta_webhook.split_events(_wa_payload("PN700", _wa_msg("hi")))[0]
    try:
        await meta_webhook.handle_inbound(db_session, channel.id, event.payload)
    finally:
        get_channel("whatsapp").transport = None
    assert calls == []


# ── WhatsApp connect ──────────────────────────────────────────────────────────────
def _wa_graph(graph: FakeGraph, *, numbers: list[dict[str, Any]] | None = None, status: str = "PENDING") -> None:
    graph.on("GET", "/oauth/access_token", {"access_token": "EAA_WA_TOKEN"})
    graph.on(
        "GET",
        "/debug_token",
        {
            "data": {
                "is_valid": True,
                "app_id": APP_ID,
                "user_id": "FBUSER1",
                "expires_at": 0,
                "granular_scopes": [{"scope": "whatsapp_business_management", "target_ids": ["1001"]}],
            }
        },
    )
    graph.on(
        "GET",
        "/1001/phone_numbers",
        {
            "data": numbers
            if numbers is not None
            else [
                {"id": "2001", "display_phone_number": "+91 98765 43210", "verified_name": "Acme Co", "status": status}
            ]
        },
    )
    graph.on("POST", "/1001/subscribed_apps", {"success": True})
    graph.on("POST", "/2001/register", {"success": True})


def _wa_body(aid: str, **over: str) -> dict[str, str]:
    return {"agent_id": aid, "code": "AUTHCODE", "waba_id": "1001", "phone_number_id": "2001", **over}


async def test_whatsapp_connect_happy_path(client: AsyncClient, db_session: AsyncSession, graph: FakeGraph) -> None:
    _wa_graph(graph)
    headers = await _headers(client, "wa1@example.com")
    aid = await _fake_agent(client, headers)
    r = await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid), headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["type"] == "whatsapp" and out["name"] == "Acme Co"
    assert out["connection_source"] == "meta_oauth" and out["status"] == "active" and out["enabled"] is True
    assert out["external_id"] == "2001" and out["external_parent_id"] == "1001"
    assert out["config"]["access_token"] == "••••set" and out["config"]["registration_pin"] == "••••set"
    assert "EAA_WA_TOKEN" not in r.text  # never returned to the browser

    channel = (await db_session.execute(select(Channel).where(Channel.id == uuid.UUID(out["id"])))).scalar_one()
    assert channel.config["access_token"] != "EAA_WA_TOKEN"
    assert decrypt(channel.config["access_token"]) == "EAA_WA_TOKEN"
    assert channel.config["phone_number_id"] == "2001" and channel.config["display_phone_number"] == "+91 98765 43210"
    assert channel.config["meta_user_id"] == "FBUSER1"
    pin = decrypt(channel.config["registration_pin"])
    assert len(pin) == 6 and pin.isdigit()

    # Exchange used the code with the app's credentials; the follow-up calls carried the new token as a header.
    exchange = graph.called("GET", "/oauth/access_token")[0]["params"]
    assert exchange["code"] == "AUTHCODE" and exchange["client_id"] == APP_ID and "redirect_uri" not in exchange
    assert graph.called("POST", "/1001/subscribed_apps")[0]["auth"] == "Bearer EAA_WA_TOKEN"
    register = graph.called("POST", "/2001/register")[0]
    assert register["form"] == {"messaging_product": "whatsapp", "pin": pin}

    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "channel.meta_connected"))).scalars().all()
    )
    assert audit and "EAA_WA_TOKEN" not in json.dumps(audit[-1].meta)


async def test_whatsapp_connect_skips_register_when_number_already_connected(
    client: AsyncClient, graph: FakeGraph
) -> None:
    _wa_graph(graph, status="CONNECTED")
    headers = await _headers(client, "wa2@example.com")
    aid = await _fake_agent(client, headers)
    r = await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid), headers=headers)
    assert r.status_code == 200 and graph.called("POST", "/2001/register") == []


async def test_whatsapp_connect_rejects_forged_phone_number(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    _wa_graph(graph)
    headers = await _headers(client, "wa3@example.com")
    aid = await _fake_agent(client, headers)
    r = await client.post(
        "/v1/channels/meta/whatsapp/connect", json=_wa_body(aid, phone_number_id="9999"), headers=headers
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "meta.phone_not_in_waba"
    assert graph.called("POST", "/1001/subscribed_apps") == []
    assert (await db_session.execute(select(Channel).where(Channel.external_id == "9999"))).first() is None


async def test_whatsapp_connect_rejects_unauthorized_waba(client: AsyncClient, graph: FakeGraph) -> None:
    _wa_graph(graph)
    headers = await _headers(client, "wa4@example.com")
    aid = await _fake_agent(client, headers)
    # The token only covers 1001; the browser claims 2002.
    graph.on("GET", "/2002/phone_numbers", {"data": [{"id": "2001", "status": "CONNECTED"}]})
    r = await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid, waba_id="2002"), headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "meta.waba_not_authorized"


async def test_whatsapp_connect_rejects_token_for_another_app(client: AsyncClient, graph: FakeGraph) -> None:
    _wa_graph(graph)
    graph.on("GET", "/debug_token", {"data": {"is_valid": True, "app_id": "SOMEONE_ELSE"}})
    headers = await _headers(client, "wa5@example.com")
    aid = await _fake_agent(client, headers)
    r = await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid), headers=headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "meta.token_invalid"


async def test_whatsapp_connect_surfaces_metas_message(client: AsyncClient, graph: FakeGraph) -> None:
    graph.on(
        "GET", "/oauth/access_token", {"error": {"message": "This authorization code has expired.", "code": 100}}, 400
    )
    headers = await _headers(client, "wa6@example.com")
    aid = await _fake_agent(client, headers)
    r = await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid), headers=headers)
    assert r.status_code == 400 and "expired" in r.json()["error"]["message"]


async def test_whatsapp_connect_cross_org_conflict_is_409(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    _wa_graph(graph)
    a = await _headers(client, "wa7a@example.com")
    b = await _headers(client, "wa7b@example.com")
    aid_a, aid_b = await _fake_agent(client, a), await _fake_agent(client, b)
    first = await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid_a), headers=a)
    assert first.status_code == 200
    graph.calls.clear()
    second = await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid_b), headers=b)
    assert second.status_code == 409 and second.json()["error"]["code"] == "channels.already_connected"
    assert graph.calls == []  # refused before any Graph call, so nothing was exchanged or subscribed
    rows = (await db_session.execute(select(Channel).where(Channel.external_id == "2001"))).scalars().all()
    assert len(rows) == 1 and str(rows[0].organization_id) == a["X-Org-Id"]  # not taken over


async def test_whatsapp_reconnect_same_org_updates_instead_of_duplicating(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    _wa_graph(graph)
    headers = await _headers(client, "wa8@example.com")
    aid = await _fake_agent(client, headers)
    one = (await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid), headers=headers)).json()
    graph.on("GET", "/oauth/access_token", {"access_token": "EAA_NEW"})
    two = (await client.post("/v1/channels/meta/whatsapp/connect", json=_wa_body(aid), headers=headers)).json()
    assert one["id"] == two["id"]
    rows = (await db_session.execute(select(Channel).where(Channel.external_id == "2001"))).scalars().all()
    assert len(rows) == 1 and decrypt(rows[0].config["access_token"]) == "EAA_NEW"
    # The registration pin is kept so a re-register uses the same one.
    pin_one = decrypt(rows[0].config["registration_pin"])
    assert len(pin_one) == 6


# ── Messenger + Instagram ─────────────────────────────────────────────────────────
def _pages_graph(graph: FakeGraph) -> None:
    graph.on("GET", "/oauth/access_token", {"access_token": "LONG_USER_TOKEN"})
    graph.on("GET", "/me", {"id": "FBUSER9"})
    graph.on(
        "GET",
        "/me/accounts",
        {
            "data": [
                {
                    "id": "PG1",
                    "name": "Acme Shop",
                    "access_token": "PAGE_TOKEN_1",
                    "picture": {"data": {"url": "https://img/1.png"}},
                    "instagram_business_account": {"id": "IG1", "username": "acme"},
                },
                {"id": "PG2", "name": "No Insta", "access_token": "PAGE_TOKEN_2"},
            ]
        },
    )
    graph.on("POST", "/PG1/subscribed_apps", {"success": True})
    graph.on("POST", "/PG2/subscribed_apps", {"success": True})
    graph.on("DELETE", "/PG1/subscribed_apps", {"success": True})


async def _list_pages(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    r = await client.post("/v1/channels/meta/facebook/pages", json={"user_access_token": "SHORT"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_pages_list_returns_only_public_fields(client: AsyncClient, graph: FakeGraph) -> None:
    _pages_graph(graph)
    headers = await _headers(client, "pg1@example.com")
    r = await client.post("/v1/channels/meta/facebook/pages", json={"user_access_token": "SHORT"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["pages"][0] == {
        "page_id": "PG1",
        "name": "Acme Shop",
        "picture": "https://img/1.png",
        "instagram": {"id": "IG1", "username": "acme"},
    }
    assert body["pages"][1]["instagram"] is None
    assert "PAGE_TOKEN" not in r.text and "LONG_USER_TOKEN" not in r.text and "SHORT" not in r.text
    ex = graph.called("GET", "/oauth/access_token")[0]["params"]
    assert ex["grant_type"] == "fb_exchange_token" and ex["fb_exchange_token"] == "SHORT"
    assert graph.called("GET", "/me/accounts")[0]["auth"] == "Bearer LONG_USER_TOKEN"


async def test_pages_connect_messenger_and_instagram(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    _pages_graph(graph)
    headers = await _headers(client, "pg2@example.com")
    aid = await _fake_agent(client, headers)
    listed = await _list_pages(client, headers)
    r = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={
            "session_id": listed["session_id"],
            "agent_id": aid,
            "page_ids": ["PG1"],
            "kinds": ["messenger", "instagram"],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert "PAGE_TOKEN_1" not in r.text
    by_type = {c["type"]: c for c in r.json()}
    assert by_type["facebook"]["external_id"] == "PG1" and by_type["facebook"]["name"] == "Acme Shop"
    assert by_type["instagram"]["external_id"] == "IG1" and by_type["instagram"]["name"] == "@acme"
    assert {c["connection_source"] for c in by_type.values()} == {"meta_oauth"}
    assert all(c["enabled"] and c["status"] == "active" for c in by_type.values())

    rows = (await db_session.execute(select(Channel).where(Channel.external_parent_id == "PG1"))).scalars().all()
    assert len(rows) == 2
    for row in rows:
        assert decrypt(row.config["page_access_token"]) == "PAGE_TOKEN_1"
        assert row.config["meta_user_id"] == "FBUSER9"
    subs = graph.called("POST", "/PG1/subscribed_apps")
    assert len(subs) == 1  # one subscription per page, shared by both channels
    assert subs[0]["form"] == {"subscribed_fields": "messages,messaging_postbacks"}
    assert subs[0]["auth"] == "Bearer PAGE_TOKEN_1"

    # The session is single-use.
    again = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": listed["session_id"], "agent_id": aid, "page_ids": ["PG1"], "kinds": ["messenger"]},
        headers=headers,
    )
    assert again.status_code == 410


async def test_instagram_requires_a_linked_account(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    _pages_graph(graph)
    headers = await _headers(client, "pg3@example.com")
    aid = await _fake_agent(client, headers)
    listed = await _list_pages(client, headers)
    r = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": listed["session_id"], "agent_id": aid, "page_ids": ["PG1", "PG2"], "kinds": ["instagram"]},
        headers=headers,
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "meta.no_instagram"
    assert graph.called("POST", "/PG1/subscribed_apps") == []  # nothing happened for the valid page either
    assert (
        await db_session.execute(select(Channel).where(Channel.external_parent_id.in_(["PG1", "PG2"])))
    ).first() is None


async def test_pages_connect_rejects_unlisted_page_and_foreign_session(client: AsyncClient, graph: FakeGraph) -> None:
    _pages_graph(graph)
    headers = await _headers(client, "pg4@example.com")
    other = await _headers(client, "pg4b@example.com")
    aid, other_aid = await _fake_agent(client, headers), await _fake_agent(client, other)
    listed = await _list_pages(client, headers)
    unlisted = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": listed["session_id"], "agent_id": aid, "page_ids": ["PG_FORGED"], "kinds": ["messenger"]},
        headers=headers,
    )
    assert unlisted.status_code == 400 and unlisted.json()["error"]["code"] == "meta.page_not_available"
    foreign = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": listed["session_id"], "agent_id": other_aid, "page_ids": ["PG1"], "kinds": ["messenger"]},
        headers=other,
    )
    assert foreign.status_code == 410  # another user/org cannot redeem someone else's session
    bad_kind = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": listed["session_id"], "agent_id": aid, "page_ids": ["PG1"], "kinds": ["telegram"]},
        headers=headers,
    )
    assert bad_kind.status_code == 422


async def test_pages_connect_cross_org_conflict_is_409(client: AsyncClient, graph: FakeGraph) -> None:
    _pages_graph(graph)
    a = await _headers(client, "pg5a@example.com")
    b = await _headers(client, "pg5b@example.com")
    aid_a, aid_b = await _fake_agent(client, a), await _fake_agent(client, b)
    la = await _list_pages(client, a)
    ok = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": la["session_id"], "agent_id": aid_a, "page_ids": ["PG1"], "kinds": ["messenger"]},
        headers=a,
    )
    assert ok.status_code == 200
    lb = await _list_pages(client, b)
    graph.calls.clear()
    conflict = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": lb["session_id"], "agent_id": aid_b, "page_ids": ["PG1"], "kinds": ["messenger"]},
        headers=b,
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "channels.already_connected"
    assert graph.calls == []


# ── disconnect / reconnect ────────────────────────────────────────────────────────
async def _connect_page(
    client: AsyncClient, graph: FakeGraph, headers: dict[str, str], kinds: list[str]
) -> tuple[str, list[dict[str, Any]]]:
    _pages_graph(graph)
    aid = await _fake_agent(client, headers)
    listed = await _list_pages(client, headers)
    r = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": listed["session_id"], "agent_id": aid, "page_ids": ["PG1"], "kinds": kinds},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return aid, r.json()  # type: ignore[no-any-return]


async def test_disconnect_unsubscribes_wipes_and_frees_the_account(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    headers = await _headers(client, "dc1@example.com")
    _aid, channels = await _connect_page(client, graph, headers, ["messenger"])
    cid = channels[0]["id"]
    r = await client.post(f"/v1/channels/{cid}/meta/disconnect", headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "disconnected" and out["enabled"] is False and out["external_id"] is None
    assert "page_access_token" not in out["config"]
    assert graph.called("DELETE", "/PG1/subscribed_apps")[0]["auth"] == "Bearer PAGE_TOKEN_1"
    row = await db_session.get(Channel, uuid.UUID(cid))
    await db_session.refresh(row)
    assert "page_access_token" not in row.config  # type: ignore[union-attr]


async def test_disconnect_keeps_the_page_subscription_while_a_sibling_needs_it(
    client: AsyncClient, graph: FakeGraph
) -> None:
    headers = await _headers(client, "dc2@example.com")
    _aid, channels = await _connect_page(client, graph, headers, ["messenger", "instagram"])
    by_type = {c["type"]: c["id"] for c in channels}
    assert (
        await client.post(f"/v1/channels/{by_type['instagram']}/meta/disconnect", headers=headers)
    ).status_code == 200
    assert graph.called("DELETE", "/PG1/subscribed_apps") == []  # Messenger still needs it
    assert (
        await client.post(f"/v1/channels/{by_type['facebook']}/meta/disconnect", headers=headers)
    ).status_code == 200
    assert len(graph.called("DELETE", "/PG1/subscribed_apps")) == 1


async def test_disconnect_survives_a_dead_token_and_rejects_manual_channels(
    client: AsyncClient, graph: FakeGraph
) -> None:
    headers = await _headers(client, "dc3@example.com")
    _aid, channels = await _connect_page(client, graph, headers, ["messenger"])
    graph.on("DELETE", "/PG1/subscribed_apps", {"error": {"message": "token expired", "code": 190}}, 400)
    assert (await client.post(f"/v1/channels/{channels[0]['id']}/meta/disconnect", headers=headers)).status_code == 200

    aid = await _fake_agent(client, headers)
    manual = await client.post(
        "/v1/channels", json={"agent_id": aid, "type": "telegram", "config": {"bot_token": "1:a"}}, headers=headers
    )
    r = await client.post(f"/v1/channels/{manual.json()['id']}/meta/disconnect", headers=headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "channels.not_meta_connected"


async def test_reconnect_after_disconnect_reuses_the_row(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    headers = await _headers(client, "dc4@example.com")
    aid, channels = await _connect_page(client, graph, headers, ["messenger"])
    cid = channels[0]["id"]
    await client.post(f"/v1/channels/{cid}/meta/disconnect", headers=headers)
    listed = await _list_pages(client, headers)
    r = await client.post(
        "/v1/channels/meta/facebook/connect",
        json={"session_id": listed["session_id"], "agent_id": aid, "page_ids": ["PG1"], "kinds": ["messenger"]},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()[0]["id"] == cid
    assert r.json()[0]["status"] == "active" and r.json()[0]["external_id"] == "PG1"


async def test_freed_account_can_be_connected_by_another_org(client: AsyncClient, graph: FakeGraph) -> None:
    a = await _headers(client, "dc5a@example.com")
    b = await _headers(client, "dc5b@example.com")
    _aid, channels = await _connect_page(client, graph, a, ["messenger"])
    await client.post(f"/v1/channels/{channels[0]['id']}/meta/disconnect", headers=a)
    _aid_b, mine = await _connect_page(client, graph, b, ["messenger"])
    assert mine[0]["external_id"] == "PG1"


# ── deauthorize ───────────────────────────────────────────────────────────────────
async def test_deauthorize_marks_that_users_channels(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    headers = await _headers(client, "da1@example.com")
    _aid, channels = await _connect_page(client, graph, headers, ["messenger"])
    r = await client.post(
        "/api/meta/deauthorize",
        data={"signed_request": _signed_request({"algorithm": "HMAC-SHA256", "user_id": "FBUSER9"})},
    )
    assert r.status_code == 200
    row = await db_session.get(Channel, uuid.UUID(channels[0]["id"]))
    await db_session.refresh(row)
    assert row.status == "needs_reconnect" and row.enabled is False  # type: ignore[union-attr]
    assert "page_access_token" not in row.config  # type: ignore[union-attr]
    # A different user id touches nothing.
    other = await client.post(
        "/api/meta/deauthorize",
        data={"signed_request": _signed_request({"algorithm": "HMAC-SHA256", "user_id": "SOMEONE"})},
    )
    assert other.status_code == 200


async def test_deauthorize_rejects_bad_signature(client: AsyncClient) -> None:
    r = await client.post(
        "/api/meta/deauthorize",
        data={"signed_request": _signed_request({"algorithm": "HMAC-SHA256", "user_id": "1"}, secret="wrong")},
    )
    assert r.status_code == 400
    assert (await client.post("/api/meta/deauthorize", data={})).status_code == 422


# ── token health ──────────────────────────────────────────────────────────────────
async def test_health_check_transitions_once(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _headers(client, "hc1@example.com")
    _aid, channels = await _connect_page(client, graph, headers, ["messenger"])
    row = await db_session.get(Channel, uuid.UUID(channels[0]["id"]))
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_emit(_session: Any, _org: Any, event: str, data: dict[str, Any]) -> list[Any]:
        events.append((event, data))
        return []

    monkeypatch.setattr(meta_connect, "emit_event", fake_emit)

    # Healthy: stays active, timestamps recorded.
    graph.on("GET", "/debug_token", {"data": {"is_valid": True, "expires_at": 0}})
    result = await meta_health.run_health_check(db_session)
    assert result["needs_reconnect"] == 0 and row.status == "active"  # type: ignore[union-attr]
    assert row.last_health_check_at is not None  # type: ignore[union-attr]

    # A Meta outage must not flip anything.
    graph.on("GET", "/debug_token", {"error": {"message": "temporarily down", "code": 2}}, 503)
    assert (await meta_health.run_health_check(db_session))["skipped"] >= 1
    assert row.status == "active"  # type: ignore[union-attr]

    # Invalid token: flips once, emits once, never again.
    graph.on("GET", "/debug_token", {"data": {"is_valid": False}})
    assert (await meta_health.run_health_check(db_session))["needs_reconnect"] == 1
    assert row.status == "needs_reconnect"  # type: ignore[union-attr]
    assert (await meta_health.run_health_check(db_session))["needs_reconnect"] == 0
    assert [e for e, _ in events] == ["channel.needs_reconnect"]
    assert events[0][1]["channel_id"] == channels[0]["id"]


async def test_health_check_flags_expired_tokens(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    headers = await _headers(client, "hc2@example.com")
    _aid, channels = await _connect_page(client, graph, headers, ["messenger"])
    graph.on("GET", "/debug_token", {"data": {"is_valid": True, "expires_at": 1}})  # 1970
    await meta_health.run_health_check(db_session)
    row = await db_session.get(Channel, uuid.UUID(channels[0]["id"]))
    assert row.status == "needs_reconnect"  # type: ignore[union-attr]


async def test_health_check_ignores_manual_channels(
    client: AsyncClient, db_session: AsyncSession, graph: FakeGraph
) -> None:
    channel, *_ = await _seed_channel(
        client, db_session, "facebook", "MANUAL1", "hc3@example.com", config={"page_access_token": "t"}
    )
    graph.on("GET", "/debug_token", {"data": {"is_valid": False}})
    await meta_health.run_health_check(db_session)
    assert channel.status == "active" and graph.calls == []


# ── outbound guard ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "state,code", [("needs_reconnect", "channel_needs_reconnect"), ("disconnected", "channel_disconnected")]
)
async def test_inactive_channels_do_not_send(
    client: AsyncClient, db_session: AsyncSession, state: str, code: str
) -> None:
    calls: list[dict[str, Any]] = []
    for kind, ext, cfg in (
        ("whatsapp", "PNX", {"phone_number_id": "PNX", "access_token": "t"}),
        ("facebook", "PGX", {"page_access_token": "t"}),
        ("instagram", "IGX", {"page_access_token": "t"}),
    ):
        get_channel(kind).transport = _capture_transport(calls)
        channel, *_ = await _seed_channel(
            client, db_session, kind, ext + state, f"g{kind}{state}@example.com", config=cfg, status=state
        )
        adapter = get_channel(kind)
        try:
            with pytest.raises(AppError) as err:
                await adapter.send(channel, "user", "hello")
            assert err.value.code == code and err.value.status_code == 409
            with pytest.raises(AppError):
                adapter.check_can_send(channel, last_inbound_at=None)
        finally:
            adapter.transport = None
    assert calls == []


# ── backward compatibility ────────────────────────────────────────────────────────
async def test_manual_channel_flow_is_unchanged_but_claims_its_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    a = await _headers(client, "bc1@example.com")
    b = await _headers(client, "bc1b@example.com")
    aid_a, aid_b = await _fake_agent(client, a), await _fake_agent(client, b)
    cfg = {"phone_number_id": "MANUAL_PN", "access_token": "tok", "verify_token": "v", "app_secret": "s"}
    created = await client.post("/v1/channels", json={"agent_id": aid_a, "type": "whatsapp", "config": cfg}, headers=a)
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["connection_source"] == "manual" and out["status"] == "active" and out["external_id"] == "MANUAL_PN"
    assert out["config"]["access_token"] == "••••set"
    assert "/v1/channels/whatsapp/" in out["webhook_url"]  # per-channel webhook URL still advertised

    # Another workspace cannot register the same number by hand either.
    dup = await client.post("/v1/channels", json={"agent_id": aid_b, "type": "whatsapp", "config": cfg}, headers=b)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "channels.already_connected"

    # Changing the id through PATCH moves the claim.
    patched = await client.patch(
        f"/v1/channels/{out['id']}", json={"config": {"phone_number_id": "OTHER_PN"}}, headers=a
    )
    assert patched.status_code == 200 and patched.json()["external_id"] == "OTHER_PN"


async def test_manual_channels_without_an_id_never_collide(client: AsyncClient) -> None:
    a = await _headers(client, "bc2@example.com")
    aid = await _fake_agent(client, a)
    for _ in range(2):
        r = await client.post(
            "/v1/channels", json={"agent_id": aid, "type": "telegram", "config": {"bot_token": "1:a"}}, headers=a
        )
        assert r.status_code == 201 and r.json()["external_id"] is None


def test_graph_version_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert meta_graph.graph_base() == f"https://graph.facebook.com/{settings.meta_graph_version}"
    monkeypatch.setattr(settings, "meta_graph_version", "v99.0")
    assert meta_graph.graph_base().endswith("/v99.0")
