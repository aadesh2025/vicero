"""Per-agent outbound webhooks and the agent-level events (docs/28 T3.2)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crm import capture as crm_capture
from app.crm.extract import ExtractedContact, find_contact_hints, normalize_email
from app.llm.fake import ScriptedToolProvider
from app.llm.types import ToolCall
from app.models import WebhookDelivery
from app.webhooks import EVENT_CATALOG
from app.webhooks.dispatch import deliver_delivery, emit_event, sanitize_payload
from tests.conftest import HttpMock


async def _org(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "WhAgentOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _agent(client: AsyncClient, headers: dict[str, str], name: str = "Bot") -> tuple[str, str]:
    agent = await client.post("/v1/agents", json={"name": name}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {"tools_enabled": True, "memory_enabled": True, "handoff_enabled": True},
        },
        headers=headers,
    )
    return str(aid), str(agent.json()["public_key"])


async def _endpoint(client: AsyncClient, headers: dict[str, str], url: str, agent_id: str | None = None) -> str:
    body: dict = {"url": url, "events": ["*"]}
    if agent_id:
        body["agent_id"] = agent_id
    r = await client.post("/v1/webhooks", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _deliveries(db: AsyncSession, endpoint_id: str) -> list[WebhookDelivery]:
    stmt = select(WebhookDelivery).where(WebhookDelivery.webhook_endpoint_id == uuid.UUID(endpoint_id))
    return list((await db.execute(stmt)).scalars().all())


def test_the_catalogue_lists_the_new_events() -> None:
    assert {"tool.called", "variables.captured", "conversation.ended", "lead.captured"} <= set(EVENT_CATALOG)


async def test_an_agent_endpoint_gets_only_its_agents_events_and_an_org_wide_one_gets_all(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _org(client, "wha1@example.com")
    a, _ = await _agent(client, headers, "A")
    b, _ = await _agent(client, headers, "B")
    org_id = uuid.UUID(headers["X-Org-Id"])
    only_a = await _endpoint(client, headers, "https://a.example.com/h", a)
    only_b = await _endpoint(client, headers, "https://b.example.com/h", b)
    everyone = await _endpoint(client, headers, "https://all.example.com/h")

    await emit_event(db_session, org_id, "message.created", {"x": 1}, agent_id=uuid.UUID(a))
    await emit_event(db_session, org_id, "message.created", {"agent_id": b})  # agent read from the payload
    await emit_event(db_session, org_id, "usage.threshold", {"pct": 80})  # no agent at all

    assert [d.payload["data"].get("x") for d in await _deliveries(db_session, only_a)] == [1]
    assert len(await _deliveries(db_session, only_b)) == 1
    assert len(await _deliveries(db_session, everyone)) == 3


async def test_an_agent_endpoint_never_receives_an_event_with_no_agent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _org(client, "wha2@example.com")
    a, _ = await _agent(client, headers)
    ep = await _endpoint(client, headers, "https://a.example.com/h", a)
    await emit_event(db_session, uuid.UUID(headers["X-Org-Id"]), "document.ready", {"doc": "d"})
    assert await _deliveries(db_session, ep) == []


async def test_endpoint_agent_must_belong_to_the_org_and_list_can_filter(client: AsyncClient) -> None:
    headers = await _org(client, "wha3@example.com")
    other = await _org(client, "wha3b@example.com")
    a, _ = await _agent(client, headers)
    foreign, _ = await _agent(client, other)
    bad = await client.post(
        "/v1/webhooks", json={"url": "https://x.example.com/h", "agent_id": foreign}, headers=headers
    )
    assert bad.status_code == 404
    mine = await _endpoint(client, headers, "https://a.example.com/h", a)
    await _endpoint(client, headers, "https://all.example.com/h")
    listed = (await client.get(f"/v1/webhooks?agent_id={a}", headers=headers)).json()
    assert [w["id"] for w in listed] == [mine] and listed[0]["agent_id"] == a
    assert len((await client.get("/v1/webhooks", headers=headers)).json()) == 2


def test_sanitize_payload_masks_secret_keys_and_cuts_long_text() -> None:
    clean = sanitize_payload(
        {"api_key": "sk-123", "nested": {"Authorization": "Bearer x", "note": "n" * 1000}, "ok": [1, "a"]}
    )
    assert clean["api_key"] == "••••" and clean["nested"]["Authorization"] == "••••"
    assert clean["nested"]["note"].endswith("...") and len(clean["nested"]["note"]) < 400
    assert clean["ok"] == [1, "a"]


async def test_tool_called_event_carries_redacted_args_and_goes_to_that_agent_only(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.conversations import service

    headers = await _org(client, "wha4@example.com")
    a, _ = await _agent(client, headers, "A")
    b, _ = await _agent(client, headers, "B")
    ep_a = await _endpoint(client, headers, "https://a.example.com/h", a)
    ep_b = await _endpoint(client, headers, "https://b.example.com/h", b)
    await client.post("/v1/tools", json={"name": "calculator", "type": "builtin", "agent_id": a}, headers=headers)

    call = ToolCall(id="c1", name="calculator", arguments={"expression": "2+2", "api_key": "sk-live-secret"})

    async def _provider(*_a: object, **_k: object) -> ScriptedToolProvider:
        return ScriptedToolProvider(call, answer="four")

    monkeypatch.setattr(service, "_resolve_provider", lambda *args, **kw: _provider())
    resp = await client.post(f"/v1/agents/{a}/chat", json={"message": "2+2?", "stream": False}, headers=headers)
    assert resp.status_code == 200, resp.text

    called = [d for d in await _deliveries(db_session, ep_a) if d.event == "tool.called"]
    assert len(called) == 1
    data = called[0].payload["data"]
    assert data["tool"] == "calculator" and data["provider"] == "builtin" and data["status"] == "success"
    assert data["args"] == {"expression": "2+2", "api_key": "••••"}
    assert "sk-live-secret" not in json.dumps(called[0].payload)
    assert [d for d in await _deliveries(db_session, ep_b) if d.event == "tool.called"] == []


async def test_conversation_ended_has_a_summary_and_reaches_the_agent_endpoint(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _org(client, "wha5@example.com")
    a, key = await _agent(client, headers)
    ep = await _endpoint(client, headers, "https://a.example.com/h", a)
    chat = await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": "hello, do you ship abroad?", "stream": False, "visitor": {"id": "v1"}},
    )
    cid = chat.json()["conversation_id"]
    closed = await client.post(f"/v1/inbox/conversations/{cid}/close", headers=headers)
    assert closed.status_code == 200, closed.text
    ended = [d for d in await _deliveries(db_session, ep) if d.event == "conversation.ended"]
    assert len(ended) == 1
    data = ended[0].payload["data"]
    assert data["conversation_id"] == cid and data["reason"] == "closed_by_operator"
    assert data["summary"]["message_count"] >= 2
    assert data["summary"]["first_user_message"] == "hello, do you ship abroad?"
    assert data["variables"] == {}


async def test_lead_captured_fires_for_a_new_person_and_not_for_an_unchanged_one(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(_s: object, _o: uuid.UUID, text: str, _h: object) -> ExtractedContact:
        h = find_contact_hints(text)
        return ExtractedContact(name="Asha", email=normalize_email(h.email), phone=h.phone)

    monkeypatch.setattr(crm_capture, "extract_contact", fake)
    headers = await _org(client, "wha6@example.com")
    a, key = await _agent(client, headers)
    ep = await _endpoint(client, headers, "https://a.example.com/h", a)

    async def say(text: str) -> None:
        r = await client.post(
            f"/v1/public/agents/{key}/chat",
            json={"message": text, "stream": False, "visitor": {"id": "v-lead", "name": "Asha"}},
        )
        assert r.status_code == 200, r.text

    await say("I am Asha, email me at asha@example.com")
    await say("again asha@example.com thanks")
    leads = [d for d in await _deliveries(db_session, ep) if d.event == "lead.captured"]
    assert len(leads) == 1
    assert leads[0].payload["data"]["created"] is True and leads[0].payload["data"]["email"] == "asha@example.com"


# ── Delivery now goes through the egress guard ───────────────────────────────────────
async def test_a_delivery_to_a_name_that_resolves_inside_the_network_fails_for_good(
    client: AsyncClient, db_session: AsyncSession, dns: dict[str, list[str]]
) -> None:
    headers = await _org(client, "wha7@example.com")
    dns["rebound.example.com"] = ["10.0.0.9"]
    ep = await _endpoint(client, headers, "https://rebound.example.com/h")
    ids = await emit_event(db_session, uuid.UUID(headers["X-Org-Id"]), "message.created", {})
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    ok = await deliver_delivery(db_session, ids[0], transport=httpx.MockTransport(handler))
    assert ok is False and sent == []
    d = (await _deliveries(db_session, ep))[0]
    assert d.status == "failed" and d.next_retry_at is None


async def test_a_delivery_does_not_follow_a_redirect_into_the_network(
    client: AsyncClient, db_session: AsyncSession, mock_http: HttpMock
) -> None:
    headers = await _org(client, "wha8@example.com")
    ep = await _endpoint(client, headers, "https://hooks.example.com/h")
    mock_http.handler = lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
    ids = await emit_event(db_session, uuid.UUID(headers["X-Org-Id"]), "message.created", {})
    assert await deliver_delivery(db_session, ids[0]) is True  # a 302 is "accepted" (< 400); it is not followed
    assert [r.url.host for r in mock_http.requests] == ["93.184.216.34"]
    assert (await _deliveries(db_session, ep))[0].response_status == 302
