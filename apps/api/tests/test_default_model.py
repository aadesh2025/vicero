"""The platform default model must exist on the provider (found live 2026-09-24).

Groq retired `llama-3.3-70b-versatile` and `llama-3.1-8b-instant`; both were hardcoded as defaults,
so every agent created with no overrides answered every visitor with its fallback message and
nothing in CI could see it (the fake provider never 404s). Two layers:

- **Offline, always on:** the defaults must resolve to entries in the provider catalog, and a
  zero-override agent must actually be created on that default. This catches the defaults
  drifting apart from the catalog; it cannot know what Groq retired.
- **Live, opt-in** (`RUN_LIVE_LLM_TESTS=1` + a real `GROQ_API_KEY`): asks Groq itself. This is the
  one that would have caught the incident. No model name is hardcoded here — the test reads the
  defaults from the code and the truth from `/models`, so it cannot go stale the way the
  defaults did.
"""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.llm import catalog
from app.modules.agents.service import DEFAULT_MODEL_CONFIG

LIVE = os.environ.get("RUN_LIVE_LLM_TESTS") == "1" and bool((settings.groq_api_key or "").strip())
live_only = pytest.mark.skipif(not LIVE, reason="set RUN_LIVE_LLM_TESTS=1 and a real GROQ_API_KEY to hit Groq")


def _groq_catalog_ids() -> list[str]:
    spec = catalog.get_provider("groq")
    assert spec is not None
    return [m.id for m in spec.models]


def test_platform_defaults_are_in_the_provider_catalog() -> None:
    assert DEFAULT_MODEL_CONFIG["provider"] == "groq"
    ids = _groq_catalog_ids()
    assert DEFAULT_MODEL_CONFIG["model"] in ids
    assert ids[0] == DEFAULT_MODEL_CONFIG["model"], "DEFAULT_CHAT_MODEL must be the first Groq entry"
    assert settings.summary_provider == "groq"
    assert settings.summary_model in ids
    assert settings.guard_injection_model in catalog.GUARD_MODELS_BY_ID


async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "DefaultModelOrg"}, headers={"Authorization": f"Bearer {token}"})
    assert org.status_code == 201, org.text
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def test_agent_created_with_zero_overrides_gets_the_platform_default(client: AsyncClient) -> None:
    headers = await _headers(client, "defaultmodel@example.com")
    agent = await client.post("/v1/agents", json={"name": "Zero Overrides"}, headers=headers)
    assert agent.status_code == 201, agent.text
    versions = await client.get(f"/v1/agents/{agent.json()['id']}/versions", headers=headers)
    cfg = versions.json()[0]["model_config"]
    assert cfg["provider"] == DEFAULT_MODEL_CONFIG["provider"]
    assert cfg["model"] == DEFAULT_MODEL_CONFIG["model"]
    assert cfg["model"] in _groq_catalog_ids()


@live_only
async def test_live_groq_serves_every_platform_default() -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {settings.groq_api_key}", "User-Agent": "vicero-live-test"},
        )
    assert resp.status_code == 200, resp.text
    live = {m["id"] for m in resp.json()["data"]}
    wanted = {
        "default chat model": DEFAULT_MODEL_CONFIG["model"],
        "summary model": settings.summary_model,
        "L2 injection guard": settings.guard_injection_model,
        "L3 policy guard": settings.guard_policy_model,
    }
    missing = {role: model for role, model in wanted.items() if model not in live}
    assert not missing, f"Groq no longer serves: {missing}. Live list: {sorted(live)}"


@live_only
async def test_live_fresh_agent_answers_without_the_fallback_message(client: AsyncClient) -> None:
    """The literal regression: a new agent, nothing overridden, a real visitor message."""
    headers = await _headers(client, "livedefault@example.com")
    agent = (await client.post("/v1/agents", json={"name": "Live Default"}, headers=headers)).json()
    aid = agent["id"]
    # An unmistakable fallback, so a 404 upstream cannot pass for a real answer.
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "system_prompt": "You are a terse assistant. Reply with one short sentence.",
            "fallback_message": "@@FALLBACK@@",
        },
        headers=headers,
    )
    await client.post(f"/v1/agents/{aid}/versions/1/publish", headers=headers)
    chat = await client.post(
        f"/v1/public/agents/{agent['public_key']}/chat",
        json={"message": "Say hello.", "stream": False, "visitor": {"id": "live-default-model"}},
    )
    assert chat.status_code == 200, chat.text
    content = chat.json()["content"]
    assert content.strip() and "@@FALLBACK@@" not in content, f"agent fell back: {content!r}"
