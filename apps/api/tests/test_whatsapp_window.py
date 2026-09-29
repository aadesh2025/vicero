"""WhatsApp's 24-hour customer-service window.

Outside it Meta drops a free-form message and returns 131047. Before this, Vicero
attempted the send anyway and swallowed the failure — the operator saw a message in the
transcript that the customer never received. These tests pin the two halves of the fix:
refuse locally before persisting, and honour Meta's verdict if it disagrees with us.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel
from app.channels.whatsapp import WINDOW_CLOSED_CODE, window_open
from app.models import Conversation


@pytest.fixture(autouse=True)
def _reset_transport() -> Iterator[None]:
    yield
    adapter = get_channel("whatsapp")
    if adapter:
        adapter.transport = None


def _transport(calls: list[dict], *, error_code: int | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "body": request.content.decode()})
        if error_code is not None:
            return httpx.Response(400, json={"error": {"code": error_code, "message": "window"}})
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    return httpx.MockTransport(handler)


async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "WaOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _agent_with_whatsapp(
    client: AsyncClient, headers: dict[str, str], templates: str = "order_update,welcome_back"
) -> tuple[str, str]:
    agent = await client.post("/v1/agents", json={"name": "Wa Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "fallback_message": "Connecting you to a teammate now.",
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {"tools_enabled": False, "memory_enabled": True, "handoff_enabled": True},
        },
        headers=headers,
    )
    ch = await client.post(
        "/v1/channels",
        json={
            "agent_id": aid,
            "type": "whatsapp",
            "config": {"phone_number_id": "PN1", "access_token": "tok", "templates": templates},
        },
        headers=headers,
    )
    cid = ch.json()["id"]
    await client.post(f"/v1/channels/{cid}/enable", headers=headers)
    return aid, cid


async def _inbound(client: AsyncClient, channel_id: str, text: str, sender: str = "15551234") -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"profile": {"name": "Rohak"}}],
                            "messages": [{"from": sender, "text": {"body": text}}],
                        }
                    }
                ]
            }
        ]
    }
    r = await client.post(f"/v1/channels/whatsapp/{channel_id}/webhook", json=payload)
    assert r.status_code == 200, r.text


# ── The pure window rule ────────────────────────────────────────────────────────────
def test_window_open_rules() -> None:
    now = dt.datetime.now(tz=dt.UTC)
    assert window_open(now - dt.timedelta(hours=1)) is True
    assert window_open(now - dt.timedelta(hours=23, minutes=59)) is True
    assert window_open(now - dt.timedelta(hours=24, minutes=1)) is False
    # Never heard from them: we have no basis to open a conversation free-form.
    assert window_open(None) is False


# ── Inside the window: unchanged behaviour ──────────────────────────────────────────
async def test_reply_inside_window_sends(client: AsyncClient) -> None:
    calls: list[dict] = []
    get_channel("whatsapp").transport = _transport(calls)
    headers = await _headers(client, "wa-open@example.com")
    _aid, channel_id = await _agent_with_whatsapp(client, headers)

    await _inbound(client, channel_id, "I want to talk to a human")
    convs = (await client.get("/v1/inbox/conversations", headers=headers)).json()
    cid = convs[0]["id"]
    await client.post(f"/v1/inbox/conversations/{cid}/takeover", headers=headers)

    detail = (await client.get(f"/v1/inbox/conversations/{cid}", headers=headers)).json()
    assert detail["send_window"]["open"] is True
    assert detail["send_window"]["templates"] == ["order_update", "welcome_back"]

    reply = await client.post(
        f"/v1/inbox/conversations/{cid}/messages", json={"text": "Hi, how can I help?"}, headers=headers
    )
    assert reply.status_code == 200, reply.text
    sends = [c for c in calls if "/PN1/messages" in c["url"]]
    assert sends and json.loads(sends[-1]["body"])["type"] == "text"


# ── Outside the window: refused, typed, and nothing persisted ───────────────────────
async def test_reply_outside_window_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    calls: list[dict] = []
    get_channel("whatsapp").transport = _transport(calls)
    headers = await _headers(client, "wa-closed@example.com")
    _aid, channel_id = await _agent_with_whatsapp(client, headers)

    await _inbound(client, channel_id, "I need a human agent")
    convs = (await client.get("/v1/inbox/conversations", headers=headers)).json()
    cid = convs[0]["id"]
    await client.post(f"/v1/inbox/conversations/{cid}/takeover", headers=headers)

    # Age the conversation past the window.
    conv = await db_session.get(Conversation, uuid.UUID(cid))
    conv.last_inbound_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=25)
    await db_session.flush()

    detail = (await client.get(f"/v1/inbox/conversations/{cid}", headers=headers)).json()
    assert detail["send_window"]["open"] is False
    assert detail["send_window"]["closes_at"] is not None
    before = len(detail["messages"])

    sends_before = len([c for c in calls if "/PN1/messages" in c["url"]])
    refused = await client.post(
        f"/v1/inbox/conversations/{cid}/messages", json={"text": "are you still there?"}, headers=headers
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "whatsapp_window_closed"

    # Nothing sent, and nothing left in the transcript pretending it was.
    assert len([c for c in calls if "/PN1/messages" in c["url"]]) == sends_before
    after = (await client.get(f"/v1/inbox/conversations/{cid}", headers=headers)).json()
    assert len(after["messages"]) == before


async def test_other_channels_are_unaffected(client: AsyncClient, db_session: AsyncSession) -> None:
    """The window is a WhatsApp rule; Telegram replies must not inherit it."""
    calls: list[dict] = []
    get_channel("telegram").transport = _transport(calls)
    headers = await _headers(client, "tg-window@example.com")
    agent = await client.post("/v1/agents", json={"name": "Tg Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {"tools_enabled": False, "memory_enabled": True, "handoff_enabled": True},
        },
        headers=headers,
    )
    ch = await client.post(
        "/v1/channels", json={"agent_id": aid, "type": "telegram", "config": {"bot_token": "1:a"}}, headers=headers
    )
    ch_id = ch.json()["id"]
    await client.post(f"/v1/channels/{ch_id}/enable", headers=headers)
    secret = (await client.get(f"/v1/channels/{ch_id}", headers=headers)).json()["webhook_secret"]
    await client.post(
        f"/v1/channels/telegram/{ch_id}/webhook",
        json={"message": {"chat": {"id": 7}, "from": {"id": 7, "first_name": "Z"}, "text": "talk to a human"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )

    convs = (await client.get("/v1/inbox/conversations", headers=headers)).json()
    cid = convs[0]["id"]
    conv = await db_session.get(Conversation, uuid.UUID(cid))
    conv.last_inbound_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=9)
    await db_session.flush()

    detail = (await client.get(f"/v1/inbox/conversations/{cid}", headers=headers)).json()
    assert detail["send_window"] is None  # no platform limit to report

    await client.post(f"/v1/inbox/conversations/{cid}/takeover", headers=headers)
    ok = await client.post(
        f"/v1/inbox/conversations/{cid}/messages", json={"text": "still here"}, headers=headers
    )
    assert ok.status_code == 200, ok.text


# ── Templates re-open the conversation ──────────────────────────────────────────────
async def test_template_send_and_approval_check(client: AsyncClient, db_session: AsyncSession) -> None:
    calls: list[dict] = []
    get_channel("whatsapp").transport = _transport(calls)
    headers = await _headers(client, "wa-template@example.com")
    _aid, channel_id = await _agent_with_whatsapp(client, headers)

    await _inbound(client, channel_id, "please get me a human")
    cid = (await client.get("/v1/inbox/conversations", headers=headers)).json()[0]["id"]
    conv = await db_session.get(Conversation, uuid.UUID(cid))
    conv.last_inbound_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=30)
    await db_session.flush()

    sent = await client.post(
        f"/v1/inbox/conversations/{cid}/template",
        json={"template": "order_update", "params": ["Rohak", "A-1"]},
        headers=headers,
    )
    assert sent.status_code == 200, sent.text

    body = json.loads([c for c in calls if "/PN1/messages" in c["url"]][-1]["body"])
    assert body["type"] == "template"
    assert body["template"]["name"] == "order_update"
    assert [p["text"] for p in body["template"]["components"][0]["parameters"]] == ["Rohak", "A-1"]

    # It shows in the transcript, so the operator can see what the customer got.
    detail = (await client.get(f"/v1/inbox/conversations/{cid}", headers=headers)).json()
    assert any("[template: order_update]" in (m["content"] or "") for m in detail["messages"])

    # A name the operator never registered with Meta is rejected here, with the list.
    bad = await client.post(
        f"/v1/inbox/conversations/{cid}/template", json={"template": "made_up"}, headers=headers
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "inbox.template_not_approved"
    assert bad.json()["error"]["details"]["approved"] == ["order_update", "welcome_back"]


# ── Defence in depth: Meta's own 131047 ─────────────────────────────────────────────
async def test_meta_131047_is_surfaced(client: AsyncClient) -> None:
    """Our clock said the window was open; Meta disagreed. Meta wins, loudly."""
    calls: list[dict] = []
    get_channel("whatsapp").transport = _transport(calls, error_code=WINDOW_CLOSED_CODE)
    headers = await _headers(client, "wa-131047@example.com")
    _aid, channel_id = await _agent_with_whatsapp(client, headers)

    await _inbound(client, channel_id, "I want a human")
    cid = (await client.get("/v1/inbox/conversations", headers=headers)).json()[0]["id"]
    await client.post(f"/v1/inbox/conversations/{cid}/takeover", headers=headers)

    # last_inbound_at is fresh, so the local check passes and we really do call Meta.
    refused = await client.post(
        f"/v1/inbox/conversations/{cid}/messages", json={"text": "hello"}, headers=headers
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "whatsapp_window_closed"


async def test_last_inbound_at_tracks_only_inbound(client: AsyncClient, db_session: AsyncSession) -> None:
    """`last_message_at` moves on our own sends too; the window must not be fooled by that."""
    calls: list[dict] = []
    get_channel("whatsapp").transport = _transport(calls)
    headers = await _headers(client, "wa-clock@example.com")
    _aid, channel_id = await _agent_with_whatsapp(client, headers)

    await _inbound(client, channel_id, "I need a human")
    cid = (await client.get("/v1/inbox/conversations", headers=headers)).json()[0]["id"]
    conv = (
        await db_session.execute(select(Conversation).where(Conversation.id == uuid.UUID(cid)))
    ).scalar_one()
    stale = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=25)
    conv.last_inbound_at = stale
    await db_session.flush()

    await client.post(f"/v1/inbox/conversations/{cid}/takeover", headers=headers)
    # takeover touches the conversation; the inbound clock must not have moved.
    await db_session.refresh(conv)
    assert conv.last_inbound_at == stale

    # A fresh inbound does move it, re-opening the window.
    await _inbound(client, channel_id, "hello again")
    await db_session.refresh(conv)
    assert conv.last_inbound_at > stale
    detail = (await client.get(f"/v1/inbox/conversations/{cid}", headers=headers)).json()
    assert detail["send_window"]["open"] is True
