"""Retrieval: vector top-k (+ optional hybrid full-text via RRF) with citations (docs/06 §2)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import stage_timing
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import EmbeddingProvider
from app.models import Chunk
from app.rag import fts, rerank

log = get_logger("rag.retrieval")

def _note_cold(stage: str, t0: float) -> None:
    """A retrieval stage that took seconds is the cold one: say which, so the log answers "why 8 s?"."""
    ms = int((time.perf_counter() - t0) * 1000)
    if ms >= settings.warmup_cold_ms:
        log.warning("rag_stage_cold", stage=stage, ms=ms, threshold_ms=settings.warmup_cold_ms)


# Reciprocal-rank-fusion constant (standard default).
_RRF_K = 60


class Citation(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    ordinal: int
    content: str
    score: float
    metadata: dict[str, Any] = {}


def _to_citation(chunk: Chunk, score: float) -> Citation:
    return Citation(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        knowledge_base_id=chunk.knowledge_base_id,
        ordinal=chunk.ordinal,
        content=chunk.content,
        score=round(score, 6),
        metadata=chunk.meta or {},
    )


async def _vector_hits(
    session: AsyncSession,
    org_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    query_vec: list[float],
    limit: int,
) -> list[tuple[Chunk, float]]:
    distance = Chunk.embedding.cosine_distance(query_vec).label("distance")
    stmt = (
        select(Chunk, distance)
        .where(
            Chunk.organization_id == org_id,
            Chunk.knowledge_base_id.in_(kb_ids),
            Chunk.embedding.is_not(None),
        )
        # `Chunk.id` breaks ties — see `fts_statement` for why an unstable sort is a real bug
        # and not a tidiness point. Ties are rare here (a cosine distance is continuous) but a
        # stable order costs nothing and stops the two retrievers differing in that respect.
        .order_by(distance.asc(), Chunk.id)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    # cosine similarity = 1 - cosine distance
    return [(row[0], 1.0 - float(row[1])) for row in rows]


def fts_statement(
    org_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    query: str,
    limit: int,
    fts_config: str | None = None,
) -> Select[tuple[Chunk, float]]:
    """The keyword-half SELECT. Split out from `_fts_hits` so a test can compile it.

    Whether this statement reaches `ix_chunks_content_fts` is the whole of task P0-1, and a
    test that needs a live session to find out would not have run in CI at all.
    """
    # The regconfig is a LITERAL, never a bind parameter: `ix_chunks_content_fts` is an
    # expression index on `to_tsvector('english', content)`, and the planner can only use it
    # when the expressions match exactly. See `rag/fts.py` for the measured EXPLAIN.
    config = fts.regconfig(fts_config)
    # Heading + content, not content alone — see `fts.SEARCHABLE_SQL` for the measurement that
    # forced it. This expression and the one in migration 0021 must stay character-identical.
    tsvector = fts.searchable(config)
    # ANY of the query's terms, not all of them — see `fts.any_term_tsquery`. With `&`-joined
    # terms this half of hybrid retrieval scored 1 query in 36 and RRF had nothing to fuse.
    # The *text* stays a bind parameter; only the config is a literal.
    tsquery = fts.any_term_tsquery(config, query)
    rank = func.ts_rank(tsvector, tsquery).label("rank")
    return (
        select(Chunk, rank)
        .where(
            Chunk.organization_id == org_id,
            Chunk.knowledge_base_id.in_(kb_ids),
            tsvector.op("@@")(tsquery),
        )
        # `Chunk.id` is a tie-break, and it is load-bearing. `ts_rank` returns a coarse value
        # and the any-term query makes ties the common case: a dozen chunks matching one term
        # once all score identically. With `ORDER BY rank DESC` alone, Postgres returns them in
        # whatever order the scan produces — physical row order — so `LIMIT` keeps a different
        # dozen run to run and **the same question answers differently on the same data**.
        #
        # Measured, not theorised: the eval harness ran the identical `fts` variant four times
        # over an unchanged corpus and scored 0.6247, 0.6247, 0.6220, 0.6397 — a spread of
        # 0.018, wider than every effect docs/14 K2 set out to measure and wider than CI's own
        # 0.02 regression tolerance. The harness described itself as "fully deterministic".
        # Ordering by id makes both the product and the benchmark reproducible.
        .order_by(rank.desc(), Chunk.id)
        .limit(limit)
    )


async def _fts_hits(
    session: AsyncSession,
    org_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    query: str,
    limit: int,
    fts_config: str | None = None,
) -> list[tuple[Chunk, float]]:
    stmt = fts_statement(org_id, kb_ids, query, limit, fts_config)
    rows = (await session.execute(stmt)).all()
    return [(row[0], float(row[1])) for row in rows]


async def search(
    session: AsyncSession,
    org_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    query: str,
    embedder: EmbeddingProvider,
    *,
    top_k: int = 5,
    score_threshold: float = 0.0,
    hybrid: bool = True,
    fts_config: str | None = None,
    fts_weight: float | None = None,
    reranker: rerank.Reranker | None = None,
) -> list[Citation]:
    """Return the top-k most relevant chunks for `query`, filtered by `organization_id`.

    `fts_config` is the Postgres text-search configuration for the keyword half — the owning
    knowledge base's `fts_config`. `None` means English.

    `fts_weight` is the keyword list's weight in the fusion; `None` takes
    `settings.rag_rrf_fts_weight`. See ADR-058 for why it is not 1.0.

    `reranker` is stage 4. `None` or a `NoOpReranker` leaves RRF's ordering untouched, which is
    the default for every deployment — see `rag/rerank.py`. When one is supplied, the candidate
    pool widens to `settings.rerank_candidate_k`, because a reranker's whole value is rescuing a
    correct chunk that RRF ranked below the cut, and it cannot rescue what was never fetched.
    """
    if not kb_ids or not query.strip():
        return []

    reranking = reranker is not None and not isinstance(reranker, rerank.NoOpReranker)
    t0 = time.perf_counter()
    with stage_timing.stage("rag_embed"):
        query_vec = (await embedder.embed([query]))[0]
    _note_cold("embed", t0)
    fetch = max(settings.rerank_candidate_k, top_k) if reranking else max(top_k * 4, top_k)
    t0 = time.perf_counter()
    with stage_timing.stage("rag_vector"):
        vector_hits = await _vector_hits(session, org_id, kb_ids, query_vec, fetch)
    _note_cold("vector", t0)
    vector_hits = [(c, s) for c, s in vector_hits if s >= score_threshold]

    if not hybrid:
        return await _finish(reranker, query, list(vector_hits), top_k)

    t0 = time.perf_counter()
    with stage_timing.stage("rag_fts"):
        fts_hits = await _fts_hits(session, org_id, kb_ids, query, fetch, fts_config)
    _note_cold("fts", t0)
    weight = settings.rag_rrf_fts_weight if fts_weight is None else fts_weight

    # WEIGHTED reciprocal rank fusion. Textbook RRF weights each list equally, which assumes
    # the retrievers are comparable; measured on the eval corpus they are not (dense NDCG@10
    # 0.898, keyword 0.660), and at equal weight the keyword list dragged the fused result to
    # 0.816 — below dense on its own. `settings.rag_rrf_fts_weight` and ADR-058 carry the
    # sweep and, more importantly, what it does not establish.
    fused: dict[uuid.UUID, float] = {}
    chunks: dict[uuid.UUID, Chunk] = {}
    sims: dict[uuid.UUID, float] = {}
    for rank, (chunk, sim) in enumerate(vector_hits):
        fused[chunk.id] = fused.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
        chunks[chunk.id] = chunk
        sims[chunk.id] = sim
    for rank, (chunk, _score) in enumerate(fts_hits):
        fused[chunk.id] = fused.get(chunk.id, 0.0) + weight / (_RRF_K + rank + 1)
        chunks.setdefault(chunk.id, chunk)
        sims.setdefault(chunk.id, 0.0)

    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    candidates = [(chunks[cid], sims[cid]) for cid, _ in ordered]
    return await _finish(reranker, query, candidates, top_k)


async def _finish(
    reranker: rerank.Reranker | None,
    query: str,
    candidates: list[tuple[Chunk, float]],
    top_k: int,
) -> list[Citation]:
    """Stage 4, then the top-k slice.

    `apply()` returns `None` when reranking did not run — disabled, no-op, or failed open — and
    that is deliberately distinct from "ran and changed nothing": the caller must fall back to
    the ordering it already had rather than to an empty list. A reranker outage degrades the
    ranking; it must never be able to make a client's agent answer with no context at all.
    """
    order = None
    if reranker is not None:
        order = await rerank.apply(reranker, query, [c.content for c, _ in candidates])
    if order is not None:
        candidates = [candidates[i] for i in order]
    return [_to_citation(c, s) for c, s in candidates[:top_k]]
