"""Inbox "All chats" (`include_bot=true`) and contact name/avatar in the conversation views."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator

import httpx
import pytest
from httpx import AsyncClient

from app.channels import get_channel  # importing the package registers all adapters

PROFILE = {"id": "IG42", "name": "Aadesh", "username": "_.aadesx._", "profile_pic": "https://cdn/x.jpg"}


@pytest.fixture(autouse=True)
def _reset_transports() -> Iterator[None]:
    yield
    for t in ("instagram", "facebook"):
        adapter = get_channel(t)
        if adapter:
            adapter.transport = None


def _graph() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=PROFILE)
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "AllChatsOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _instagram_dm(client: AsyncClient, headers: dict[str, str], text: str = "Hi") -> str:
    """An Instagram DM the *bot* handles (no handoff). Returns the agent id."""
    get_channel("instagram").transport = _graph()
    agent = await client.post("/v1/agents", json={"name": "IG Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {"tools_enabled": False, "memory_enabled": True, "handoff_enabled": False},
        },
        headers=headers,
    )
    ch = await client.post(
        "/v1/channels",
        json={"agent_id": aid, "type": "instagram", "config": {"page_access_token": "t", "app_secret": "sec"}},
        headers=headers,
    )
    cid = ch.json()["id"]
    await client.post(f"/v1/channels/{cid}/enable", headers=headers)
    event = {"sender": {"id": "IG42"}, "message": {"text": text}}
    body = json.dumps({"object": "instagram", "entry": [{"id": "PAGE1", "messaging": [event]}]}).encode()
    sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    r = await client.post(
        f"/v1/channels/instagram/{cid}/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    return aid


async def test_default_inbox_still_hides_bot_handled_chats(client: AsyncClient) -> None:
    headers = await _headers(client, "allchats.default@example.com")
    await _instagram_dm(client, headers)
    assert (await client.get("/v1/inbox/conversations", headers=headers)).json() == []


async def test_include_bot_lists_them_with_name_avatar_and_username(client: AsyncClient) -> None:
    headers = await _headers(client, "allchats.on@example.com")
    await _instagram_dm(client, headers)

    items = (await client.get("/v1/inbox/conversations?include_bot=true", headers=headers)).json()
    assert len(items) == 1
    item = items[0]
    assert item["channel"] == "instagram"
    assert item["handoff"] is None
    assert item["status"] == "active"
    assert item["contact"]["display_name"] == "Aadesh"
    assert item["contact"]["avatar_url"] == "https://cdn/x.jpg"
    assert item["contact"]["username"] == "_.aadesx._"

    only_ig = (await client.get("/v1/inbox/conversations?include_bot=true&channel=instagram", headers=headers)).json()
    assert len(only_ig) == 1
    other = (await client.get("/v1/inbox/conversations?include_bot=true&channel=whatsapp", headers=headers)).json()
    assert other == []


async def test_include_bot_is_org_scoped(client: AsyncClient) -> None:
    mine = await _headers(client, "allchats.mine@example.com")
    theirs = await _headers(client, "allchats.theirs@example.com")
    await _instagram_dm(client, mine)
    assert (await client.get("/v1/inbox/conversations?include_bot=true", headers=theirs)).json() == []


async def test_include_bot_needs_the_inbox_permission(client: AsyncClient) -> None:
    owner = await _headers(client, "allchats.owner@example.com")
    viewer = await _headers(client, "allchats.viewer@example.com")
    invite = await client.post(
        f"/v1/orgs/{owner['X-Org-Id']}/invitations",
        json={"email": "allchats.viewer@example.com", "role": "viewer"},
        headers=owner,
    )
    await client.post(
        f"/v1/orgs/invitations/{invite.json()['accept_token']}/accept",
        headers={"Authorization": viewer["Authorization"]},
    )
    r = await client.get(
        "/v1/inbox/conversations?include_bot=true",
        headers={"Authorization": viewer["Authorization"], "X-Org-Id": owner["X-Org-Id"]},
    )
    assert r.status_code == 403


async def test_taking_over_a_bot_chat_opens_the_handoff_and_pauses_the_bot(client: AsyncClient) -> None:
    headers = await _headers(client, "allchats.takeover@example.com")
    await _instagram_dm(client, headers)
    cid = (await client.get("/v1/inbox/conversations?include_bot=true", headers=headers)).json()[0]["id"]

    take = await client.post(f"/v1/inbox/conversations/{cid}/takeover", headers=headers)
    assert take.status_code == 200, take.text
    assert take.json()["status"] == "handoff"
    assert take.json()["handoff"]["status"] == "assigned"
    assert take.json()["handoff"]["reason"] == "operator_takeover"

    # Now it is a normal inbox thread: it shows in the default queue and the operator can reply.
    queue = (await client.get("/v1/inbox/conversations", headers=headers)).json()
    assert [i["id"] for i in queue] == [cid]
    reply = await client.post(
        f"/v1/inbox/conversations/{cid}/messages", json={"text": "Hello from a human"}, headers=headers
    )
    assert reply.status_code == 200, reply.text


async def test_conversations_api_carries_the_contact(client: AsyncClient) -> None:
    headers = await _headers(client, "allchats.conv@example.com")
    await _instagram_dm(client, headers)

    rows = (await client.get("/v1/conversations", headers=headers)).json()
    assert len(rows) == 1
    assert rows[0]["contact"]["display_name"] == "Aadesh"
    assert rows[0]["contact"]["username"] == "_.aadesx._"
    assert rows[0]["channel_user_id"] == "IG42"

    detail = (await client.get(f"/v1/conversations/{rows[0]['id']}", headers=headers)).json()
    assert detail["contact"]["avatar_url"] == "https://cdn/x.jpg"
