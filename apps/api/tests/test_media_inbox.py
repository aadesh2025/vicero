"""M5: photos in the inbox - attachments on messages, operator image send, typing indicator (docs/26)."""

from __future__ import annotations

import io
import uuid
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel
from app.models import Conversation
from tests.test_media_inbound import (
    _env,  # noqa: F401  (autouse fixture: temp media dir, feature on, transports reset)
    _headers,
    _post_wa,
    _sends,
    _stub_vision,
    _wa_channel,
    _wa_transport,
)


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (20, 20), (0, 90, 200)).save(out, format="PNG")
    return out.getvalue()


async def _photo_conversation(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, email: str
) -> tuple[dict[str, str], str, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    get_channel("whatsapp").transport = _wa_transport(calls)
    _stub_vision(monkeypatch, "A cracked screen")
    headers = await _headers(client, email)
    cid = await _wa_channel(client, headers)
    await _post_wa(client, cid, {"type": "image", "image": {"id": "MID1", "caption": "broken"}})
    listed = (await client.get("/v1/inbox/conversations", params={"status": "all"}, headers=headers)).json()
    if not listed:  # bot-handled chats are listed under "all chats"
        listed = (await client.get("/v1/conversations", headers=headers)).json()
    return headers, str(listed[0]["id"]), calls


async def test_messages_carry_their_attachments(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, conv_id, _ = await _photo_conversation(client, db_session, monkeypatch, "mi1@example.com")
    msgs = (await client.get(f"/v1/conversations/{conv_id}/messages", headers=headers)).json()
    photo = next(m for m in msgs if m["role"] == "user")
    assert photo["attachments"][0]["direction"] == "in"
    assert photo["attachments"][0]["description"] == "A cracked screen"
    # The image itself is served to this org only.
    media_id = photo["attachments"][0]["media_id"]
    assert (await client.get(f"/v1/media/{media_id}", headers=headers)).status_code == 200
    other = await _headers(client, "mi1b@example.com")
    assert (await client.get(f"/v1/media/{media_id}", headers=other)).status_code == 404
    reply = next(m for m in msgs if m["role"] == "assistant")
    assert reply["attachments"] == []


async def test_operator_can_send_a_photo(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, conv_id, calls = await _photo_conversation(client, db_session, monkeypatch, "mi2@example.com")
    assert (await client.post(f"/v1/inbox/conversations/{conv_id}/takeover", headers=headers)).status_code == 200

    r = await client.post(
        f"/v1/inbox/conversations/{conv_id}/images",
        files={"file": ("size-chart.png", _png(), "image/png")},
        data={"caption": "Here is the size chart"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "Here is the size chart"
    assert body["attachments"][0]["direction"] == "out"

    image_sends = [
        s["json"] for s in (c for c in [{"json": __import__("json").loads(c["body"])} for c in _sends(calls)])
    ]
    sent = image_sends[-1]
    assert sent["type"] == "image" and sent["image"]["caption"] == "Here is the size chart"
    assert "/v1/media/public/" in sent["image"]["link"]


async def test_operator_image_rejects_non_images_and_widget_chats(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, conv_id, _ = await _photo_conversation(client, db_session, monkeypatch, "mi3@example.com")
    bad = await client.post(
        f"/v1/inbox/conversations/{conv_id}/images",
        files={"file": ("x.png", b"<svg/>", "image/png")},
        headers=headers,
    )
    assert bad.status_code == 422

    conv = await db_session.get(Conversation, uuid.UUID(conv_id))
    assert conv is not None
    conv.channel = "widget"
    await db_session.flush()
    widget = await client.post(
        f"/v1/inbox/conversations/{conv_id}/images",
        files={"file": ("x.png", _png(), "image/png")},
        headers=headers,
    )
    assert widget.status_code == 400 and widget.json()["error"]["code"] == "inbox.images_unsupported"


async def test_meta_typing_indicator_body() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "json": __import__("json").loads(request.content)})
        return httpx.Response(200, json={})

    class Chan:
        id = uuid.uuid4()
        status = "active"
        config = {"page_access_token": "tok"}  # noqa: RUF012

    for kind in ("facebook", "instagram"):
        adapter = get_channel(kind)
        adapter.transport = httpx.MockTransport(handler)
        await adapter.send_typing(Chan(), "U1")
    assert [c["json"] for c in calls] == [{"recipient": {"id": "U1"}, "sender_action": "typing_on"}] * 2
    # Adapters without support do nothing and do not raise.
    await get_channel("telegram").send_typing(Chan(), "U1")  # type: ignore[arg-type]


async def test_typing_failure_never_breaks_the_adapter() -> None:
    adapter = get_channel("facebook")
    adapter.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))

    class Chan:
        id = uuid.uuid4()
        status = "active"
        config = {"page_access_token": "tok"}  # noqa: RUF012

    await adapter.send_typing(Chan(), "U1")  # must not raise
