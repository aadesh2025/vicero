"""What a real conversation does when no LLM provider can be resolved.

The rule these pin: a visitor must never be answered by the test stub. `FakeChatProvider`
replies with a literal ``echo: <their own message>``, so substituting it when an API key is
missing or revoked turns every customer on a client's site into someone talking to a parrot —
indistinguishable from a broken product, and invisible to the operator except in logs.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

# No `pytest.mark.anyio` here: the suite runs under pytest-asyncio (asyncio_mode = auto), and the `client` /
# `db_session` fixtures are pytest-asyncio fixtures. Marking the test for anyio runs it on a second event
# loop, so the first DB call died in CI with "Task ... attached to a different loop" (order-dependent).

FALLBACK = "Sorry, I can't answer right now — a teammate will follow up."


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee the "no key configured" condition regardless of the developer's .env.

    Without this the suite passes or fails depending on whether the machine happens to have
    ANTHROPIC_API_KEY set — with a key it reaches the network and fails differently.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)


async def _agent_with_unusable_provider(client: AsyncClient) -> tuple[dict[str, str], str, str]:
    """An agent configured for a provider with no key, so resolution fails at chat time."""
    signup = await client.post(
        "/v1/auth/signup", json={"email": "nokey@example.com", "password": "password123"}
    )
    token = signup.json()["access_token"]
    org = await client.post(
        "/v1/orgs", json={"name": "No Key Co"}, headers={"Authorization": f"Bearer {token}"}
    )
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}

    agent = await client.post("/v1/agents", json={"name": "Bot", "description": "d"}, headers=headers)
    agent_json = agent.json()
    patch = await client.patch(
        f"/v1/agents/{agent_json['id']}/versions/{agent_json['draft_version']}",
        json={
            # `anthropic` requires a key and none is configured in the test environment.
            "model_config": {"provider": "anthropic", "model": "claude-3-5-sonnet"},
            "fallback_message": FALLBACK,
        },
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    return headers, agent_json["id"], agent_json["public_key"]


async def test_a_visitor_gets_the_fallback_message_not_an_echo(client: AsyncClient) -> None:
    _, _, public_key = await _agent_with_unusable_provider(client)

    resp = await client.post(
        f"/v1/public/agents/{public_key}/chat",
        json={"message": "what are your opening hours?", "stream": False},
    )
    assert resp.status_code == 200, resp.text
    content = resp.json()["content"]

    assert content == FALLBACK
    # The specific regression: never the stub, and never the visitor's own words parroted back.
    assert "echo:" not in content
    assert "opening hours" not in content


async def test_the_dashboard_chat_also_avoids_the_stub(client: AsyncClient) -> None:
    headers, agent_id, _ = await _agent_with_unusable_provider(client)

    resp = await client.post(
        f"/v1/agents/{agent_id}/chat",
        json={"message": "hello there", "stream": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == FALLBACK
    assert "echo:" not in resp.json()["content"]


async def test_the_playground_surfaces_the_real_error_instead_of_faking_an_answer(
    client: AsyncClient,
) -> None:
    """The Playground answers "is my agent configured right?" — a stub answers it wrongly."""
    headers, agent_id, _ = await _agent_with_unusable_provider(client)

    resp = await client.post(
        f"/v1/agents/{agent_id}/playground/chat",
        json={"message": "hi", "stream": False},
        headers=headers,
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["error"]["code"] == "llm.provider_unavailable"
    # It names the provider, so the operator knows which key to add.
    assert "anthropic" in body["error"]["message"]


async def test_an_explicitly_fake_agent_still_works(client: AsyncClient) -> None:
    """Configuring `provider: "fake"` on purpose is a legitimate path the suites rely on."""
    signup = await client.post(
        "/v1/auth/signup", json={"email": "fakeprov@example.com", "password": "password123"}
    )
    token = signup.json()["access_token"]
    org = await client.post(
        "/v1/orgs", json={"name": "Fake Co"}, headers={"Authorization": f"Bearer {token}"}
    )
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    agent = await client.post("/v1/agents", json={"name": "Bot", "description": "d"}, headers=headers)
    agent_json = agent.json()
    await client.patch(
        f"/v1/agents/{agent_json['id']}/versions/{agent_json['draft_version']}",
        json={"model_config": {"provider": "fake", "model": "fake-1"}},
        headers=headers,
    )

    resp = await client.post(
        f"/v1/agents/{agent_json['id']}/playground/chat",
        json={"message": "ping", "stream": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert "echo: ping" in resp.json()["content"]
