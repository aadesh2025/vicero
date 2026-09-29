"""Phase 15 tests: outbound webhooks — CRUD, signed delivery, retry, event emission."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.webhooks.dispatch import deliver_delivery, emit_event


def _mock(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


async def _headers(client: AsyncClient, email: str = "wh@example.com") -> tuple[dict[str, str], uuid.UUID]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "WhOrg"}, headers={"Authorization": f"Bearer {token}"})
    org_id = org.json()["id"]
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org_id}, uuid.UUID(org_id)


async def test_webhook_crud_masks_secret(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    created = await client.post(
        "/v1/webhooks", json={"url": "https://example.com/hook", "events": ["message.created"]}, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["secret"] and not created.json()["secret"].startswith("••••")  # revealed once

    listed = await client.get("/v1/webhooks", headers=headers)
    assert listed.json()[0]["secret"] == "••••set"  # masked afterwards

    catalog = await client.get("/v1/webhooks/events", headers=headers)
    assert "handoff.requested" in catalog.json()


async def test_signed_delivery(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id = await _headers(client, "wh2@example.com")
    ep = await client.post(
        "/v1/webhooks", json={"url": "https://hooks.example.com/x", "events": ["message.created"]}, headers=headers
    )
    secret = ep.json()["secret"]
    eid = ep.json()["id"]

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sig"] = request.headers.get("x-vicero-signature")
        captured["ts"] = request.headers.get("x-vicero-timestamp")
        captured["event"] = request.headers.get("x-vicero-event")
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    ids = await emit_event(db_session, org_id, "message.created", {"conversation_id": "abc"})
    assert ids, "expected a pending delivery for the subscribed endpoint"
    ok = await deliver_delivery(db_session, ids[0], transport=_mock(handler))
    assert ok is True
    assert captured["event"] == "message.created"
    # Signature = HMAC-SHA256 of "{ts}.{body}".
    expected = hmac.new(secret.encode(), f"{captured['ts']}.".encode() + captured["body"], hashlib.sha256).hexdigest()
    assert captured["sig"] == expected
    body = json.loads(captured["body"])
    assert body["event"] == "message.created" and body["data"]["conversation_id"] == "abc"

    log = await client.get(f"/v1/webhooks/{eid}/deliveries", headers=headers)
    assert log.json()[0]["status"] == "delivered"
    assert log.json()[0]["attempts"] == 1


async def test_failed_delivery_marks_pending_for_retry(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id = await _headers(client, "wh3@example.com")
    await client.post(
        "/v1/webhooks", json={"url": "https://bad.example.com/x", "events": ["*"]}, headers=headers
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    ids = await emit_event(db_session, org_id, "conversation.created", {"conversation_id": "c1"})
    ok = await deliver_delivery(db_session, ids[0], transport=_mock(handler))
    assert ok is False

    from app.models import WebhookDelivery

    delivery = await db_session.get(WebhookDelivery, ids[0])
    assert delivery is not None
    assert delivery.attempts == 1
    assert delivery.status == "pending"  # will be retried
    assert delivery.next_retry_at is not None
    assert delivery.response_status == 500


async def test_ssrf_blocked_url_fails_permanently(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id = await _headers(client, "wh4@example.com")
    await client.post(
        "/v1/webhooks", json={"url": "http://127.0.0.1:9000/x", "events": ["*"]}, headers=headers
    )
    ids = await emit_event(db_session, org_id, "tool.run", {"tool": "calc"})
    # No transport → the real SSRF guard rejects the loopback host.
    ok = await deliver_delivery(db_session, ids[0])
    assert ok is False
    from app.models import WebhookDelivery

    delivery = await db_session.get(WebhookDelivery, ids[0])
    assert delivery is not None and delivery.status == "failed"


async def test_message_created_event_emitted_on_chat(client: AsyncClient) -> None:
    """A real chat turn should produce a message.created delivery for a subscribed endpoint."""
    headers, _ = await _headers(client, "wh5@example.com")
    ep = await client.post(
        "/v1/webhooks", json={"url": "https://example.com/hook", "events": ["message.created"]}, headers=headers
    )
    eid = ep.json()["id"]
    agent = await client.post("/v1/agents", json={"name": "Emitter"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": "fake", "model": "fake-1"}},
        headers=headers,
    )
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    deliveries = await client.get(f"/v1/webhooks/{eid}/deliveries", headers=headers)
    assert any(d["event"] == "message.created" for d in deliveries.json())


async def test_sweep_requeues_only_due_pending(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The beat sweep re-enqueues pending deliveries past next_retry_at, and only those."""
    import datetime as dt

    from app.models import WebhookDelivery
    from app.webhooks import dispatch

    headers, _ = await _headers(client, "wh6@example.com")
    ep = await client.post(
        "/v1/webhooks", json={"url": "https://example.com/hook", "events": ["message.created"]}, headers=headers
    )
    endpoint_id = uuid.UUID(ep.json()["id"])
    now = dt.datetime.now(tz=dt.UTC)

    due = WebhookDelivery(
        webhook_endpoint_id=endpoint_id, event="message.created", payload={}, status="pending",
        attempts=1, next_retry_at=now - dt.timedelta(minutes=5),
    )
    future = WebhookDelivery(
        webhook_endpoint_id=endpoint_id, event="message.created", payload={}, status="pending",
        attempts=1, next_retry_at=now + dt.timedelta(minutes=5),
    )
    delivered = WebhookDelivery(
        webhook_endpoint_id=endpoint_id, event="message.created", payload={}, status="delivered",
        attempts=1, next_retry_at=None,
    )
    db_session.add_all([due, future, delivered])
    await db_session.flush()

    requeued: list[uuid.UUID] = []
    monkeypatch.setattr(dispatch, "_enqueue", lambda did: requeued.append(did))

    count = await dispatch.sweep_due_deliveries(db_session)
    assert count == 1
    assert requeued == [due.id]
