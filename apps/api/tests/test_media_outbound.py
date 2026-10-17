"""M4: sending product photos - adapters, catalog tools, delivery order and failure handling (docs/26)."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel
from app.channels.base import BaseChannel
from app.channels.service import InboundOutcome, _send_images, run_and_send
from app.channels.whatsapp import WhatsAppWindowClosed
from app.core.config import settings
from app.llm.types import ChatRequest, StreamEvent, ToolCall, ToolSpec, Usage
from app.media import service as media_service
from app.media import urls
from app.models import Channel, Message, Organization, Product, ProductImage
from app.tools.base import ToolContext
from app.tools.builtins import CATALOG_TOOLS, catalog_enabled


def _jpeg(n: int = 0) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40 + n, 30), (n * 20 % 255, 80, 160)).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def _env(tmp_path: Path) -> Iterator[None]:
    prev = (settings.media_dir, settings.media_feature_enabled, settings.max_images_per_reply)
    settings.media_dir = str(tmp_path / "media")
    settings.media_feature_enabled = True
    settings.max_images_per_reply = 3
    yield
    settings.media_dir, settings.media_feature_enabled, settings.max_images_per_reply = prev
    for t in ("whatsapp", "instagram", "facebook"):
        adapter = get_channel(t)
        if adapter:
            adapter.transport = None


# ── adapters: exact request bodies ───────────────────────────────────────────
def _capture(calls: list[dict[str, Any]], status: int = 200, body: dict[str, Any] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "url": str(request.url),
                "json": json.loads(request.content or b"{}"),
                "auth": request.headers.get("authorization"),
            }
        )
        return httpx.Response(status, json=body or {"ok": True})

    return httpx.MockTransport(handler)


class _Chan:
    id = uuid.uuid4()
    status = "active"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


async def test_whatsapp_send_image_body() -> None:
    calls: list[dict[str, Any]] = []
    adapter = get_channel("whatsapp")
    adapter.transport = _capture(calls)
    ok = await adapter.send_image(_Chan({"phone_number_id": "PN1", "access_token": "tok"}), "9198", "https://x/y.jpg")
    assert ok is True
    assert calls[0]["url"].endswith("/PN1/messages") and calls[0]["auth"] == "Bearer tok"
    assert calls[0]["json"] == {
        "messaging_product": "whatsapp",
        "to": "9198",
        "type": "image",
        "image": {"link": "https://x/y.jpg"},
    }
    await adapter.send_image(
        _Chan({"phone_number_id": "PN1", "access_token": "tok"}), "9198", "https://x/y.jpg", "Nice"
    )
    assert calls[1]["json"]["image"] == {"link": "https://x/y.jpg", "caption": "Nice"}


@pytest.mark.parametrize("ctype", ["facebook", "instagram"])
async def test_meta_send_image_body(ctype: str) -> None:
    calls: list[dict[str, Any]] = []
    adapter = get_channel(ctype)
    adapter.transport = _capture(calls)
    assert await adapter.send_image(_Chan({"page_access_token": "tok"}), "U1", "https://x/y.jpg") is True
    assert calls[0]["url"].endswith("/me/messages")
    assert calls[0]["json"] == {
        "recipient": {"id": "U1"},
        "messaging_type": "RESPONSE",
        "message": {"attachment": {"type": "image", "payload": {"url": "https://x/y.jpg", "is_reusable": False}}},
    }


async def test_send_image_rejection_and_dead_token() -> None:
    from app.channels.base import ChannelAuthRejected

    adapter = get_channel("facebook")
    adapter.transport = _capture([], 400, {"error": {"code": 100, "message": "bad url"}})
    assert await adapter.send_image(_Chan({"page_access_token": "t"}), "U1", "https://x/y.jpg") is False
    adapter.transport = _capture([], 400, {"error": {"code": 190, "message": "expired"}})
    with pytest.raises(ChannelAuthRejected):
        await adapter.send_image(_Chan({"page_access_token": "t"}), "U1", "https://x/y.jpg")
    wa = get_channel("whatsapp")
    wa.transport = _capture([], 400, {"error": {"code": 131047, "message": "window"}})
    with pytest.raises(WhatsAppWindowClosed):
        await wa.send_image(_Chan({"phone_number_id": "P", "access_token": "t"}), "1", "https://x/y.jpg")


def test_only_meta_adapters_claim_image_support() -> None:
    assert get_channel("whatsapp").supports_images and get_channel("facebook").supports_images
    assert get_channel("instagram").supports_images
    assert not get_channel("telegram").supports_images
    with pytest.raises(NotImplementedError):
        import asyncio

        asyncio.run(BaseChannel().send_image(None, "x", "u"))  # type: ignore[arg-type]


# ── catalog tools ────────────────────────────────────────────────────────────
async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "CatOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _agent(client: AsyncClient, headers: dict[str, str], *, catalog: bool, tools: bool = False) -> str:
    aid = (await client.post("/v1/agents", json={"name": "Shop Bot"}, headers=headers)).json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {
                "tools_enabled": tools,
                "memory_enabled": True,
                "handoff_enabled": False,
                "product_catalog_enabled": catalog,
            },
        },
        headers=headers,
    )
    return str(aid)


async def _product(
    session: AsyncSession, org_id: uuid.UUID, name: str, photos: int, org: Organization | None = None
) -> Product:
    product = Product(organization_id=org_id, name=name, price_minor=129900, currency="INR", description="Soft cotton.")
    session.add(product)
    await session.flush()
    org = org or await session.get(Organization, org_id)
    assert org is not None
    for i in range(photos):
        asset = await media_service.save_asset(session, org, "product", _jpeg(i + hash(name) % 50))
        session.add(ProductImage(product_id=product.id, media_asset_id=asset.id, position=i))
    await session.flush()
    await session.refresh(product)
    return product


async def _ctx(session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID, *, catalog: bool = True) -> ToolContext:
    from app.models import AgentVersion

    version = (
        await session.execute(select(AgentVersion).where(AgentVersion.agent_id == agent_id).limit(1))
    ).scalar_one()
    version.features = {**(version.features or {}), "product_catalog_enabled": catalog}
    return ToolContext(session=session, org_id=org_id, agent_id=agent_id, version=version, outbound_media=[])


async def test_search_products_tool_returns_summaries(client: AsyncClient, db_session: AsyncSession) -> None:
    h = await _headers(client, "cat1@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    aid = uuid.UUID(await _agent(client, h, catalog=True))
    p = await _product(db_session, org_id, "Black Kurta", 2)
    ctx = await _ctx(db_session, org_id, aid)

    res = await CATALOG_TOOLS["search_products"].run(ctx, {"query": "kurta"})
    assert res.status == "success"
    row = res.output["results"][0]
    assert row["id"] == str(p.id) and row["price"] == 1299.0 and row["currency"] == "INR"
    assert row["image_count"] == 2 and len(row["summary"]) <= 200

    assert (await CATALOG_TOOLS["search_products"].run(ctx, {"query": " "})).status == "error"
    off = await _ctx(db_session, org_id, aid, catalog=False)
    assert (await CATALOG_TOOLS["search_products"].run(off, {"query": "kurta"})).status == "error"


async def test_send_product_images_validates_clamps_and_isolates(client: AsyncClient, db_session: AsyncSession) -> None:
    h = await _headers(client, "cat2@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    other = uuid.UUID((await _headers(client, "cat2b@example.com"))["X-Org-Id"])
    aid = uuid.UUID(await _agent(client, h, catalog=True))
    mine = await _product(db_session, org_id, "Mine", 5)
    bare = await _product(db_session, org_id, "No Photos", 0)
    theirs = await _product(db_session, other, "Theirs", 2)
    ctx = await _ctx(db_session, org_id, aid)
    send = CATALOG_TOOLS["send_product_images"].run

    assert (await send(ctx, {"product_id": str(theirs.id)})).output == {"error": "not_found"}
    assert (await send(ctx, {"product_id": "not-a-uuid"})).output == {"error": "not_found"}
    assert (await send(ctx, {"product_id": str(uuid.uuid4())})).output == {"error": "not_found"}
    assert (await send(ctx, {"product_id": str(bare.id)})).output == {"error": "no_images"}
    assert ctx.outbound_media == []

    assert (await send(ctx, {"product_id": str(mine.id)})).output == {"queued": 1}
    assert (await send(ctx, {"product_id": str(mine.id), "count": 99})).output == {"queued": 2}  # 3 total cap
    assert len(ctx.outbound_media) == 3 and len(set(ctx.outbound_media)) == 3
    assert (await send(ctx, {"product_id": str(mine.id), "count": 2})).output == {"queued": 0}

    mine.is_active = False
    await db_session.flush()
    fresh = await _ctx(db_session, org_id, aid)
    assert (await send(fresh, {"product_id": str(mine.id)})).output == {"error": "not_found"}


async def test_images_cannot_be_queued_on_a_surface_without_image_support(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    h = await _headers(client, "cat3@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    aid = uuid.UUID(await _agent(client, h, catalog=True))
    p = await _product(db_session, org_id, "Thing", 1)
    ctx = await _ctx(db_session, org_id, aid)
    ctx.outbound_media = None
    assert (await CATALOG_TOOLS["send_product_images"].run(ctx, {"product_id": str(p.id)})).status == "error"


async def test_build_tooling_offers_tools_only_when_enabled(client: AsyncClient, db_session: AsyncSession) -> None:
    from app.models import Agent, AgentVersion
    from app.tools.service import build_tooling

    h = await _headers(client, "cat4@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    on = uuid.UUID(await _agent(client, h, catalog=True))
    off = uuid.UUID(await _agent(client, h, catalog=False))

    async def tools(agent_id: uuid.UUID, media: list[uuid.UUID] | None) -> list[str]:
        agent = await db_session.get(Agent, agent_id)
        version = (
            await db_session.execute(select(AgentVersion).where(AgentVersion.agent_id == agent_id).limit(1))
        ).scalar_one()
        assert agent is not None
        specs, executor = await build_tooling(db_session, org_id, agent, version, None, outbound_media=media)
        assert (executor is None) == (not specs)
        return sorted(s.name for s in specs)

    assert await tools(on, []) == ["search_products", "send_product_images"]
    assert await tools(on, None) == ["search_products"]  # web widget: no photos
    assert await tools(off, []) == []
    settings.media_feature_enabled = False
    assert await tools(on, []) == []  # the platform switch wins
    settings.media_feature_enabled = True
    assert catalog_enabled(type("V", (), {"features": {"product_catalog_enabled": True}})())


async def test_features_toggle_roundtrips_through_the_agent_api(client: AsyncClient) -> None:
    h = await _headers(client, "cat5@example.com")
    aid = await _agent(client, h, catalog=True)
    versions = (await client.get(f"/v1/agents/{aid}/versions", headers=h)).json()
    assert versions[0]["features"]["product_catalog_enabled"] is True


# ── delivery ─────────────────────────────────────────────────────────────────
class _RecordingAdapter(BaseChannel):
    type = "whatsapp"
    supports_images = True

    def __init__(self, fail_on: set[int] | None = None, raises: Exception | None = None) -> None:
        super().__init__()
        self.events: list[str] = []
        self.fail_on = fail_on or set()
        self.raises = raises
        self.n = 0

    async def send(self, channel: Any, to: str, text: str) -> None:
        self.events.append(f"text:{text}")

    async def send_image(self, channel: Any, to: str, url: str, caption: str | None = None) -> bool:
        self.n += 1
        if self.raises is not None:
            raise self.raises
        self.events.append(f"image:{self.n}")
        return self.n not in self.fail_on


async def _delivery_setup(client: AsyncClient, db_session: AsyncSession, email: str, photos: int = 3):  # type: ignore[no-untyped-def]
    h = await _headers(client, email)
    org_id = uuid.UUID(h["X-Org-Id"])
    aid = await _agent(client, h, catalog=True)
    product = await _product(db_session, org_id, "Kurta", photos)
    from app.models import Agent

    channel = Channel(organization_id=org_id, agent_id=uuid.UUID(aid), type="whatsapp", name="wa", config={})
    db_session.add(channel)
    await db_session.flush()
    from app.modules.products.service import image_assets

    assets = await image_assets(db_session, product.id)
    assert await db_session.get(Agent, uuid.UUID(aid)) is not None
    return channel, [a.id for a in assets], org_id


def _outcome(media: list[uuid.UUID], reply: str | None = "Here it is") -> InboundOutcome:
    from app.channels.base import InboundMessage

    return InboundOutcome(InboundMessage("9198", "show me"), reply, media, None)


async def test_text_goes_first_then_images_in_order(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel, media, _ = await _delivery_setup(client, db_session, "del1@example.com", photos=2)
    adapter = _RecordingAdapter()

    async def fake_process(*a: Any, **k: Any) -> InboundOutcome:
        return _outcome(media)

    monkeypatch.setattr("app.channels.service.process_inbound_full", fake_process)
    await run_and_send(db_session, channel, adapter, {})
    assert adapter.events == ["text:Here it is", "image:1", "image:2"]


async def test_one_rejected_image_does_not_stop_the_rest(client: AsyncClient, db_session: AsyncSession) -> None:
    from app.models import Conversation

    channel, media, org_id = await _delivery_setup(client, db_session, "del2@example.com", photos=3)
    adapter = _RecordingAdapter(fail_on={2})
    conv = Conversation(organization_id=org_id, agent_id=channel.agent_id, channel="whatsapp", channel_user_id="9198")
    db_session.add(conv)
    await db_session.flush()
    msg = Message(conversation_id=conv.id, organization_id=org_id, role="assistant", content="Here it is")
    db_session.add(msg)
    await db_session.flush()
    outcome = _outcome(media)
    outcome.assistant_message = msg

    await _send_images(db_session, channel, adapter, "9198", outcome)

    assert adapter.events == ["image:1", "image:2", "image:3"]  # the rejected one did not stop the third
    assert [a["media_id"] for a in msg.attachments] == [str(media[0]), str(media[2])]
    assert all(a["direction"] == "out" for a in msg.attachments)


async def test_window_closed_stops_remaining_images(client: AsyncClient, db_session: AsyncSession) -> None:
    channel, media, _ = await _delivery_setup(client, db_session, "del3@example.com", photos=3)
    adapter = _RecordingAdapter(raises=WhatsAppWindowClosed())
    await _send_images(db_session, channel, adapter, "9198", _outcome(media))
    assert adapter.n == 1 and adapter.events == []


async def test_dead_token_flags_channel_and_stops(client: AsyncClient, db_session: AsyncSession) -> None:
    from app.channels.base import ChannelAuthRejected

    channel, media, _ = await _delivery_setup(client, db_session, "del4@example.com", photos=3)
    adapter = _RecordingAdapter(raises=ChannelAuthRejected(190))
    await _send_images(db_session, channel, adapter, "9198", _outcome(media))
    assert adapter.n == 1
    assert channel.status == "needs_reconnect"


async def test_foreign_or_missing_media_ids_are_skipped(client: AsyncClient, db_session: AsyncSession) -> None:
    channel, media, _ = await _delivery_setup(client, db_session, "del5@example.com", photos=1)
    other = uuid.UUID((await _headers(client, "del5b@example.com"))["X-Org-Id"])
    foreign = await _product(db_session, other, "Foreign", 1)
    from app.modules.products.service import image_assets

    foreign_media = [a.id for a in await image_assets(db_session, foreign.id)]
    adapter = _RecordingAdapter()
    await _send_images(db_session, channel, adapter, "9198", _outcome([*foreign_media, uuid.uuid4(), *media]))
    assert adapter.events == ["image:1"]  # only the one that belongs to this org


async def test_image_cap_per_reply(client: AsyncClient, db_session: AsyncSession) -> None:
    channel, media, _ = await _delivery_setup(client, db_session, "del6@example.com", photos=5)
    adapter = _RecordingAdapter()
    await _send_images(db_session, channel, adapter, "9198", _outcome(media))
    assert adapter.n == 3


async def test_signed_link_is_what_meta_is_given(client: AsyncClient, db_session: AsyncSession) -> None:
    channel, media, _ = await _delivery_setup(client, db_session, "del7@example.com", photos=1)
    seen: list[str] = []

    class A(_RecordingAdapter):
        async def send_image(self, channel: Any, to: str, url: str, caption: str | None = None) -> bool:
            seen.append(url)
            return True

    await _send_images(db_session, channel, A(), "9198", _outcome(media))
    assert seen and seen[0].startswith(f"{settings.api_base_url.rstrip('/')}/v1/media/public/{media[0]}?exp=")
    exp = int(seen[0].split("exp=")[1].split("&")[0])
    sig = seen[0].split("sig=")[1]
    assert urls.verify(media[0], exp, sig)


# ── end to end: a scripted model that calls the tools, through the real WhatsApp webhook ──
class _ScriptedShopModel:
    """Searches, then asks for the photos, then answers - the way a real tool-calling model would."""

    name = "scripted"

    def __init__(self) -> None:
        self.step = 0
        self.product_id: str | None = None

    def supports_tools(self) -> bool:
        return True

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        tool_msgs = [m for m in req.messages if m.role == "tool"]
        offered = {t.name for t in (req.tools or [])}
        assert {"search_products", "send_product_images"} <= offered
        if not tool_msgs:
            yield StreamEvent(
                type="tool_call", tool_call=ToolCall(id="c1", name="search_products", arguments={"query": "kurta"})
            )
            yield StreamEvent(type="done", usage=Usage(prompt_tokens=5), finish_reason="tool_calls")
        elif len(tool_msgs) == 1:
            pid = json.loads(tool_msgs[0].content or "{}")["results"][0]["id"]
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="c2", name="send_product_images", arguments={"product_id": pid, "count": 2}),
            )
            yield StreamEvent(type="done", usage=Usage(prompt_tokens=5), finish_reason="tool_calls")
        else:
            yield StreamEvent(type="token", delta="Sending the photos now.")
            yield StreamEvent(type="done", usage=Usage(prompt_tokens=5, completion_tokens=4), finish_reason="stop")

    async def chat(self, req: ChatRequest):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    _ = ToolSpec


async def test_end_to_end_search_then_photos_over_whatsapp(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await _headers(client, "e2e1@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    aid = await _agent(client, h, catalog=True, tools=True)
    await _product(db_session, org_id, "Black Kurta", 3)
    ch = await client.post(
        "/v1/channels",
        json={
            "agent_id": aid,
            "type": "whatsapp",
            "config": {"phone_number_id": "PN1", "access_token": "atok", "app_secret": "appsec"},
        },
        headers=h,
    )
    cid = ch.json()["id"]
    await client.post(f"/v1/channels/{cid}/enable", headers=h)

    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "json": json.loads(request.content or b"{}")})
        return httpx.Response(200, json={"ok": True})

    get_channel("whatsapp").transport = httpx.MockTransport(handler)
    model = _ScriptedShopModel()

    async def fake_resolve(*a: Any, **k: Any) -> _ScriptedShopModel:
        return model

    monkeypatch.setattr("app.modules.conversations.service._resolve_provider", fake_resolve)

    payload = {
        "entry": [
            {
                "changes": [
                    {"value": {"messages": [{"from": "9198", "type": "text", "text": {"body": "show me the kurta"}}]}}
                ]
            }
        ]
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"appsec", body, hashlib.sha256).hexdigest()
    r = await client.post(
        f"/v1/channels/whatsapp/{cid}/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text

    sends = [c["json"] for c in calls if "/PN1/messages" in c["url"]]
    assert [s["type"] for s in sends] == ["text", "image", "image"]
    assert sends[0]["text"]["body"] == "Sending the photos now."
    assert all(s["image"]["link"].startswith(settings.api_base_url) for s in sends[1:])

    assistant = (
        (
            await db_session.execute(
                select(Message).where(Message.organization_id == org_id, Message.role == "assistant")
            )
        )
        .scalars()
        .all()
    )
    delivered = [m for m in assistant if m.attachments]
    assert len(delivered) == 1 and len(delivered[0].attachments) == 2
    assert all(a["direction"] == "out" and a["kind"] == "image" for a in delivered[0].attachments)
