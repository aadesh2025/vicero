"""Stage 4 — cross-encoder reranking over the fused candidates (docs/13 R1, docs/14 K4).

Vicero has stages 1-3: Postgres FTS, pgvector dense, and RRF over the two. The missing stage
is the cross-encoder. A bi-encoder embeds the query and the document **separately** and compares
two vectors that never met; a cross-encoder feeds both into one model with joint attention, so
it can answer "does this passage actually answer this question?" in a way two independent
vectors structurally cannot. Much more accurate, much slower — hence running it on ~50 fused
candidates rather than on the corpus.

**Why it earns its place here specifically.** Vicero's worst live failure mode is fabrication
when retrieval misses and no context block is appended at all (2026-08-02: score 0.0318 against
a 0.35 threshold, and the model invented opening hours). docs/11 §9 records grounding as the
weakest link at 12/15 fabricated with **no safety phase A-G touching it**. This attacks that
from the retrieval side, which is the side the root cause is on. CLAUDE.md §10a's own rule
applies: a prompt line is not enforcement.

**Off by default, everywhere.** Platform-wide (`RERANK_ENABLED`), and per agent
(`rag_config.rerank`). NFR-1 is p50 417 ms to first token and this call happens *before*
generation starts, so it is a per-turn latency cost on the critical path — which is a decision
for whoever owns the latency budget, not a default somebody inherits.

**Fails open, loudly** (the ADR-051 rule every docs/11 layer follows). A rerank outage degrades
to RRF ordering. Never an error, never an empty result set: an unavailable reranker must not be
able to take a client's chat down, and the alternative — serving nothing — is strictly worse
than serving the order we already had.

**The key is the platform's, never the org's** (ADR-055, restated as ADR-063). Guard models
learned this the hard way: `resolve_credential()`'s agent -> org -> env chain falls back to the
env key *last*, so an org running its agent on Mistral or DeepSeek would have had its own key
resolved against a rerank endpoint. Rerank is platform infrastructure — its spend, its
credential, its own metrics bucket, never folded into `TurnResult`.

**Self-hosted by default** (docs/14 §5.3). `BAAI/bge-reranker-v2-m3` is ~568 MB, CPU-viable, and
speaks the same `/rerank` shape as a hosted API — so the transport here works against either. It
is the recommended one for two reasons that are not about accuracy: client knowledge-base text
never leaves the deployment, which is a residency and contractual matter rather than a technical
one; and it is trained multilingual, which matters in a market where docs/11 §9.2a records Tamil
as a first language.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core import metrics
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("rag.rerank")


@runtime_checkable
class Reranker(Protocol):
    """Reorder `documents` for `query`.

    Returns `(index, score)` pairs, highest first, indexing into the input list. Indices rather
    than reordered documents so the caller keeps whatever it had attached to each candidate —
    here, the `Chunk` and its similarity — without this module needing to know about any of it.
    """

    name: str

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]: ...


class NoOpReranker:
    """The default. Hands back the input order untouched.

    Not a stub to be replaced later — it is the permanent behaviour for every deployment that
    has not turned reranking on, and the thing a failure degrades to. A test asserts retrieval
    is byte-identical with it in place, which is what makes enabling the feature a decision
    rather than a side effect.
    """

    name = "noop"

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        return [(i, 0.0) for i in range(len(documents))]


class HttpCrossEncoderReranker:
    """Talks to a `/rerank` endpoint: text-embeddings-inference, or a hosted API of that shape.

    The wire format is the one both speak — `{query, texts}` in, a list of `{index, score}` out
    — so pointing `RERANK_ENDPOINT` at a self-hosted TEI container or at a vendor is a config
    change. Responses are parsed defensively because these two disagree on the response key
    (`texts` vs `documents`, `index` vs `document.index`), and a shape surprise must degrade to
    RRF order rather than raise on a visitor's turn.
    """

    name = "cross-encoder"

    def __init__(
        self,
        endpoint: str,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_ms: int = 800,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_ms / 1000.0
        self._transport = transport

    def _payload(self, query: str, documents: list[str]) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "texts": documents}
        if self.model:
            # TEI serves one model and ignores this; a hosted API requires it.
            body["model"] = self.model
        return body

    @staticmethod
    def _parse(body: Any, count: int) -> list[tuple[int, float]]:
        rows = body.get("results") if isinstance(body, dict) else body
        if not isinstance(rows, list):
            raise ValueError(f"rerank response was not a list: {type(body).__name__}")
        parsed: list[tuple[int, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("rerank result row was not an object")
            index = row.get("index")
            if index is None and isinstance(row.get("document"), dict):
                index = row["document"].get("index")
            score = row.get("score", row.get("relevance_score"))
            if index is None or score is None:
                raise ValueError(f"rerank result row missing index/score: {sorted(row)}")
            position = int(index)
            # A model that returns an out-of-range index would silently reorder the caller's
            # list into something that does not correspond to the query at all.
            if not 0 <= position < count:
                raise ValueError(f"rerank returned index {position} for {count} document(s)")
            parsed.append((position, float(score)))
        return sorted(parsed, key=lambda pair: pair[1], reverse=True)

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        if not documents:
            return []
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(
                f"{self.endpoint}/rerank", json=self._payload(query, documents), headers=headers
            )
            resp.raise_for_status()
            result = self._parse(resp.json(), len(documents))
        metrics.observe_rerank_call("scored", (time.perf_counter() - started) * 1000.0)
        return result


def platform_rerank_key() -> str | None:
    """The platform's own rerank credential — deliberately **not** `resolve_credential()`.

    Same reasoning as `chat.guard_models.platform_guard_key()`: the agent -> org -> env chain
    would resolve a client's *own* provider key against the rerank endpoint for any org that
    holds one, and because this layer fails open, nothing would say so. A self-hosted TEI
    container needs no key at all, which is the expected deployment.
    """
    key = (settings.rerank_api_key or "").strip()
    return key or None


def is_available() -> bool:
    """Whether reranking can actually run, platform-wide."""
    return bool(settings.rerank_enabled and settings.rerank_endpoint.strip())


def unavailable_reason() -> str | None:
    """Why reranking is not running, for the admin console. `None` when healthy."""
    if not settings.rerank_enabled:
        return "disabled by configuration (RERANK_ENABLED=false)"
    if not settings.rerank_endpoint.strip():
        return "RERANK_ENABLED is on but RERANK_ENDPOINT is empty - no reranker to call"
    return None


def warn_if_misconfigured() -> None:
    """Startup check. Enabled-but-unreachable is the state that must not be quiet.

    Disabled is a decision and says nothing. Enabled with no endpoint is a *broken* decision:
    every agent with `rag_config.rerank` on silently gets RRF ordering, which looks exactly like
    a reranker that is running and finding nothing to change. That is the Phase C lesson.
    """
    if not settings.rerank_enabled:
        return
    reason = unavailable_reason()
    if reason is None:
        return
    (log.error if settings.env == "prod" else log.warning)(
        "rerank_misconfigured",
        reason=reason,
        impact="agents with reranking enabled are silently falling back to RRF ordering",
    )


def build_reranker(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> Reranker:
    """The configured reranker, or the no-op. Never raises."""
    if not is_available():
        return NoOpReranker()
    return HttpCrossEncoderReranker(
        settings.rerank_endpoint.strip(),
        model=settings.rerank_model.strip() or None,
        api_key=platform_rerank_key(),
        timeout_ms=settings.rerank_timeout_ms,
        transport=transport,
    )


async def apply(
    reranker: Reranker, query: str, documents: list[str]
) -> list[int] | None:
    """Ranked indices, or `None` when reranking did not happen.

    `None` means **the stage did not run** — not "no change". The distinction is the same one
    `score_injection()` draws: a caller that treats a failure as a verdict is the bug.
    """
    if isinstance(reranker, NoOpReranker) or not documents:
        return None
    started = time.perf_counter()
    try:
        ranked = await reranker.rerank(query, documents)
    except Exception as exc:
        # FAIL OPEN. A rerank outage degrades to RRF ordering; it does not take chat down, and
        # it never returns an empty result set. Loud, because a silent fallback is
        # indistinguishable from a reranker that agreed with the existing order.
        metrics.observe_rerank_call("error", (time.perf_counter() - started) * 1000.0)
        log.warning(
            "rerank_unavailable",
            error=str(exc)[:200],
            endpoint=settings.rerank_endpoint,
            impact="this turn was ranked by RRF alone",
        )
        return None
    return [index for index, _score in ranked]
