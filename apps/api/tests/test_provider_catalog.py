"""The provider catalog, per-org key status, and live model discovery.

These cover the contract the Settings key manager and the builder's Model tab both depend on:
a provider is offered for selection only when this org can actually run it, and the model list
comes from the provider itself whenever a key exists.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.errors import AppError
from app.llm import catalog
from app.llm.base import ProviderError
from app.llm.fake import FakeChatProvider
from app.llm.pricing import FREE_PROVIDERS, price_for
from app.llm.registry import build_chat_provider


async def _org_headers(client: AsyncClient, email: str = "cat@example.com") -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "Acme"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


@pytest.fixture(autouse=True)
def _no_ambient_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank every platform env key.

    Without this the assertions depend on whichever keys happen to be in the developer's
    `.env` — this machine has a real `GROQ_API_KEY`, so "an unkeyed provider reports
    `none`" would pass here and fail in CI, or vice versa.
    """
    for attr in ("groq_api_key", "gemini_api_key", "openrouter_api_key", "openai_api_key", "anthropic_api_key"):
        monkeypatch.setattr(settings, attr, "", raising=False)


# ── catalog integrity ──────────────────────────────────────────────────────────────


def test_every_catalog_provider_is_buildable() -> None:
    """A provider offered in the UI must be constructible, or selecting it 500s at turn time."""
    for spec in catalog.PROVIDERS:
        if spec.base_url_required:
            with pytest.raises(AppError) as exc:
                build_chat_provider(spec.name, api_key="k")
            assert exc.value.code == "llm.custom_base_url_required"
            assert build_chat_provider(spec.name, api_key="k", base_url="http://x/v1") is not None
        else:
            assert build_chat_provider(spec.name, api_key="k") is not None


def test_model_ids_are_unique_within_a_provider() -> None:
    for spec in catalog.PROVIDERS:
        ids = [m.id for m in spec.models]
        assert len(ids) == len(set(ids)), f"duplicate model id in {spec.name}"


def test_openai_compatible_providers_declare_an_endpoint() -> None:
    for spec in catalog.PROVIDERS:
        if spec.kind == "openai_compatible" and not spec.base_url_required:
            assert spec.base_url, f"{spec.name} has no base_url and does not ask for one"


def test_a_paid_model_never_silently_prices_at_zero() -> None:
    """Either we publish a rate, or `pricing_known` is False so the UI can say so.

    A paid model reporting $0 is a wrong number in a client's cost report, which is worse
    than an absent one.
    """
    for spec in catalog.PROVIDERS:
        if spec.free:
            continue
        for model in spec.models:
            rate = price_for(spec.name, model.id)
            known = model.prompt_micros is not None and model.completion_micros is not None
            assert known == (rate != (0, 0)), f"{spec.name}/{model.id} prices at 0 but claims to be known"


def test_every_paid_provider_publishes_a_rate() -> None:
    """The other direction of the test above, and the one that actually bites.

    The test above passes when a paid model has *no* rate, because absent and zero agree.
    This one says the catalogue must not be in that state: an operator who switches an agent
    onto a paid provider has to see the cost change, and `None` there reads as $0 in the
    dashboard — indistinguishable from the free tier they just left.
    """
    missing = [
        f"{spec.name}/{model.id}"
        for spec in catalog.PROVIDERS
        if not spec.free
        for model in spec.models
        if model.prompt_micros is None or model.completion_micros is None
    ]
    assert not missing, f"paid models with no published rate: {missing}"


def test_a_paid_model_on_a_free_tier_provider_is_still_billed() -> None:
    """The provider `free` flag is a default, not a guarantee.

    OpenRouter is free-tier-first but routes some ids to billed upstreams. Consulting the
    flag before the model rate made every one of them cost $0, so moving an agent from
    `:free` to Claude showed no cost change. The per-model rate has to win.
    """
    assert "openrouter" in FREE_PROVIDERS
    assert price_for("openrouter", "meta-llama/llama-3.3-70b-instruct:free") == (0, 0)
    assert price_for("openrouter", "anthropic/claude-sonnet-4.5") == (3000, 15000)


def test_free_providers_are_derived_from_the_catalog() -> None:
    assert {"groq", "gemini", "ollama", "openrouter", "fake"} <= FREE_PROVIDERS
    assert "openai" not in FREE_PROVIDERS and "anthropic" not in FREE_PROVIDERS


def test_legacy_catalog_shape_is_preserved() -> None:
    """`PROVIDER_CATALOG` is consumed by `get_chat_provider`'s requires-a-key branch."""
    legacy = catalog.legacy_catalog()
    assert set(legacy["groq"]) == {"label", "free", "requires_key", "models"}
    assert legacy["groq"]["requires_key"] is True
    assert legacy["ollama"]["requires_key"] is False


# ── per-org key status ─────────────────────────────────────────────────────────────


async def test_providers_report_whether_this_org_can_run_them(client: AsyncClient) -> None:
    headers = await _org_headers(client)
    resp = await client.get("/v1/credentials/providers", headers=headers)
    assert resp.status_code == 200
    by_name = {p["name"]: p for p in resp.json()}

    # Every catalog provider is listed, whether or not it is usable.
    assert set(by_name) == set(catalog.PROVIDER_NAMES)
    # No key anywhere → not selectable.
    assert by_name["openai"]["configured"] is False
    assert by_name["openai"]["key_source"] == "none"
    # Local, needs no key at all.
    assert by_name["ollama"]["configured"] is True
    assert by_name["ollama"]["key_source"] == "not_required"
    # Needs an endpoint we cannot guess, so it is not usable until configured.
    assert by_name["custom"]["configured"] is False
    # Catalog metadata rides along for the picker.
    assert by_name["groq"]["available_models"][0]["id"] == "openai/gpt-oss-120b"
    assert by_name["openai"]["api_key_url"]


async def test_a_platform_env_key_counts_as_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The platform's own agents run on the env key with no credential row — if `configured`
    ignored it, the builder would hide the provider those agents are already using."""
    headers = await _org_headers(client)
    monkeypatch.setattr(settings, "groq_api_key", "gsk_env_key_value")
    resp = await client.get("/v1/credentials/providers", headers=headers)
    groq = next(p for p in resp.json() if p["name"] == "groq")
    assert groq["configured"] is True
    assert groq["key_source"] == "env"
    assert groq["masked_key"] is None  # not ours to show; it isn't stored per-org


async def test_a_stored_key_takes_precedence_over_the_env(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _org_headers(client)
    monkeypatch.setattr(settings, "groq_api_key", "gsk_env_key_value")
    await client.put("/v1/credentials/providers/groq", json={"api_key": "gsk_org_key_9876"}, headers=headers)

    groq = next(
        p for p in (await client.get("/v1/credentials/providers", headers=headers)).json()
        if p["name"] == "groq"
    )
    assert groq["key_source"] == "org"
    assert groq["masked_key"].endswith("9876")


# ── one key per provider (the Settings page's model) ───────────────────────────────


async def test_saving_a_key_twice_replaces_it_rather_than_stacking_rows(client: AsyncClient) -> None:
    headers = await _org_headers(client)
    first = await client.put(
        "/v1/credentials/providers/openai", json={"api_key": "sk-first-key-1111"}, headers=headers
    )
    assert first.status_code == 200
    second = await client.put(
        "/v1/credentials/providers/openai", json={"api_key": "sk-second-key-2222"}, headers=headers
    )
    assert second.status_code == 200

    listed = (await client.get("/v1/credentials", headers=headers)).json()
    openai_rows = [c for c in listed if c["provider"] == "openai"]
    assert len(openai_rows) == 1, "re-saving a provider key must not leave two rows to arbitrate"
    assert openai_rows[0]["masked_key"].endswith("2222")


async def test_resaving_without_a_key_keeps_the_stored_one(client: AsyncClient) -> None:
    """The form renders a masked value; submitting it unchanged must not wipe a working key."""
    headers = await _org_headers(client)
    await client.put("/v1/credentials/providers/openai", json={"api_key": "sk-keep-me-3333"}, headers=headers)
    resp = await client.put("/v1/credentials/providers/openai", json={"label": "Prod"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["masked_key"].endswith("3333")
    assert resp.json()["label"] == "Prod"


async def test_a_provider_that_needs_a_key_refuses_an_empty_one(client: AsyncClient) -> None:
    headers = await _org_headers(client)
    resp = await client.put("/v1/credentials/providers/openai", json={"api_key": "  "}, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "credentials.api_key_required"


async def test_custom_endpoint_requires_a_base_url(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A local endpoint (LM Studio, vLLM) is only accepted once the operator has named it (S-03).
    monkeypatch.setattr(settings, "provider_private_hosts", "localhost")
    headers = await _org_headers(client)
    resp = await client.put("/v1/credentials/providers/custom", json={"api_key": "k"}, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "credentials.base_url_required"

    ok = await client.put(
        "/v1/credentials/providers/custom",
        json={"api_key": "k", "base_url": "http://localhost:8080/v1"},
        headers=headers,
    )
    assert ok.status_code == 200


async def test_unknown_provider_is_rejected(client: AsyncClient) -> None:
    headers = await _org_headers(client)
    resp = await client.put("/v1/credentials/providers/notreal", json={"api_key": "k"}, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "credentials.unknown_provider"


async def test_removing_a_provider_key_makes_it_unconfigured_again(client: AsyncClient) -> None:
    headers = await _org_headers(client)
    await client.put("/v1/credentials/providers/openai", json={"api_key": "sk-bye-4444"}, headers=headers)
    deleted = await client.delete("/v1/credentials/providers/openai", headers=headers)
    assert deleted.status_code == 204

    openai = next(
        p for p in (await client.get("/v1/credentials/providers", headers=headers)).json()
        if p["name"] == "openai"
    )
    assert openai["configured"] is False


# ── model discovery ────────────────────────────────────────────────────────────────


async def test_models_come_from_the_provider_when_a_key_exists(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.credentials import service

    headers = await _org_headers(client)
    await client.put("/v1/credentials/providers/openai", json={"api_key": "sk-x-5555"}, headers=headers)
    monkeypatch.setattr(service, "build_chat_provider", lambda *a, **k: FakeChatProvider())

    resp = await client.get("/v1/credentials/providers/openai/models", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "live"
    assert [m["id"] for m in body["models"]] == ["fake-1"]


async def test_discovery_falls_back_to_the_catalog_instead_of_failing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable provider still has to render a usable dropdown."""
    from app.modules.credentials import service

    headers = await _org_headers(client)
    await client.put("/v1/credentials/providers/openai", json={"api_key": "sk-x-6666"}, headers=headers)

    def _boom(*_a: object, **_k: object) -> FakeChatProvider:
        raise ProviderError("network error: connection refused")

    monkeypatch.setattr(service, "build_chat_provider", _boom)
    resp = await client.get("/v1/credentials/providers/openai/models", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "catalog"
    assert "connection refused" in body["error"]
    assert "gpt-4o" in [m["id"] for m in body["models"]]


async def test_discovery_without_a_key_reports_the_catalog(client: AsyncClient) -> None:
    headers = await _org_headers(client)
    resp = await client.get("/v1/credentials/providers/anthropic/models", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["source"] == "catalog"
    assert resp.json()["error"] == "No API key configured."


async def test_a_rejected_key_is_not_echoed_back_in_the_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Providers quote the rejected key in their 401 body — OpenAI returns
    `Incorrect API key provided: sk-live-*******8888`. Masked or not, that is secret material
    in an API response and a log line."""
    from app.modules.credentials import service

    headers = await _org_headers(client)
    await client.put("/v1/credentials/providers/openai", json={"api_key": "sk-tail-9999"}, headers=headers)

    def _rejected(*_a: object, **_k: object) -> FakeChatProvider:
        raise ProviderError(
            'provider returned 401: {"error": {"message": "Incorrect API key provided: '
            'sk-live-*******9999. You can find your API key at ..."}}'
        )

    monkeypatch.setattr(service, "build_chat_provider", _rejected)
    resp = await client.get("/v1/credentials/providers/openai/models", headers=headers)
    error = resp.json()["error"]

    assert "401" in error, "the operator still needs to know the key was rejected"
    assert "sk-live" not in error and "9999" not in error
    assert "[key]" in error


async def test_non_chat_models_are_filtered_out_of_discovery() -> None:
    """A provider returns one flat list for every modality; embeddings are not agent models."""
    from app.modules.credentials.service import _is_chat_model

    assert _is_chat_model("gpt-4o")
    assert not _is_chat_model("text-embedding-3-small")
    assert not _is_chat_model("whisper-1")
    assert not _is_chat_model("meta-llama/llama-prompt-guard-2-86m")
