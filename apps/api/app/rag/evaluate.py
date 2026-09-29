"""Retrieval-quality metrics and the eval-set data model (docs/13 R2, docs/14 P0-2).

**Why this exists.** Vicero had 773 tests and zero retrieval-quality numbers. `score_threshold`
was moved 0.7 -> 0.35 on 2026-07-21 by feel, `top_k=5` and `chunk_size=1000` have never been
measured, and docs/11 §9 records grounding as the weakest link at 12/15 fabricated — where the
2026-08-02 incident showed the cause was *retrieval legitimately missing* (0.0318 against a 0.35
threshold), not the prompt. Every change after this one — the reranker, per-KB FTS language,
Docling chunking — is an unfalsifiable claim without a number to move.

This is Phase D for retrieval. docs/11 Phase D exists because every guardrail recall figure
before it was measured against probes written in the same session as the code; the same trap is
open here, and §2 of this module's docstring says plainly where this corpus still sits in it.

**No dependencies.** NDCG in ten lines of arithmetic beats adding a metrics library to a
container that has to stay small. `pytest tests/test_retrieval_eval.py` pins every formula
against a hand-computed value, because a silently wrong metric is worse than no metric — it
would make every later phase report an improvement it did not earn.

## 2. What this corpus can and cannot tell you

`apps/api/evals/retrieval/` ships a **seed** set, hand-authored alongside this module. It is
deliberately built to be hard for a lexical retriever (queries share little vocabulary with
their answer; near-miss distractors sit beside every correct document), and it is genuinely
useful for **relative** comparisons: variant A vs variant B on identical data, and regression
detection when a change makes retrieval worse.

It is **not** a substitute for a set generated from a real client knowledge base, and it carries
the same unfalsifiability Phase D was built to remove. `scripts/build_retrieval_eval.py`
implements the recipe against a live KB; running it is how this gets replaced with something
neither the code nor its author has seen.

Absolute numbers here will not match BEIR leaderboards and are not meant to. The relative
ordering between methods on your own data is the signal.

**Two blind spots this corpus has already had, both found by a phase it was supposed to judge.**
Record them: the pattern is that a corpus quietly decides an answer before anyone checks it can.

1. *Rigged against keyword search* (2026-08-13). Every query was written with deliberately low
   lexical overlap — a fair test of dense retrieval, an unfair one for FTS — so the first
   RRF weight sweep "proved" the keyword half was worthless at every weight. Ten
   exact-identifier queries were added before any constant was tuned.
2. *Blind to chunking* (2026-08-17, docs/14 K2-5). Every seed document was 272-445 characters,
   i.e. **one chunk at any chunk size this product uses**, so no chunking change could move the
   number and the mechanism §3.2 W2 improves could not occur at all. Four long multi-section
   documents were added, and `test_retrieval_eval.py` now fails if that property is lost.

**And it was not reproducible.** Until the tie-break fix in `retrieval.fts_statement`
(2026-08-17), four runs of the same variant over the same corpus scored 0.6247, 0.6247, 0.6220,
0.6397. Run a benchmark twice on identical input before trusting anything it says.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# `k` for the headline metric. 10 is the BEIR convention, which keeps the number comparable in
# shape (not in magnitude) with published figures.
DEFAULT_K = 10

#: The committed seed set. `apps/api/evals/`, not `tests/fixtures/`, because its primary caller
#: is an operator command (`make eval-retrieval`) and a script reaching into a test directory
#: invites someone to "clean up" a corpus that is load-bearing.
EVAL_SET_DIR = Path(__file__).resolve().parents[2] / "evals" / "retrieval"


@dataclass(frozen=True, slots=True)
class EvalDocument:
    id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class EvalQuery:
    """One query and the documents that genuinely answer it.

    `relevant` maps document id -> graded relevance (>0). A graded scale rather than a boolean
    because NDCG's whole advantage over recall is that it can say "this one is the best answer
    and that one is an acceptable answer", which is the normal shape of a support KB.
    """

    id: str
    text: str
    relevant: dict[str, int]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class EvalSet:
    documents: list[EvalDocument]
    queries: list[EvalQuery]

    @property
    def by_id(self) -> dict[str, EvalDocument]:
        return {d.id: d for d in self.documents}


class EvalSetError(ValueError):
    """The corpus is malformed. Raised, never warned about.

    Phase D's corpus failed twice on its first run, both times on fixtures its author had
    written wrong — a mislabelled category and a duplicated id. A corpus that cannot go red has
    not been tested either, and a silently-dropped query would quietly inflate every score.
    """


def build_eval_set(
    raw_docs: list[dict[str, Any]],
    raw_queries: list[dict[str, Any]],
    raw_qrels: list[dict[str, Any]],
) -> EvalSet:
    """Validate three already-parsed lists into an `EvalSet`.

    Split from `load_eval_set` so the validation rules can be tested without touching the
    filesystem — `tmp_path` costs 30-60 seconds per use on the Windows dev machine, which put
    two minutes on the suite for three assertions.

    Every consistency check here exists because the failure it prevents is invisible in the
    output: a duplicate id silently overwrites a document, a qrel pointing at a document that
    was renamed makes a query permanently unanswerable and drags the mean down for a reason
    nobody can see, and a query with no qrels at all scores a legitimate-looking 0.0 forever.
    """
    documents = [
        EvalDocument(id=str(d["id"]), title=str(d.get("title") or ""), text=str(d["text"]))
        for d in raw_docs
    ]
    doc_ids = [d.id for d in documents]
    if len(set(doc_ids)) != len(doc_ids):
        dupes = sorted({i for i in doc_ids if doc_ids.count(i) > 1})
        raise EvalSetError(f"duplicate document ids: {dupes}")

    query_ids = [str(q["id"]) for q in raw_queries]
    if len(set(query_ids)) != len(query_ids):
        dupes = sorted({i for i in query_ids if query_ids.count(i) > 1})
        raise EvalSetError(f"duplicate query ids: {dupes}")

    relevance: dict[str, dict[str, int]] = {qid: {} for qid in query_ids}
    known_docs = set(doc_ids)
    for row in raw_qrels:
        qid, did = str(row["query_id"]), str(row["doc_id"])
        if qid not in relevance:
            raise EvalSetError(f"qrel references unknown query {qid!r}")
        if did not in known_docs:
            raise EvalSetError(f"qrel for {qid} references unknown document {did!r}")
        relevance[qid][did] = int(row.get("relevance", 1))

    queries = [
        EvalQuery(
            id=str(q["id"]),
            text=str(q["text"]),
            relevant=relevance[str(q["id"])],
            note=(str(q["note"]) if q.get("note") else None),
        )
        for q in raw_queries
    ]
    unjudged = [q.id for q in queries if not any(g > 0 for g in q.relevant.values())]
    if unjudged:
        raise EvalSetError(f"queries with no relevant document: {unjudged}")
    return EvalSet(documents=documents, queries=queries)


def load_eval_set(directory: Path | None = None) -> EvalSet:
    """Read `corpus.yaml` + `queries.yaml` + `qrels.yaml` and validate them against each other."""
    root = directory or EVAL_SET_DIR

    def _read(name: str) -> list[dict[str, Any]]:
        path = root / name
        if not path.exists():
            raise EvalSetError(f"missing eval file: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not data:
            raise EvalSetError(f"{name} must be a non-empty list")
        return data

    return build_eval_set(_read("corpus.yaml"), _read("queries.yaml"), _read("qrels.yaml"))


@dataclass(slots=True)
class VariantResult:
    """Scores for one retrieval configuration across the whole query set."""

    name: str
    ndcg: float = 0.0
    recall: float = 0.0
    mrr: float = 0.0
    queries: int = 0
    #: query id -> ndcg, so a regression can be attributed to a query rather than a headline.
    per_query: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ndcg": round(self.ndcg, 4),
            "recall": round(self.recall, 4),
            "mrr": round(self.mrr, 4),
            "queries": self.queries,
        }


def dcg(gains: list[int]) -> float:
    """Discounted cumulative gain over an already-ordered list of relevance grades."""
    return sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))


def ndcg_at_k(predicted: list[str], relevant: dict[str, int], k: int = DEFAULT_K) -> float:
    """Normalised DCG@k — the headline metric.

    Rewards putting the *most* relevant document highest, not merely returning it somewhere.
    That is the property that matters here: `build_context_block()` trims from the bottom when
    the character budget is tight, so a correct chunk ranked 9th may never reach the model at
    all.

    Returns 0.0 when the query has no relevant documents, rather than dividing by zero — an
    unanswerable query scores zero, it does not crash the run.
    """
    if k <= 0 or not relevant:
        return 0.0
    actual = dcg([max(0, relevant.get(doc_id, 0)) for doc_id in predicted[:k]])
    ideal = dcg(sorted((g for g in relevant.values() if g > 0), reverse=True)[:k])
    return actual / ideal if ideal > 0 else 0.0


def recall_at_k(predicted: list[str], relevant: dict[str, int], k: int = DEFAULT_K) -> float:
    """Fraction of the relevant documents that appear in the top k.

    Reported beside NDCG because they fail differently, and the difference is diagnostic: high
    recall with low NDCG means the right chunk is being *found* but ranked below the budget cut
    — which is precisely the gap a reranker closes, and precisely the case where adding one is
    worth its latency.
    """
    wanted = {doc_id for doc_id, gain in relevant.items() if gain > 0}
    if not wanted or k <= 0:
        return 0.0
    return len(wanted & set(predicted[:k])) / len(wanted)


def mrr_at_k(predicted: list[str], relevant: dict[str, int], k: int = DEFAULT_K) -> float:
    """Reciprocal rank of the first relevant document; 0 if none is in the top k."""
    wanted = {doc_id for doc_id, gain in relevant.items() if gain > 0}
    for rank, doc_id in enumerate(predicted[:k], start=1):
        if doc_id in wanted:
            return 1.0 / rank
    return 0.0


def score_variant(
    name: str,
    predictions: dict[str, list[str]],
    queries: list[EvalQuery],
    *,
    k: int = DEFAULT_K,
    recall_k: int = 5,
) -> VariantResult:
    """Aggregate one variant's per-query metrics into a `VariantResult`.

    `predictions` maps query id -> ranked document ids. A query missing from `predictions`
    scores 0 rather than being skipped: a variant that returns nothing for a query has failed
    that query, and averaging over "the ones that worked" would hide exactly that.

    `recall_k` defaults to 5 to match the production `top_k`, so the recall column answers
    "would the model actually have been given this?" rather than a number with no counterpart
    in the running system.
    """
    if not queries:
        return VariantResult(name=name)
    ndcgs: dict[str, float] = {}
    recalls: list[float] = []
    rrs: list[float] = []
    for query in queries:
        predicted = predictions.get(query.id, [])
        ndcgs[query.id] = ndcg_at_k(predicted, query.relevant, k)
        recalls.append(recall_at_k(predicted, query.relevant, recall_k))
        rrs.append(mrr_at_k(predicted, query.relevant, k))
    n = len(queries)
    return VariantResult(
        name=name,
        ndcg=sum(ndcgs.values()) / n,
        recall=sum(recalls) / n,
        mrr=sum(rrs) / n,
        queries=n,
        per_query=ndcgs,
    )


def compare(
    current: VariantResult, baseline: dict[str, Any] | None, *, tolerance: float = 0.02
) -> tuple[bool, str]:
    """`(ok, message)` for one variant against its committed baseline.

    `tolerance` absorbs the jitter a non-deterministic embedding provider introduces without
    absorbing a real regression. It is deliberately one-sided: an *improvement* is never a
    failure, but it does not silently become the new baseline either — re-baselining is an
    explicit `--save-baseline`, so a number in the repo is always one somebody chose to record.
    """
    if baseline is None:
        return True, f"{current.name}: no baseline recorded yet (NDCG@10 {current.ndcg:.4f})"
    was = float(baseline.get("ndcg", 0.0))
    delta = current.ndcg - was
    if delta < -tolerance:
        return False, (
            f"{current.name}: NDCG@10 regressed {was:.4f} -> {current.ndcg:.4f} "
            f"({delta:+.4f}, tolerance {tolerance:.2f})"
        )
    return True, f"{current.name}: NDCG@10 {was:.4f} -> {current.ndcg:.4f} ({delta:+.4f})"
