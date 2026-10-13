"""The Meta reply path as it was before PR #20: a message goes in, a reply comes out.

PR #20's latency changes were rolled back after Instagram DMs stopped getting replies (see
docs/PROGRESS.md, 13 Oct). These tests pin the old shape so a later re-ship has to prove itself
against it: the DM is answered end to end, and the inbound task runs on a fresh loop per message.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.channels import get_channel  # importing the package registers all adapters
from app.worker import tasks


@pytest.fixture(autouse=True)
def _reset_transport() -> Iterator[None]:
    yield
    adapter = get_channel("instagram")
    if adapter:
        adapter.transport = None


async def test_an_instagram_dm_is_answered_end_to_end(client: AsyncClient) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "IG77", "name": "Pat", "username": "pat"})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    get_channel("instagram").transport = httpx.MockTransport(handler)

    signup = await client.post("/v1/auth/signup", json={"email": "replypath@example.com", "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "ReplyPathOrg"}, headers={"Authorization": f"Bearer {token}"})
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}

    agent = await client.post("/v1/agents", json={"name": "Reply Bot"}, headers=headers)
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

    event = {"sender": {"id": "IG77"}, "message": {"text": "Hi"}}
    body = json.dumps({"object": "instagram", "entry": [{"id": "PAGE1", "messaging": [event]}]}).encode()
    sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    r = await client.post(
        f"/v1/channels/instagram/{cid}/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text

    # Message in -> a reply went out to the sender, and it is not empty.
    assert len(sent) == 1, f"expected exactly one outbound reply, got {sent}"
    assert sent[0]["recipient"]["id"] == "IG77"
    assert sent[0]["message"]["text"].strip()

    # ...and the bot's answer is stored on the conversation.
    convs = (await client.get("/v1/conversations", headers=headers)).json()
    items = convs["items"] if isinstance(convs, dict) else convs
    assert len(items) == 1
    msgs = (await client.get(f"/v1/conversations/{items[0]['id']}/messages", headers=headers)).json()
    msgs = msgs["items"] if isinstance(msgs, dict) else msgs
    assert [m["role"] for m in msgs][-2:] == ["user", "assistant"]


def test_the_inbound_task_runs_on_a_fresh_loop_per_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker task goes through `_run` (a new event loop per message), not a long-lived one."""
    assert not hasattr(tasks, "_run_persistent"), "a persistent worker loop is back; re-ship it deliberately"
    assert "_run(" in inspect.getsource(tasks.meta_inbound_task)

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake(channel_id: str, payload: dict[str, object]) -> None:
        calls.append((channel_id, payload))

    monkeypatch.setattr(tasks, "_run_meta_inbound", fake)
    tasks.meta_inbound_task.run("chan-1", {"x": 1})
    tasks.meta_inbound_task.run("chan-1", {"x": 2})
    assert calls == [("chan-1", {"x": 1}), ("chan-1", {"x": 2})]
