# docs/13 — `daveebbelaar/ai-cookbook` review: what is worth taking into Vicero

> Review date: 2026-08-12. Repo reviewed at `HEAD` (207 files, 9 top-level areas).
> Source: https://github.com/daveebbelaar/ai-cookbook
> Status: **R1, R2, R4a and R4b are implemented (2026-08-13). R3 and R5 are not** — see §8.
>
> **⚠️ CORRECTED 2026-08-12 — finding 3 and R4a below were WRONG.** I claimed there is no GIN
> index on the FTS expression; `migrations/versions/0004_rag_indexes.py` creates both it and the
> HNSW index. I grepped `alembic/versions/` when the path is `migrations/versions/` and read a
> failed search as evidence of absence. The real issue is narrower and is now tracked as **P0-1**
> in `docs/14-KNOWLEDGE-PIPELINE-V2.md` §0. **Read docs/14 §0 before acting on finding 3 or R4a.**
>
> **⚠️ CORRECTED 2026-08-13 — two more claims in this document were wrong, and R2 is what found
> them.** §1 says Vicero "already does BM25-equivalent + dense + RRF — that stage is done."
> Measured: the keyword half scored **NDCG@10 0.0278, one query in thirty-six**, and `hybrid`
> came out byte-identical to `dense`. **That stage was not done; it was inert.** And R4b's
> "Postgres ships no Tamil/Hindi dictionary" is false — PostgreSQL 16 ships both, and `tamil`
> genuinely stems. §8 has the numbers.

---

## 1. Executive summary

- The repo is a **teaching cookbook**, not a library. There is no package to install and no
  code to vendor wholesale. The value is in **four specific patterns** and the **measured
  numbers** behind them.
- Vicero is already ahead of most of it. `rag/retrieval.py` already does BM25-equivalent
  (Postgres FTS) + dense (pgvector) + **RRF at k=60** — the exact fusion the cookbook builds
  in `knowledge/hybrid-retrieval/4-rrf.py`. That stage is done.
- **Vicero stops one stage short.** The cookbook's four-stage pipeline is
  `BM25 → dense → RRF → cross-encoder rerank`. Vicero has the first three. The **reranker is
  missing**, and on the cookbook's FiQA benchmark that stage is the single largest jump.
- **The bigger gap is not a technique, it is a measurement.** Vicero has 773 pytest tests and
  **zero retrieval-quality metrics**. `score_threshold` was moved 0.7 → 0.35 on 2026-07-21 with
  no number attached. `docs/11 §9` records grounding as the weakest link at **12/15 fabricated**
  — and the 2026-08-02 incident showed the cause was *retrieval legitimately missing*
  (score 0.0318 vs a 0.35 threshold), not the prompt. **You cannot fix what you do not measure.**
- Three things in the repo I recommend **against** adopting, with reasons in §6.

---

## 2. What is actually in the repo

| Folder | Contents | Relevance to Vicero |
|---|---|---|
| `knowledge/hybrid-retrieval/` | BM25 + dense + RRF + Cohere rerank + **NDCG@10 eval** on FiQA-2018 | **HIGHEST** — the reranker and the eval harness |
| `knowledge/agentic-rag/` | `list_files` / `grep` / `read_file` agent loop, production hardening notes | Low for the widget, medium for operator tooling |
| `knowledge/docling/` | Docling extraction + `HybridChunker` + real tokenizer wrapper | **HIGH** — fixes the PDF extraction and chunking gaps |
| `knowledge/mem0/` | mem0 long-term memory, ADD/UPDATE/DELETE/NONE ops | Low — Vicero already has `memory_summary` + CRM |
| `agents/building-blocks/` | 7 primitives: intelligence, memory, tools, validation, control, recovery, feedback | Conceptual — Vicero already implements 6 of 7 |
| `agents/agent-complexity/` | 5 levels: augmented LLM → prompt chains → tool agent → harness → multi-agent | Conceptual — useful framing for the agent-template catalog |
| `patterns/workflows/` | Prompt chaining, routing, parallelization, orchestrator | Medium — routing pattern maps onto guardrail layers |
| `mcp/` | MCP crash course, stdio/SSE/HTTP clients, Docker, lifecycle | Low now, relevant if Vicero exposes an MCP server |
| `models/openai/` | Structured output, Instructor, Responses API, **human-in-the-loop tool approval** | Medium — the approval gate is a real gap for n8n actions |
| `context/web/` | Web search + fetch tools for an agent | Low — docs/11 Phase G already shipped this |
| `roadmaps/`, `tools/uv-guide/` | Learning roadmaps, uv guide | None |

---

## 3. Verified gaps in Vicero (checked against the code, not assumed)

| # | Finding | File | Severity |
|---|---|---|---|
| 1 | **No reranking stage.** RRF output goes straight to `top_k`. Only mention of "rerank" in the whole codebase is a credential scope string. | `app/rag/retrieval.py:118` | High |
| 2 | **No retrieval eval.** No NDCG, no recall, no labelled query set. `score_threshold` and `top_k` are untested constants. | — | High |
| 3 | ~~**No GIN index on the FTS tsvector.**~~ **WRONG — see the correction above.** The index exists (`migrations/0004`). The real issue: the index is built on a literal regconfig, the query renders a bind parameter, so the planner may not match them. Superseded by **docs/14 §0 / P0-1**. | `app/rag/retrieval.py:69` + `migrations/0004` | Needs `EXPLAIN` |
| 4 | **FTS is hardcoded `'english'`.** In a market where Tamil is a first language (docs/11 §9.2a), the keyword half of hybrid retrieval is inert for non-English content. Mirrors the known L1-is-English-first gap. | `app/rag/retrieval.py:69` | Medium |
| 5 | **Token counts are guessed:** `len(text) / 4`. Chunk sizes are in *characters*, not tokens (ADR-021). Under-fills chunks for English, silently over-fills for CJK/Indic scripts. | `app/rag/chunking.py:14` | Medium |
| 6 | **PDF extraction is `pypdf`.** This is the exact code path that turned a ☎ glyph into `\x01` and phone-number spacing into tabs, which hid a real PII leak from the detector until the 2026-08-03 audit. | `app/rag/loaders.py:117` | Medium |
| 7 | **No tool-call approval gate.** n8n automations execute without a human checkpoint. `runtime.py` caps iterations (`max_iters`) but has no per-tool approval. | `app/chat/runtime.py:58` | Medium |

Findings 1, 2, 3 and 6 all have a direct answer in the cookbook.

---

## 4. Recommendations, ranked

### R1 — Add a cross-encoder reranker as stage 4 (highest value)

**What.** After RRF returns fused candidates, rerank the top ~50 with a cross-encoder and return
the top `k`. A bi-encoder embeds query and document *separately*; a cross-encoder feeds both into
one model with joint attention, so it can judge "does this passage actually answer this question?"
in a way two independent vectors cannot.

**Why it matters here specifically.** Vicero's worst live failure mode is fabrication when
retrieval misses and no context block is appended. Every point of retrieval accuracy is a turn
where the model has real grounding instead of inventing opening hours. This is the one change
that attacks `docs/11 §9`'s "grounding remains the weakest link" from the retrieval side rather
than the prompt side — and §11's own rule says a prompt line is not enforcement.

**Reference numbers** (cookbook `6-evaluate.py`, public BEIR baselines on FiQA-2018, NDCG@10):

| Stage | NDCG@10 |
|---|---|
| BM25 only | ~24 |
| `text-embedding-3-small` only | ~31 |
| Hybrid (RRF) | between the two, above both |
| **+ cross-encoder rerank** | **~40+** |

Two-stage retrieval also lifts Recall@5 ~0.69 → ~0.82 on prose corpora (cited in
`docs/agentic-rag-vs-semantic-rag.md`).

**How it fits Vicero's architecture.**

- Slots in as one function between `search()`'s RRF sort and the `[:top_k]` slice.
- Fetch `candidate_k = 50` instead of the current `top_k * 4`.
- **Must be behind the same abstraction discipline as the vector store** — a `Reranker`
  protocol with a Cohere implementation and a no-op default, so an org without a rerank
  credential degrades to today's behaviour rather than erroring.

**⚠️ Two constraints the cookbook does not have and Vicero does:**

1. **Latency.** NFR-1 is p50 417 ms first token. A Cohere rerank call adds one network round
   trip *before* generation starts. This is a per-turn cost on the critical path. Measure before
   enabling by default; consider enabling per-agent like the Phase G web tool.
2. **Whose key pays.** ADR-055 established that guard models resolve on the **platform** key,
   never the org's. A reranker is the same class of decision — decide explicitly whether rerank
   is platform-funded infrastructure or a BYO-key org feature, and record it as an ADR. Do not
   let it fall through `resolve_credential()`'s agent → org → env chain by default.

**Open-weight alternative if the latency or the vendor is unacceptable:**
`BAAI/bge-reranker-v2-m3` (~568 MB, CPU-viable, self-hosted, no per-call cost, no data egress).
For a multi-tenant platform with a data-residency story this may be the better long-run answer.

---

### R2 — Build a retrieval eval harness (highest *leverage*)

**What.** `knowledge/hybrid-retrieval/docs/build-your-own-eval.md` is a five-step recipe for
producing a BEIR-style eval set from a corpus with no human labels:

1. Sample ~100 documents from a real KB.
2. For each, prompt a small model: *"generate one realistic question this document answers, in
   the user's own words, not the document's phrasing, under 20 words."*
3. Write three files — `corpus`, `queries`, `qrels` (`query_id, source_doc_id, 1`).
4. Optional but recommended: LLM-as-judge over the top-20 retrieved candidates, so a *different*
   correct document does not score 0.
5. Run NDCG@10 across every retrieval variant.

**Why this is the highest-leverage item even though R1 is the highest-value one.**

- Without it, R1 is unfalsifiable. You would ship a reranker and have no way to say whether it
  helped — the same problem `docs/11` Phase D solved for guardrails by building a red-team corpus
  before claiming recall numbers. **This is Phase D for retrieval.**
- It turns three magic constants into measured decisions: `score_threshold` (0.35, changed by
  feel), `top_k` (5), `chunk_size` (1000 chars).
- Cost is negligible: ~$0.05 for 100–200 queries on a small model.

**⚠️ Freeze the generated set once created.** Regenerating produces different queries and makes
runs non-comparable. Commit it as a fixture, exactly like the Phase D corpus.

**⚠️ Absolute numbers will not match BEIR leaderboards** — the set is biased by the generating
model. That is fine. The *relative ordering between methods on your data* is the signal you need.

**Suggested shape:** a `make eval-retrieval` target and its own CI step — buried inside 773
tests, "1 failed" reads as flake. Same reasoning as Phase D's separate gate.

---

### R3 — Fix chunking and PDF extraction (Docling)

**What.** Replace `pypdf` extraction and the character-based recursive splitter with Docling's
`DocumentConverter` + `HybridChunker`, using a real tokenizer.

**Three separate wins:**

1. **Layout-aware extraction.** Docling does AI-driven layout analysis and table-structure
   recognition. `pypdf` returns a flat text stream — which is precisely how a ☎ glyph became
   `\x01` and a phone number's spacing became tabs, hiding a live PII leak from the detector
   (2026-08-03). Better extraction is a **safety** fix here, not just a quality fix.
2. **Real token counts.** `utils/tokenizer.py` in the cookbook is a ~40-line `tiktoken` wrapper
   that satisfies the HuggingFace tokenizer interface. Replaces `len(text) / 4`.
3. **Structure-aware chunk boundaries.** `HybridChunker(merge_peers=True)` splits on document
   structure, then merges undersized neighbours. Vicero already gets this benefit for HTML via
   trafilatura's markdown output — Docling extends it to PDF, DOCX, XLSX, PPTX.

**⚠️ Cost check first.** Docling pulls in `transformers` and layout models. That is a meaningful
container-size and cold-start increase for the Celery ingest worker. Ingestion is already a
background job (`--pool=solo`), so latency there is not user-facing — but image size and memory
are. **Measure the image delta before committing**, and consider scoping Docling to the worker
image only, not the API image.

**⚠️ Chunking changes invalidate every existing embedding.** Re-chunking means re-embedding every
document in every org. This needs a migration plan, not a code swap. Do R2 first so you can prove
the new chunking is actually better before paying to re-embed 12 orgs.

---

### R4 — Two small, cheap, high-certainty fixes

These are not from the cookbook — they surfaced while comparing against it. Both are low-risk.

**R4a — ~~Add a GIN index on the FTS expression.~~ SUPERSEDED — see the correction at the top.**
The index already exists in `migrations/0004`. The live question is whether the planner *uses*
it, given the literal-vs-bind-parameter mismatch. Tracked as **P0-1** in
`docs/14-KNOWLEDGE-PIPELINE-V2.md` §0 — verify with `EXPLAIN ANALYZE` first, fix the rendering
only if it is seq-scanning.

**R4b — Make the FTS language configurable per knowledge base.** The `'english'` literal makes
the keyword half of hybrid retrieval inert for Tamil, Hindi and other non-English content. This
is the retrieval-side twin of the known "L1 is English-first" guardrail gap. Note honestly that
Postgres ships no Tamil/Hindi dictionary — `'simple'` (no stemming) is the realistic fallback and
is still far better than English stemming applied to Tamil.

---

### R5 — Human-in-the-loop tool approval (worth a look, not urgent)

`models/openai/10-human-in-the-loop/2-tool-call-approval.py` shows an approval gate before a tool
executes. Vicero has *conversation* handoff but no *tool-call* approval — an n8n automation
bound to an agent runs without a checkpoint. This matters as soon as an automation does something
irreversible (issues a refund, sends an email, writes to a client CRM).

`agents/agent-complexity/4-agent-harness.py` is also worth reading for two runtime guards Vicero
does not have: `max_budget_usd` (a hard dollar cap per run, alongside the existing `max_iters`
turn cap) and structured-output validation of the final answer shape.

---

## 5. Priority order

| Rank | Item | Effort | Risk | Payoff | Do it because |
|---|---|---|---|---|---|
| 1 | **R2** Retrieval eval harness | M | Low | Enables everything else | Nothing after this is guesswork |
| 2 | **R4a** GIN index on FTS | S | Very low | Latency at scale | One migration, no behaviour change |
| 3 | **R1** Cross-encoder reranker | M | Medium (latency) | Largest accuracy gain | Directly attacks the fabrication root cause |
| 4 | **R4b** Per-KB FTS language | S | Low | Non-English recall | Half the hybrid stack is off for Tamil today |
| 5 | **R3** Docling extraction + tokenizer | L | Medium (re-embed) | Quality + a safety fix | Do after R2 proves it helps |
| 6 | **R5** Tool-call approval gate | M | Low | Safety for automations | Before any automation moves money |

**R2 before R1 is the important ordering.** It is the same lesson `docs/11` already paid for:
Phase D existed because every recall figure before it was measured against probes written in the
same session as the code, i.e. unfalsifiable. Shipping a reranker without an eval repeats that
mistake in a new area.

---

## 6. What I recommend *against* taking

**Agentic RAG (grep-loop) for the widget path — no.**

- The cookbook's own decision table puts *"FAQs, customer-support tickets, marketing content"*
  squarely in the **Semantic RAG** column. That is exactly Vicero's corpus.
- Latency: 5–15 s per answer vs Vicero's 417 ms p50 first-token target.
- Token cost: 3–10× vanilla RAG, on a platform where 61 checklist probes already exhausted the
  Groq free tier in one session.
- Multi-tenancy: a filesystem-grep tool means giving a model a path-scoped filesystem per org.
  That is a new isolation surface next to the existing query-layer `organization_id` filter, and
  the cookbook's own `_safe_path()` hardening exists because that surface is easy to get wrong.
- **Where it *would* fit:** an internal operator/admin tool for searching across a client's docs
  and runbooks, where latency does not matter and the operator is trusted. Not the visitor path.

**mem0 — no.** Vicero already has `chat/memory.py` (rolling `memory_summary` on a small model)
and a CRM with entity resolution by email/phone. mem0 would duplicate both and add Qdrant +
Neo4j to a stack that deliberately standardised on pgvector. *Do* steal the idea of its
ADD/UPDATE/DELETE/NONE extraction ops as a framing for CRM capture conflicts — but not the
dependency.

**MCP crash course — not now.** Well written, but Vicero's integration story is n8n over
REST + signed webhooks and that works. Revisit only if you decide to expose Vicero itself as an
MCP server for third-party agents. That is a product decision, not a performance one.

---

## 7. Confidence

**High** on the gap analysis (§3) — every finding was verified by reading Vicero's actual source
and migrations, not inferred. The absence of a GIN index, of any reranker, and of any retrieval
metric are all directly observable.

**High** on R2 and R4a — low-risk, well-understood, no architectural commitment.

**Medium** on R1's *magnitude*. The ~40+ NDCG figure is FiQA (financial forum posts), not a
customer-support KB. The direction of the effect is very well established across the literature;
the size on Vicero's corpora is unknown until R2 exists to measure it. **That uncertainty is
the argument for R2 first, not an argument against R1.**

**Medium** on R3 — the extraction-quality and safety benefits are clear, the container-size and
re-embedding costs are real and unmeasured.

**Assumptions that would change these recommendations:**

- If p50 latency is a harder constraint than answer accuracy, R1 becomes a per-agent opt-in
  rather than a default, and a self-hosted `bge-reranker-v2-m3` becomes more attractive than
  Cohere.
- If most client KBs are HTML/markdown rather than PDF, R3 drops several places — trafilatura
  already covers the HTML path well.
- If a client requires data residency, the Cohere option is off the table entirely and R1 must be
  self-hosted from the start.

---

## 8. Implementation log — 2026-08-13

Executed in the §5 priority order. **R2 first was the right call and it paid immediately**: two of
the four things shipped below are bugs the harness found on its first run, not items from this
document.

### What shipped

| Item | Status | Commit theme |
|---|---|---|
| **P0-1** (supersedes R4a) | ✅ | FTS regconfig rendered as a literal so the GIN index matches |
| **R2** eval harness | ✅ | `make eval-retrieval`, frozen corpus, 2 CI gates |
| — *found:* keyword half was inert | ✅ fixed | any-term tsquery (ADR-062) |
| — *found:* RRF regressed after that fix | ✅ fixed | weighted RRF (ADR-058) |
| **R1** reranker | ✅ built, **off** | protocol + no-op + HTTP cross-encoder (ADR-063) |
| **R4b** per-KB FTS language | ✅ | `knowledge_bases.fts_config`, migration 0019 |
| **R3** Docling | ❌ not started | gated on R2 by this document's own §5 |
| **R5** tool-call approval | ❌ not started | see below |

### The numbers, which did not exist before this session

Frozen corpus, 36 documents / 46 queries, `ollama:nomic-embed-text`, committed as
`apps/api/evals/retrieval/baseline.json`:

| Variant | before | after | |
|---|---|---|---|
| keyword (FTS) | **0.0278** | **0.6604** | `plainto_tsquery` ANDed every term |
| dense | 0.8977 | 0.8977 | unchanged |
| hybrid | 0.8977 *(= dense exactly)* | 0.8974 | RRF had nothing to fuse before |

### P0-1: measured, not asserted

docs/14 §0 called the seq-scan question "plan-dependent — measure, do not assert". Measured on
PostgreSQL 16.14, and it resolves in both directions:

```
literal regconfig:                 Bitmap Index Scan on ix_chunks_content_fts
bind parameter, generic plan:      Seq Scan  (cost=10000000000.00..)  with enable_seqscan=off
```

A prepared statement's first ~5 executions get a *custom* plan, where the parameter is folded and
the index matches — which is exactly why this was invisible in dev. asyncpg prepares every
statement and pools connections, so a long-lived production connection graduates to the generic
plan and starts scanning the whole `chunks` table. `make explain-fts` prints both plans and exits
1 if the literal form stops reaching the index.

### The two findings R2 produced

1. **The keyword half of hybrid retrieval was inert** (ADR-062). `plainto_tsquery` joins every
   lexeme with `&`, so a customer sentence became nine ANDed stems and matched no chunk. Because
   RRF then had one non-empty list, **`hybrid` was byte-identical to `dense`** — the hybrid
   retrieval this product advertises was dense-only in production, and §1 of this document said
   the stage was done.
2. **Fixing it made `hybrid` regress below `dense`** (0.8977 → 0.8162), because textbook RRF
   weights both lists equally and these two are not comparable. Now weighted, defaulting to a
   conservative 0.05. **ADR-058 is deliberately explicit that this is a floor chosen to avoid
   shipping a measured regression, not a fitted optimum** — per-query, fusion beat *both*
   retrievers where the keyword list had signal (q001 0.37/0.52 → 0.92), so the keyword half is
   unrewarded by this corpus rather than worthless. A cross-encoder is the structural fix: once
   it orders the pool, fusion only has to produce good recall.

### What the corpus cannot tell you

It was hand-authored in the same session as the code — **the exact unfalsifiability docs/11
Phase D was built to remove**, and §2 of `app/rag/evaluate.py` says so rather than implying
otherwise. It also had a hole: every query was written with deliberately low lexical overlap,
which is a fair test of dense retrieval and a rigged one against keyword search. Ten
exact-identifier queries (order references, decline codes, style codes, form numbers) were added
once the first weight sweep exposed it. **Generating a set from a real client KB is the
outstanding follow-up**, and nothing here is a substitute for it.

### Deliberately not done

- **R3 (Docling)** — this document's own §5 says "do after R2 proves it helps", and R2 now exists
  to prove it. It is also the item that adds a container, ML dependencies and a re-embedding
  campaign across every org; docs/14 K1–K3 scopes it and it needs the image-size and RAM
  measurements docs/14 §15 flags as unmeasured. **Not narrowed away — sequenced.** One cheap
  piece of it is independent and still open: the real-tokenizer fix for `len(text) / 4`
  (finding 5), which needs no Docling.
- **R5 (tool-call approval)** — ranked last here and called "not urgent". A correct version is
  not a code change but a feature: persistence for a pending call, an endpoint to approve it, an
  Inbox surface, and a way to resume a turn across it. It wants its own phase, not a tail-end
  commit in a retrieval batch.
- **The reranker is built but enabled nowhere.** docs/14 K4-4 requires a measured p50/p95 latency
  delta and K4-5 an ADR before any client gets it; ADR-063 is written, the latency measurement
  needs a real deployment with a rerank service running.
