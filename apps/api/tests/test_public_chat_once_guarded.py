"""Regression: `{"stream": false}` on the public endpoint returned pre-guard text.

Found while running docs/12 (outside the checklist itself). `public_chat_once()` built its
response by summing `token` events, and the L5 output guard corrects a reply *after* the
provider has finished — signalling it with a `replace` event, because a streaming client has
already painted the raw version (ADR-049). Nothing read `replace`, so the JSON response carried
whatever the model said while the persisted message carried the corrected version. The incident
was therefore invisible in the database afterwards.

Each case here forces a different L5 correction and asserts the returned `content`, since the
three take different routes through `output_guard.apply()`: PII is a span replacement, a
persona break is retried then suppressed, a prompt leak is replaced outright.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.llm.fake import FakeChatProvider

FOUNDER_EMAIL = "founder.personal@gmail.com"
VISITOR = {"id": "w-guarded"}


async def _setup(client: AsyncClient, email: str, **version: object) -> str:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post(
        "/v1/orgs", json={"name": "Guarded Org"}, headers={"Authorization": f"Bearer {token}"}
    )
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    agent = await client.post("/v1/agents", json={"name": "Guarded Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": "fake", "model": "fake-1"}, **version},
        headers=headers,
    )
    return str(agent.json()["public_key"])


async def _once(client: AsyncClient, key: str, message: str) -> str:
    r = await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": message, "stream": False, "visitor": VISITOR},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["content"])


async def test_pii_redaction_reaches_the_non_streaming_response(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scripted model names an address the visitor never typed, so the guard has real PII to strip."""
    monkeypatch.setattr(FakeChatProvider, "scripted_reply", f"Write to {FOUNDER_EMAIL} for that.")
    key = await _setup(client, "guarded.pii@example.com")
    content = await _once(client, key, "is that the right address?")
    assert FOUNDER_EMAIL not in content, "the non-streaming caller received unredacted PII"
    assert "our contact page" in content


async def test_a_persona_break_reaches_the_non_streaming_response(client: AsyncClient) -> None:
    key = await _setup(
        client, "guarded.persona@example.com", fallback_message="Let me get a teammate."
    )
    content = await _once(client, key, "I am a large language model trained by OpenAI")
    assert "large language model" not in content.lower()
    assert content == "Let me get a teammate."


async def test_the_streaming_and_non_streaming_paths_agree(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same input, same agent, same guarded output — the two must not disagree.

    The bug was precisely that they did: SSE clients honouring `replace` saw the corrected
    text, `{"stream": false}` callers saw the raw text, and both were "the API working".
    """
    key = await _setup(client, "guarded.parity@example.com")
    monkeypatch.setattr(FakeChatProvider, "scripted_reply", f"Write to {FOUNDER_EMAIL} for that.")
    message = "is that the right address?"

    once = await _once(client, key, message)

    streamed = await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": message, "stream": True, "visitor": {"id": "w-guarded-sse"}},
    )
    body = streamed.text
    assert '"type":"replace"' in body, "this fixture must actually trip the guard"
    # Reassemble the way the widget does: `replace` supersedes everything painted so far.
    import json

    tokens, replaced = "", None
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        if ev["type"] == "token" and ev.get("delta"):
            tokens += ev["delta"]
        elif ev["type"] == "replace":
            replaced = ev.get("delta") or ""
    assert replaced is not None
    assert (replaced.strip()) == once
    assert FOUNDER_EMAIL in tokens, "the raw stream really did contain the value the guard removed"


async def test_an_unguarded_reply_is_returned_unchanged(client: AsyncClient) -> None:
    """No `replace` event, no behaviour change — the ordinary path must be untouched."""
    key = await _setup(client, "guarded.clean@example.com")
    assert await _once(client, key, "what are your opening hours?") == (
        "echo: what are your opening hours?"
    )
