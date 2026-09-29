# Phase K6 — implementation prompt

> Paste into a fresh session. Full spec is `docs/14-KNOWLEDGE-PIPELINE-V2.md` § "Phase K6".
> **Do not start K6-B until K6-B1's ADR is written and accepted.**

---

## Context you must read first

1. `CLAUDE.md` — especially §1 (autonomous execution) and §2 (Definition of Done)
2. `docs/14-KNOWLEDGE-PIPELINE-V2.md` §0a (what is shipped vs enabled) and **§ Phase K6**
3. `docs/15-DEPLOYMENT-CAPACITY.md` §9/§10 — why Vicero images carry no ML dependencies
4. `docs/11-SAFETY-GUARDRAILS.md` §6 and §9 — the review workflow and the grounding gap

## Two rules that override any instinct to simplify

- **A prompt line is not enforcement.** Anything that must not happen needs a code-level check.
- **Fail open.** Every layer in docs/11 degrades to prior behaviour on failure. K6 is no
  exception: a confidence probe that errors must not fail an ingest, and a fact lookup that
  misses must land on exactly today's RAG-then-fallback path.

## ⚠️ Blocking prerequisite

**K1-5 must be green first.** `DOCLING_ENABLED` is `False` (`app/core/config.py:87`) pending
three real PDF fixtures. K6-A reads a field on the Docling conversion result — if Docling never
runs, there is nothing to read. **Do not build K6 against the legacy extractor.**

Also: `docling-serve` exists in `infra/docker-compose.yml` (dev) but **not** in
`infra/docker-compose.prod.yml`. K6 is dev-testable today; it is not prod-deployable until that
service is added and sized per docs/15 §3.

---

# PART 1 — K6-A: extraction confidence

## What to ADD

**1. Migration — `documents.extraction_confidence` (nullable jsonb)**

Store the document-level component scores, both aggregate grades, and the per-page breakdown.

⚠️ **Nullable is load-bearing.** `NULL` = never assessed (legacy extraction). `{}` = assessed.
This is exactly the ADR-054 distinction that `pii_flags` already makes; keep them consistent and
make the UI show them differently.

**2. Migration — `documents.review_required` (boolean, default false)**

⚠️ **Do NOT add a `needs_review` value to `documents.status`.** `status` is a lifecycle
(`queued|processing|ready|failed`); review is a flag that coexists with `ready`. Adding it as a
status value means every `status == "ready"` check in the codebase silently starts excluding
flagged documents. **This is ADR-057's lesson applied to documents instead of conversations —
read that ADR before disagreeing.**

**3. Parse confidence off the conversion result**

`ConfidenceReport` **is** available through `docling/service_client` — verified, 3 occurrences.
Component scores are `layout_score`, `ocr_score`, `parse_score`, `table_score`. Aggregates are
`mean_grade` and `low_grade`. Thresholds (from `base_models.py:609`):

```
< 0.5 → POOR    < 0.8 → FAIR    < 0.9 → GOOD    >= 0.9 → EXCELLENT
```

⚠️ **`table_score` is documented by Docling as "not yet implemented" and will be `NaN`.**
`mean_grade` averages the four components. **Before writing any gate: check empirically what a
`NaN` component does to `mean_grade`.** If it poisons the average, use `low_grade` and the
individual scores and say so in a comment. Do not ship arithmetic you have not verified — this
is the same discipline as the ADR-069 `docker inspect` verification.

**4. `DOCLING_MIN_CONFIDENCE_GRADE`, default `POOR` (accept everything)**

Below threshold → set `review_required = true`. **Never `status = failed`.** A low-confidence
document is still ingested, still chunked, still retrievable. It is flagged, not withheld.

⚠️ **Do not default this to reject.** Defaulting to a gate refuses documents that retrieve
perfectly well and turns a quality signal into an outage.

**5. Env var in `.env.example` AND `docs/ENV.md`** — required by CLAUDE.md §2.6.

**6. UI: grade on the document list + per-page breakdown on detail.** An operator needs to see
*which pages* extracted badly.

**7. Structured log + metric on every `POOR` conversion.** A client uploading systematically bad
scans should be visible without opening the UI.

## What to CHANGE

| File | Change |
|---|---|
| `app/rag/ingest.py` | After conversion, read confidence → persist → evaluate threshold → set `review_required`. **Wrap in try/except: a confidence probe must never fail an ingest.** |
| Docling converter (ADR-064) | Return confidence alongside text. `LegacyConverter` returns `None` — do not synthesise a fake grade for it |
| Document schemas/serialisers | Expose confidence + `review_required` |
| docs/11 §6 review queue | Flag on **either** `pii_flags` **or** `review_required` — one queue, two reasons |

## Tests

- Grade mapping at every boundary: `0.49 / 0.5 / 0.79 / 0.8 / 0.89 / 0.9`
- **A `NaN` `table_score` does not silently produce a wrong `mean_grade`** — this is the one that
  catches the §K6.4 hazard
- Legacy-extracted document → confidence stays `NULL`, `review_required` stays false
- A `POOR` document is **still `ready` and still retrievable**, just flagged
- Confidence parsing raising → ingest still succeeds, warning logged
- ⚠️ Assert **both** directions of the threshold — above and below, with the same document. The
  2026-08-04 lesson: testing only the deny path passes when the feature is broken

---

# PART 2 — K6-B: structured facts

## ⛔ Do not start before K6-B1

Write the ADR first. It is not ceremony: precedence, conflict policy and staleness all have to
be decided before there is code that assumes an answer.

## The one design decision that is already made

**Do NOT use Docling's `DocumentExtractor`.** Verified in
`docling/service_client/client.py` @ v2.119.0:

```
ConfidenceReport   → 3 occurrences   (reachable over the service)
def extract(       → 0
DocumentExtractor  → 0
ExtractionResult   → 0
```

There is no service path. Using it means running Docling in-process, which puts `torch` and a
VLM into the worker image and **directly contradicts docs/15's deployment thesis.**

**Use the LLM infrastructure that already exists:**

```
Docling markdown (K1 already produces it)
      ↓
small model + Pydantic schema (structured output — 13 providers already wired)
      ↓
kb_facts (typed, keyed, cited back to a chunk_id)
```

Pattern reference: `ai-cookbook/models/openai/04-structured-output/` and its Instructor examples,
already reviewed in docs/13.

## What to ADD

**1. `kb_facts` table**

```
organization_id, knowledge_base_id, document_id, chunk_id,
key, value, confidence, extracted_at
```

⚠️ **`chunk_id` is mandatory, not decorative.** A fact with no citation is not stored. Facts are
stated flatly as truth; an uncitable one is unauditable.

⚠️ Tenant-filtered at the query layer like every other table. No exceptions.

**2. Per-KB fact schema, shipping EMPTY**

The operator declares which keys matter (`business_hours`, `refund_window_days`,
`support_email`…). An unconfigured KB extracts nothing.

⚠️ **Do not seed a default schema.** Which facts matter is a client judgement, not a code
judgement — the same reasoning that ships Phase G's domain allowlist empty.

**3. Extraction at ingest** — markdown → model → validated Pydantic → `kb_facts`.

**4. Fact lookup before RAG** in `retrieve_for_version`. A hit is injected as a **distinct,
clearly-labelled block**, visibly not a retrieved chunk.

**5. Conflict surfacing.** Two documents, two values, one key → **surface it to the operator. Do
not silently pick a winner.** A confidently wrong fact is worse than a missing one.

## What to CHANGE

| File | Change |
|---|---|
| `app/rag/agent_retrieval.py` | Fact lookup **before** `retrieval.search()`. ⚠️ A miss must fall through to exactly today's path — RAG, then fallback |
| `app/chat/assembly.py` | A labelled facts block, distinct from the context block |
| `app/rag/ingest.py` | Extraction step after chunking. Best-effort — **never fails the ingest** |
| Document deletion / re-ingest | ⚠️ **Delete facts with their source document.** This is K5-3's bug in a new place: a stale fact outliving its document is worse than orphaned text on disk, because it will be *served as truth* |
| `app/chat/guardrails.py` | Facts through `neutralize_injections()` |
| `app/chat/pii.py` | Facts through the PII allowlist |

## ⚠️ Security — read before writing the extraction step

**Facts are a new untrusted-content channel.** They are model output derived from client
documents. A document crafted to yield `support_email = attacker@evil.com` is a stored injection
with a direct path into an assembled prompt — and unlike a retrieved chunk, it arrives
*unhedged and labelled as fact*.

Facts must go through `neutralize_injections()` and the PII allowlist **exactly as retrieved
chunks do**. Better extraction does not make content trustworthy. A cleaner parser is not a
guardrail, and neither is a typed schema.

## Tests

- A fact hit produces a labelled block distinct from retrieved context
- ⚠️ A fact **miss** produces byte-identical behaviour to today — assert against the current path
- Deleting a document deletes its facts
- Re-ingesting a document replaces its facts (no duplicates, no orphans)
- An injection payload in a fact value is neutralised
- A PII value in a fact is redacted unless allowlisted — **both directions, same value**
- Conflicting facts surface rather than silently resolving
- Extraction model unavailable → ingest still succeeds, facts empty, warning logged

## Measurement — K6-B7, and the phase does not ship without it

Two numbers, not one:

1. **`make eval-retrieval-full`** — the standing baseline
2. **The 2026-08-02 fabrication A/B on the no-context state** — ask a fact-shaped question where
   retrieval scores below threshold, with and without facts, 3 runs each

⚠️ **Also measure extraction precision, which K6-B7 does not cover.** Recall tells you facts were
found; precision tells you they were right. **A wrong fact is worse than no fact.** Sample real
extracted facts against their source documents by hand.

⚠️ **And answer the question the spec flags as unmeasured:** how many real client questions are
fact-shaped rather than prose-shaped? If most support questions need discursive answers, K6-B is
a lot of machinery for a thin slice. **Report that ratio.**

---

## Definition of Done (CLAUDE.md §2)

1. `tsc --noEmit`, `ruff`, `mypy` clean
2. Lint clean
3. Full suite passes
4. Playwright check for any UI change
5. Conventional Commit per task
6. Every new env var in `.env.example` **and** `docs/ENV.md`
7. **Additionally for K6:** update docs/14 §0a with what shipped and what stayed off, and append
   a CLAUDE.md §11 session entry

## Report back

- The `NaN` `table_score` finding — what it actually does to `mean_grade`
- Both K6-B7 numbers, plus the precision sample and the fact-shaped-question ratio
- Anything in the K6 spec that turned out to be wrong. **Prefer a correction with the mistake
  left visible over a silent edit** — that is how docs/13 §0 and docs/15 §8 are written, and it
  is why the next session can trust them.
