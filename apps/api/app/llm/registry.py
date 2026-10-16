"""Provider catalog, factory, credential resolution, and the fallback runner."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm import catalog
from app.llm.anthropic import AnthropicProvider
from app.llm.base import ChatProvider, EmbeddingProvider, ProviderError
from app.llm.embeddings import OllamaEmbeddingProvider
from app.llm.fake import FakeChatProvider, FakeEmbeddingProvider
from app.llm.fallback import build_fallback_chain
from app.llm.gemini import GeminiProvider
from app.llm.openai_compatible import (
    CustomProvider,
    GroqProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    OpenRouterProvider,
)
from app.llm.types import ChatRequest, ChatResponse
from app.models import ProviderCredential

log = get_logger("llm.registry")

# (session, org_id, provider_name, *, agent_id) -> ChatProvider
ProviderResolver = Callable[..., Awaitable["ChatProvider"]]

# provider -> catalog metadata. `requires_key`: needs an API key to work at all.
# Derived from `llm/catalog.py`, which is the source of truth (provider list, model lists,
# endpoints, pricing). This name and shape are kept for the existing callers.
PROVIDER_CATALOG: dict[str, dict[str, Any]] = catalog.legacy_catalog()

# Providers with a platform-wide key in the environment. The catalog's other providers are
# bring-your-own-key only: an org supplies one under Settings → Provider keys, which is stored
# encrypted per-org rather than shared across every tenant on the deployment.
_ENV_KEY = {
    "groq": "groq_api_key",
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}


def env_key_for(provider: str) -> str | None:
    """The platform env key for a provider, or None when unset.

    Blank/whitespace-only values read as unset — `.env.example` ships placeholder lines and a
    comment-only value already cost this project a live outage (ADR-044).
    """
    attr = _ENV_KEY.get(provider)
    if attr is None:
        return None
    value = getattr(settings, attr, None)
    if isinstance(value, str) and not value.strip():
        return None
    return value


def build_chat_provider(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatProvider:
    if provider == "groq":
        return GroqProvider(api_key, transport=transport)
    if provider == "gemini":
        return GeminiProvider(api_key, transport=transport)
    if provider == "ollama":
        return OllamaProvider(api_key, base_url=base_url, transport=transport)
    if provider == "openrouter":
        return OpenRouterProvider(api_key, transport=transport)
    if provider == "openai":
        return OpenAIProvider(api_key, transport=transport)
    if provider == "anthropic":
        return AnthropicProvider(api_key, transport=transport)
    if provider == "custom":
        if not base_url:
            raise AppError("llm.custom_base_url_required", "A base_url is required for custom providers.", 400)
        return CustomProvider(base_url, api_key, transport=transport)
    if provider == "fake":
        return FakeChatProvider()
    # Providers that speak the OpenAI wire format at a fixed endpoint need no adapter of their
    # own — the catalog entry carries the base URL. A stored credential may still override it
    # (a regional endpoint or a proxy in front of the vendor).
    spec = catalog.get_provider(provider)
    if spec is not None and spec.kind == "openai_compatible":
        url = base_url or spec.base_url
        if not url:
            raise AppError(
                "llm.custom_base_url_required", f"A base_url is required for '{provider}'.", 400
            )
        # A stored override (regional endpoint, proxy) is tenant-supplied; the catalog URL is not.
        return OpenAICompatibleProvider(
            url, api_key, name=provider, transport=transport, guard_destination=bool(base_url)
        )
    raise AppError("llm.unknown_provider", f"Unknown provider '{provider}'.", 400)


async def resolve_credential(
    session: AsyncSession, org_id: uuid.UUID, provider: str, *, agent_id: uuid.UUID | None = None
) -> tuple[str | None, str | None]:
    """Key lookup order: agent-scoped credential → org default → platform env key."""
    stmt = select(ProviderCredential).where(
        ProviderCredential.organization_id == org_id, ProviderCredential.provider == provider
    )
    creds = list((await session.execute(stmt)).scalars().all())

    def _pick() -> ProviderCredential | None:
        if agent_id is not None:
            for c in creds:
                if c.agent_id == agent_id:
                    return c
        for c in creds:
            if c.is_default and c.agent_id is None:
                return c
        for c in creds:
            if c.agent_id is None:
                return c
        return None

    chosen = _pick()
    if chosen is not None:
        api_key = decrypt(chosen.api_key_enc) if chosen.api_key_enc else None
        return api_key, chosen.base_url

    return env_key_for(provider), None


async def get_chat_provider(
    session: AsyncSession,
    org_id: uuid.UUID,
    provider: str,
    *,
    agent_id: uuid.UUID | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatProvider:
    # E2E/CI override: run every agent on the deterministic Fake provider (no keys).
    if settings.llm_force_fake:
        return FakeChatProvider()
    # An agent deliberately configured for `fake` (the test suites do this) resolves here,
    # before the key check: `fake` isn't in PROVIDER_CATALOG, so it would otherwise fall into
    # the requires-a-key branch and raise. That went unnoticed while callers caught the error
    # and substituted the fake provider themselves — which is exactly the behaviour that let a
    # missing *real* key answer customers with `echo:`.
    if provider == "fake":
        return FakeChatProvider()
    api_key, base_url = await resolve_credential(session, org_id, provider, agent_id=agent_id)
    meta = PROVIDER_CATALOG.get(provider, {})
    if meta.get("requires_key", True) and not api_key:
        raise AppError(
            "llm.provider_unavailable",
            f"No API key configured for '{provider}'. Add one under provider credentials.",
            503,
        )
    return build_chat_provider(provider, api_key=api_key, base_url=base_url, transport=transport)


def build_embedding_provider(
    provider: str,
    model: str,
    *,
    dim: int = 768,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmbeddingProvider:
    """Resolve an embedding provider by name. `fake` is used by tests (matches the KB dim)."""
    # E2E/CI override: deterministic keyless embeddings for the whole pipeline.
    if settings.llm_force_fake:
        return FakeEmbeddingProvider(dim=dim)
    if provider in ("ollama", "openai", "gemini"):
        # All non-fake providers currently route through Ollama's local endpoint (free-first).
        # Real OpenAI/Gemini embedding adapters can be added later behind this same factory.
        return OllamaEmbeddingProvider(model or "nomic-embed-text", dim=dim, transport=transport)
    if provider == "fake":
        return FakeEmbeddingProvider(dim=dim)
    raise AppError("kb.unknown_embedding_provider", f"Unknown embedding provider '{provider}'.", 400)


async def run_with_fallback(providers: list[ChatProvider], req: ChatRequest) -> ChatResponse:
    """Try each provider in order; on a provider failure, fall back to the next."""
    if not providers:
        raise AppError("llm.provider_unavailable", "No providers available.", 503)
    last_error: Exception | None = None
    for provider in providers:
        try:
            return await provider.chat(req)
        except ProviderError as exc:
            last_error = exc
    raise AppError("llm.provider_unavailable", f"All providers failed: {last_error}", 502)


async def get_chat_provider_chain(
    session: AsyncSession,
    org_id: uuid.UUID,
    model_config: dict[str, Any],
    *,
    agent_id: uuid.UUID | None = None,
    resolve: ProviderResolver | None = None,
) -> ChatProvider:
    """Resolve an agent's provider plus its configured fallback chain (NFR-4).

    `model_config.fallbacks` is a list of `{"provider": ..., "model": ...}`. A fallback whose
    credentials are missing is skipped rather than failing the turn — an unusable link should
    not take down a working primary.

    `resolve` lets a caller supply its own provider lookup; callers pass their module-level
    `get_chat_provider` so a test that stubs that name keeps controlling what gets built.
    """
    lookup = resolve or get_chat_provider
    primary_name = str(model_config.get("provider") or "fake")
    primary = await lookup(session, org_id, primary_name, agent_id=agent_id)

    chain: list[tuple[ChatProvider, str | None]] = []
    for entry in model_config.get("fallbacks") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("provider") or "").strip()
        model = str(entry.get("model") or "") or None
        # The same provider is a valid fallback when it names a DIFFERENT model: Groq rate-limits per model, so
        # `gpt-oss-120b` at its tokens-per-minute cap can fall back to `llama-3.3-70b-versatile` (ADR-123).
        if not name or (name == primary_name and (model is None or model == str(model_config.get("model") or ""))):
            continue
        try:
            provider = await lookup(session, org_id, name, agent_id=agent_id)
        except AppError as exc:
            log.warning("llm_fallback_unavailable", provider=name, error=str(exc))
            continue
        chain.append((provider, model))

    return build_fallback_chain(primary, str(model_config.get("model") or "") or None, chain)
