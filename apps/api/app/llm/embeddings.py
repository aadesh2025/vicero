"""Embedding providers (docs/06 §2). Free-first: Ollama `nomic-embed-text` (dim 768)."""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import ProviderError

log = get_logger("llm.embeddings")

#: Provider names that `registry.build_embedding_provider` sends to Ollama. `openai` and `gemini`
#: are in here because there is no adapter for either — they are accepted and then routed to the
#: local Ollama endpoint anyway.
OLLAMA_ROUTED = ("ollama", "openai", "gemini")

#: Everything `build_embedding_provider` accepts. Anything else raises at knowledge-base creation.
KNOWN_PROVIDERS = (*OLLAMA_ROUTED, "fake")

#: Short on purpose. This runs on the startup path, so a hung or blackholed endpoint must cost a
#: couple of seconds once, not delay the process coming up.
_PROBE_TIMEOUT = 2.0


class OllamaEmbeddingProvider:
    """Calls Ollama's batch embeddings endpoint (`/api/embed`)."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        *,
        base_url: str | None = None,
        dim: int = 768,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.dim = dim
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._transport = transport
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            try:
                resp = await client.post(
                    "/api/embed",
                    json={"model": self.model, "input": texts, "keep_alive": settings.ollama_keep_alive},
                )
            except httpx.HTTPError as exc:
                raise ProviderError(f"embedding network error: {exc}") from exc
            if resp.status_code >= 400:
                raise ProviderError(
                    f"embedding provider returned {resp.status_code}: {resp.text[:200]}",
                    retryable=resp.status_code == 429 or resp.status_code >= 500,
                )
            data = resp.json()
        vectors = data.get("embeddings") or []
        if len(vectors) != len(texts):
            raise ProviderError("embedding provider returned a mismatched vector count")
        return [[float(x) for x in v] for v in vectors]


def _tagged(name: str) -> str:
    """`nomic-embed-text` and `nomic-embed-text:latest` are the same model to Ollama."""
    return name if ":" in name else f"{name}:latest"


def warn_if_misconfigured() -> None:
    """Configuration-only checks, no network. Called from the startup warning block.

    Deliberately separate from `probe_reachable()`: this half is decidable from `Settings` alone
    and is therefore worth saying even when the endpoint happens to be up.
    """
    provider = (settings.embedding_provider or "").strip().lower()
    if provider not in KNOWN_PROVIDERS:
        log.warning(
            "embedding_provider_unknown",
            provider=provider or "(empty)",
            effect="every knowledge base created with the default provider will 400",
            fix=f"set EMBEDDING_PROVIDER to one of {', '.join(KNOWN_PROVIDERS)}",
        )
        return
    if provider in ("openai", "gemini"):
        # Not a typo on the operator's part — `build_embedding_provider` accepts these names and
        # then constructs an `OllamaEmbeddingProvider` regardless, because no adapter for either
        # was ever written. Someone setting EMBEDDING_PROVIDER=openai to escape a missing Ollama
        # would change nothing at all and have no way to tell.
        log.warning(
            "embedding_provider_has_no_adapter",
            provider=provider,
            effect=f"embeddings are served by Ollama at {settings.ollama_base_url}, not by {provider}",
            fix="use EMBEDDING_PROVIDER=ollama for accuracy, or write the adapter",
        )


async def probe_reachable(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[bool, str | None]:
    """Resolve the configured embedding endpoint at startup and say so loudly if it is not there.

    docs/15 PROD-2: production passed `OLLAMA_BASE_URL=http://ollama:11434` while declaring no
    such service, so every embed call failed and every document failed to ingest — with nothing
    in the configuration looking wrong. This is the check that names it. It is the same failure
    family as the `.env`-comment-as-API-key outage (ADR-044) and the `LLM_FORCE_FAKE`-on-8010
    mix-up: **configuration that is correct in dev and silently wrong in production.**

    Returns `(ok, reason)` for the caller's benefit and **never raises**. A diagnostic that can
    stop the API from starting is worse than the bug it reports.

    Two distinct failures, because they need different fixes:

    * the endpoint does not answer  -> the service is missing or the URL is wrong;
    * it answers but does not have the model -> `ollama/ollama` starts EMPTY. It serves an API
      with no models, and embedding against a model it has not pulled is an error rather than a
      download. A port that answers is not evidence that embedding works, which is exactly how a
      "fixed" PROD-2 could still be broken.
    """
    provider = (settings.embedding_provider or "").strip().lower()
    if not settings.embedding_probe_enabled or settings.llm_force_fake:
        return True, None
    if provider not in OLLAMA_ROUTED:
        return True, None

    base_url = settings.ollama_base_url.rstrip("/")
    want = _tagged(settings.embedding_model or "nomic-embed-text")
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=_PROBE_TIMEOUT, transport=transport
        ) as client:
            resp = await client.get("/api/tags")
    except Exception as exc:  # httpx raises a family; a probe must survive all of it
        reason = f"{type(exc).__name__}: {str(exc)[:120]}"
        _report(
            "embedding_provider_unreachable",
            endpoint=base_url,
            error=reason,
            effect="no document can be ingested and RAG retrieval returns nothing",
            fix="deploy an ollama service reachable at OLLAMA_BASE_URL (infra/docker-compose.prod.yml)",
        )
        return False, reason
    if resp.status_code >= 400:
        reason = f"HTTP {resp.status_code}"
        _report(
            "embedding_provider_unhealthy",
            endpoint=base_url,
            status=resp.status_code,
            effect="no document can be ingested and RAG retrieval returns nothing",
        )
        return False, reason
    try:
        available = {_tagged(str(m.get("name") or "")) for m in resp.json().get("models") or []}
    except Exception:
        # A 200 that is not the JSON we expect. Not worth failing over — something is serving on
        # that port, and the embed call itself will produce a precise error.
        return True, None
    if want not in available:
        reason = f"model {want} not pulled"
        _report(
            "embedding_model_missing",
            endpoint=base_url,
            model=want,
            available=sorted(available)[:10],
            effect="every embed call fails; documents will be marked failed",
            fix=f"run `ollama pull {settings.embedding_model}` on the ollama service",
        )
        return False, reason
    log.info("embedding_provider_ready", endpoint=base_url, model=want)
    return True, None


def _report(event: str, **fields: object) -> None:
    """Error level in production, warning elsewhere.

    In dev this is routine — Ollama simply is not running — and an error there trains people to
    ignore it. In production it means the knowledge base does not work.
    """
    if settings.is_prod:
        log.error(event, **fields)
    else:
        log.warning(event, **fields)
