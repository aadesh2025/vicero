#!/usr/bin/env python
"""Measure retrieval quality against the frozen eval set (docs/13 R2, docs/14 P0-2).

    make eval-retrieval                 # keyword half only - deterministic, no model needed
    make eval-retrieval-full            # dense + hybrid too, needs a real embedding provider
    cd apps/api && ./.venv/Scripts/python.exe ../../scripts/eval_retrieval.py --help

**Why this is the highest-leverage thing in docs/13.** Every retrieval constant in this repo is
a guess. `score_threshold` moved 0.7 -> 0.35 on 2026-07-21 with no number attached; `top_k=5`
and `chunk_size=1000` have never been measured at all. docs/11 §9 records grounding as the
weakest link, and the 2026-08-02 incident showed the mechanism was *retrieval legitimately
missing* rather than a bad prompt. Until this script exists, the reranker, per-KB FTS language
and Docling chunking are all changes nobody can prove helped.

**It runs the real pipeline, not a model of it.** The corpus is ingested through
`ingest_document` — the same loader, chunker and embedder a client's upload goes through — into
a scratch organisation that is deleted afterwards. So a chunking change shows up here, which is
the point: docs/14 §3.4's re-chunk feedback loop is only usable if something scores the result.

**Two run modes, and the difference matters.**

* `--variants fts` uses the fake embedder. The keyword half of hybrid retrieval does not touch
  embeddings at all, so this is deterministic, needs no model, and is a real signal: it would
  have caught the P0-1 index regression, and it catches chunking and `fts_config` regressions.
  This is the mode CI can run.

  **It was not deterministic until 2026-08-17, and this paragraph used to say it was.** Four
  consecutive runs over an unchanged corpus scored 0.6247, 0.6247, 0.6220 and 0.6397 — a spread
  of 0.018, wider than every effect docs/14 K2 set out to measure and wider than the 0.02
  tolerance that fails CI. The cause was in the product, not the harness: `fts_statement`
  ordered by `ts_rank` alone, and tied ranks came back in physical row order. See the tie-break
  comment in `rag/retrieval.py`. **A benchmark that has never been run twice on the same input
  has not been checked for reproducibility.**
* `--variants dense,hybrid` needs a genuine embedding provider (Ollama `nomic-embed-text` by
  default - local and free). These are the numbers that matter for the product, and they cannot
  be faked: `FakeEmbeddingProvider` hashes text, so a "dense" score under it measures a hash
  function.

Baselines are keyed by embedder for exactly that reason - a fake-embedder number can never be
compared against a real one by accident.

Exits **1** on a regression beyond `--tolerance`, so it can gate a PR. An improvement never
fails, and never silently becomes the new baseline either: re-baselining is `--save-baseline`,
so every number in the repo is one somebody chose to record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.llm.registry import build_embedding_provider
from app.models import Document, KnowledgeBase, Organization
from app.rag import retrieval
from app.rag.evaluate import (
    EVAL_SET_DIR,
    EvalSet,
    VariantResult,
    compare,
    load_eval_set,
    score_variant,
)
from app.rag.ingest import ingest_document

# A fixed slug so an interrupted run is cleaned up by the next one rather than accumulating
# scratch tenants. Deliberately unmistakable - nobody names a client this.
SCRATCH_SLUG = "vicero-retrieval-eval-scratch"
BASELINE_PATH = EVAL_SET_DIR / "baseline.json"

# `hybrid` uses the configured RRF weight; `hybrid@<w>` overrides it, so the weight can be
# swept without editing settings. Only the plain names carry a committed baseline — a sweep is
# an experiment, not a number to defend.
VARIANTS = ("fts", "dense", "hybrid")
# Retrieve 10 so NDCG@10 has 10 ranks to score. Recall is reported at 5 to match the production
# `top_k`, so that column answers "would the model actually have been given this?".
RETRIEVE_K = 10
RECALL_K = 5


def _embedder_key(provider: str, model: str) -> str:
    return "fake" if provider == "fake" else f"{provider}:{model}"


async def _reset_scratch(session: AsyncSession) -> None:
    """Drop any previous scratch org. Cascades to its KBs, documents and chunks."""
    org = (
        await session.execute(select(Organization).where(Organization.slug == SCRATCH_SLUG))
    ).scalar_one_or_none()
    if org is not None:
        await session.execute(delete(Organization).where(Organization.id == org.id))
        await session.commit()


def _as_docling_json(title: str, text: str) -> tuple[str, dict[str, Any]]:
    """Build a `DoclingDocument` whose heading is *structure*, not a line of body text.

    This is what makes K2 measurable at all. On the legacy path the heading is simply the first
    line of the chunk, so `contextualize()` has nothing to add and the two paths embed identical
    strings. Real extraction is not like that: Docling emits the heading as a node, every chunk
    under it inherits the heading path, and the *second* chunk of a section — which on the legacy
    path carries no heading at all — is the one W2 is about.

    Returns `(markdown, docling_json)`, the two fields docling-serve returns.
    """
    from docling_core.types.doc.document import DoclingDocument
    from docling_core.types.doc.labels import DocItemLabel

    lines = [ln.strip() for ln in text.strip().splitlines()]
    # The corpus writes each document's heading as its own first line. Lift it out, so the body
    # below it does not repeat it — otherwise this measures nothing.
    heading = lines[0] if lines else title
    body = "\n".join(lines[1:]).strip() or text.strip()

    doc = DoclingDocument(name=title or heading)
    top = doc.add_heading(text=heading, level=1)
    parent = top
    for block in [p.strip() for p in body.split("\n\n") if p.strip()]:
        # `## ` marks a subsection in the long documents added for K2-5. Everything after it
        # hangs off that heading, which is what gives chunks 2..N of a section a heading path
        # on this side and nothing at all on the legacy side.
        if block.startswith("## "):
            parent = doc.add_heading(text=block[3:].strip(), level=2, parent=top)
            continue
        doc.add_text(label=DocItemLabel.TEXT, text=" ".join(block.split()), parent=parent)
    return doc.export_to_markdown(), doc.model_dump(mode="json")


def _docling_transport(markdown: str, payload: dict[str, Any]) -> Any:
    """Stand in for docling-serve, so the eval exercises the real K1 ingest path.

    Not a shortcut around the pipeline — `ingest_document` still does the persisting, the PII
    scan, the chunking and the embedding. Only the ML container is replaced, which the eval has
    no business running.
    """
    import httpx

    return httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"document": {"md_content": markdown, "json_content": payload}}
        )
    )


async def _ingest(
    session: AsyncSession,
    eval_set: EvalSet,
    provider: str,
    model: str,
    *,
    structural: bool = False,
) -> tuple[Organization, KnowledgeBase]:
    org = Organization(name="Retrieval eval (scratch)", slug=SCRATCH_SLUG, plan="free")
    session.add(org)
    await session.flush()
    kb = KnowledgeBase(
        organization_id=org.id,
        name="Retrieval eval corpus",
        embedding_provider=provider,
        embedding_model=model,
    )
    session.add(kb)
    await session.flush()

    upload_dir = Path(settings.upload_dir) / str(org.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    for doc in eval_set.documents:
        # The eval document id travels as the filename, which is what lands in chunk metadata
        # and comes back on the Citation. That is how a retrieved chunk is mapped to the
        # document it came from without the eval needing its own bookkeeping table.
        row = Document(
            knowledge_base_id=kb.id,
            organization_id=org.id,
            source_type="text",
            filename=f"{doc.id}.md",
            mime_type="text/plain",
            status="queued",
        )
        session.add(row)
        await session.flush()
        body = f"{doc.title}\n\n{doc.text}".encode()
        path = upload_dir / f"{row.id}.txt"
        path.write_bytes(body)
        row.storage_path = str(path)
        row.size_bytes = len(body)
        await session.flush()
        transport = None
        if structural:
            markdown, payload = _as_docling_json(doc.title, doc.text)
            transport = _docling_transport(markdown, payload)
        await ingest_document(session, row.id, docling_transport=transport)
        if row.status != "ready":
            raise RuntimeError(f"ingest failed for {doc.id}: {row.error_message}")
    await session.commit()
    return org, kb


def _doc_ids(citations: list[Any]) -> list[str]:
    """Ranked chunk hits -> ranked *document* ids, first occurrence wins.

    Several chunks of the same document are one answer, not several. Counting them separately
    would let a long document inflate NDCG by filling the top 10 with itself.
    """
    seen: list[str] = []
    for cite in citations:
        name = str(cite.metadata.get("filename") or "")
        doc_id = name[:-3] if name.endswith(".md") else name
        if doc_id and doc_id not in seen:
            seen.append(doc_id)
    return seen


async def _run_variant(
    session: AsyncSession,
    org_id: uuid.UUID,
    kb_id: uuid.UUID,
    eval_set: EvalSet,
    variant: str,
    provider: str,
    model: str,
    score_threshold: float,
) -> VariantResult:
    embedder = build_embedding_provider(provider, model)
    base, _, weight_arg = variant.partition("@")
    fts_weight = float(weight_arg) if weight_arg else None
    predictions: dict[str, list[str]] = {}
    for query in eval_set.queries:
        if base == "fts":
            # Straight to the keyword half so the dense side cannot contribute. `search()` with
            # hybrid=False is the *dense* path, not this one.
            hits = await retrieval._fts_hits(
                session, org_id, [kb_id], query.text, RETRIEVE_K * 4
            )
            citations = [retrieval._to_citation(chunk, score) for chunk, score in hits][:RETRIEVE_K]
        else:
            citations = await retrieval.search(
                session,
                org_id,
                [kb_id],
                query.text,
                embedder,
                top_k=RETRIEVE_K,
                score_threshold=score_threshold,
                hybrid=(base == "hybrid"),
                fts_weight=fts_weight,
            )
        predictions[query.id] = _doc_ids(citations)
    return score_variant(variant, predictions, eval_set.queries, recall_k=RECALL_K)


def _print_table(results: list[VariantResult], eval_set: EvalSet, embedder: str) -> None:
    print(f"\nembedder: {embedder}   documents: {len(eval_set.documents)}   queries: {len(eval_set.queries)}")
    print(f"\n{'VARIANT':<12} {'NDCG@10':>9} {f'RECALL@{RECALL_K}':>10} {'MRR@10':>8}")
    print("-" * 42)
    for r in results:
        print(f"{r.name:<12} {r.ndcg:>9.4f} {r.recall:>10.4f} {r.mrr:>8.4f}")


def _print_worst(result: VariantResult, eval_set: EvalSet, limit: int = 5) -> None:
    """The queries a variant fails. A headline mean hides which question is broken."""
    by_id = {q.id: q for q in eval_set.queries}
    worst = sorted(result.per_query.items(), key=lambda kv: kv[1])[:limit]
    if not worst or worst[0][1] >= 1.0:
        return
    print(f"\nweakest queries for '{result.name}':")
    for qid, score in worst:
        query = by_id[qid]
        note = f"   [{query.note}]" if query.note else ""
        print(f"  {score:.3f}  {qid}  {query.text!r}{note}")


def _print_per_query(results: list[VariantResult], eval_set: EvalSet) -> None:
    """Per-query NDCG for every variant, side by side.

    A headline mean cannot tell you *why* a variant lost, and the interesting question for
    hybrid retrieval is never "is the average better" but "which queries does each half win,
    and do they overlap". A keyword list that wins nothing the dense list loses has no business
    being fused in at any weight.
    """
    by_id = {q.id: q for q in eval_set.queries}
    names = [r.name for r in results]
    print("\n" + "QUERY".ljust(7) + "".join(n[:11].rjust(12) for n in names) + "  TEXT")
    print("-" * (7 + 12 * len(names) + 30))
    for qid in sorted(by_id):
        cells = "".join(f"{r.per_query.get(qid, 0.0):>12.3f}" for r in results)
        print(f"{qid:<7}{cells}  {by_id[qid].text[:44]}")


def _load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.exists():
        return {}
    return dict(json.loads(BASELINE_PATH.read_text(encoding="utf-8")))


async def run(args: argparse.Namespace) -> int:
    eval_set = load_eval_set()
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v.partition("@")[0] not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {unknown}. Choose from {list(VARIANTS)}, optionally 'hybrid@0.3'.")
        return 2

    provider, model = args.embedder_provider, args.embedder_model
    if provider == "fake" and {v.partition("@")[0] for v in variants} - {"fts"}:
        # Refusing rather than warning: a dense number produced by hashing text is not a weak
        # measurement, it is a meaningless one, and printing it next to a real one in the same
        # table is how a wrong number gets quoted later.
        print(
            "\nThe fake embedder can only score the 'fts' variant. 'dense' and 'hybrid' would\n"
            "be measuring a hash function. Pass --embedder-provider ollama (free, local) or\n"
            "restrict to --variants fts.\n"
        )
        return 2

    engine = create_async_engine(settings.database_url)
    results: list[VariantResult] = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _reset_scratch(session)
            structural = args.chunking.startswith("structural")
            previous_mode = settings.docling_chunk_heading_mode
            previous_tokens = settings.docling_chunk_max_tokens
            if structural and args.chunk_max_tokens:
                settings.docling_chunk_max_tokens = args.chunk_max_tokens
            if structural:
                # Turned on around the ingest only. The transport is a mock, so nothing reaches
                # a real docling-serve, but `converters.build_converter()` checks the flag.
                settings.docling_enabled = True
                settings.docling_endpoint = settings.docling_endpoint or "http://eval-mock"
                settings.docling_chunk_heading_mode = (
                    "inline" if args.chunking.endswith("inline") else "embed"
                )
            print(
                f"ingesting {len(eval_set.documents)} document(s) via the real pipeline "
                f"({args.chunking} chunking)..."
            )
            try:
                org, kb = await _ingest(session, eval_set, provider, model, structural=structural)
            finally:
                if structural:
                    settings.docling_enabled = False
                    settings.docling_chunk_heading_mode = previous_mode
                    settings.docling_chunk_max_tokens = previous_tokens
            for variant in variants:
                results.append(
                    await _run_variant(
                        session, org.id, kb.id, eval_set, variant,
                        provider, model, args.score_threshold,
                    )
                )
            if not args.keep:
                await _reset_scratch(session)
            else:
                print(f"\n(--keep) scratch org left in place: slug={SCRATCH_SLUG}")
    finally:
        await engine.dispose()

    # The baseline key carries the chunking too. A structural number compared against a legacy
    # one would read as a retrieval regression when the only thing that changed is how the
    # corpus was cut up — the same reason the key already carries the embedder.
    embedder = _embedder_key(provider, model)
    run_key = embedder if args.chunking == "legacy" else f"{embedder}|{args.chunking}"
    _print_table(results, eval_set, embedder)
    if args.per_query:
        _print_per_query(results, eval_set)
    else:
        for result in results:
            _print_worst(result, eval_set)

    baseline = _load_baseline()
    recorded = dict(baseline.get("runs", {}).get(run_key, {}))

    if args.save_baseline:
        baseline.setdefault("runs", {})
        baseline["runs"][run_key] = {
            **recorded,
            **{r.name: r.as_dict() for r in results},
        }
        baseline["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        baseline["eval_set"] = {
            "documents": len(eval_set.documents),
            "queries": len(eval_set.queries),
        }
        baseline["score_threshold"] = args.score_threshold
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"\nbaseline written to {BASELINE_PATH}")
        return 0

    print("\ncomparison with the committed baseline:")
    failed = False
    for result in results:
        ok, message = compare(result, recorded.get(result.name), tolerance=args.tolerance)
        print(f"  {'ok  ' if ok else 'FAIL'} {message}")
        failed = failed or not ok
    if failed:
        print(
            "\nRetrieval got worse. If that is intentional, re-run with --save-baseline and say\n"
            "in the commit message why a lower number is the right trade.\n"
        )
        return 1
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--variants",
        default="fts",
        help="comma-separated: fts,dense,hybrid (default: fts - the deterministic one)",
    )
    parser.add_argument("--embedder-provider", default="fake", help="fake|ollama|openai|gemini")
    parser.add_argument("--embedder-model", default="nomic-embed-text")
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.0,
        help="0.0 measures pure ranking. Raise it to sweep the production constant (0.35).",
    )
    parser.add_argument(
        "--chunking",
        choices=("legacy", "structural", "structural-inline"),
        default="legacy",
        help=(
            "legacy = the character splitter (production default). structural = K2's "
            "HybridChunker over a DoclingDocument, with contextualize() as the embedding input. "
            "structural-inline = the same, but the heading path is also prefixed to the stored "
            "chunk, so the keyword half can see it (docs/14 K2-5)."
        ),
    )
    parser.add_argument(
        "--chunk-max-tokens",
        type=int,
        help=(
            "override DOCLING_CHUNK_MAX_TOKENS for a structural run. This is the parameter "
            "docs/14 §3.4 exists to make sweepable — the default 512 is roughly twice the "
            "~250 tokens the legacy 1000-character splitter produces, so comparing the two at "
            "their defaults confounds chunker with chunk size."
        ),
    )
    parser.add_argument("--tolerance", type=float, default=0.02, help="allowed NDCG@10 drop")
    parser.add_argument("--save-baseline", action="store_true", help="record these as the numbers to beat")
    parser.add_argument("--keep", action="store_true", help="leave the scratch org for inspection")
    parser.add_argument(
        "--per-query",
        action="store_true",
        help="per-query NDCG for every variant, side by side, instead of the weakest-query list",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
