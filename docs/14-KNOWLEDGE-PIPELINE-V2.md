# docs/14 — Knowledge Pipeline v2: Docling ingestion + four-stage retrieval

> **Status:** design specification, **partly implemented** — see §0a for what shipped and what is
> blocked. Execute the rest phase by phase, on request.
> **Supersedes:** parts of `docs/13-AI-COOKBOOK-REVIEW.md` — see §0 for a correction.
> **Revision 2 (2026-08-12):** scope widened after an operator decision — see §1.1. Revision 1
> covered ~40% of Docling; this covers the agreed set.
> **Sources reviewed:** `docling-project/docling` @ v2.119.0 (1,631 files),
> `daveebbelaar/ai-cookbook` @ HEAD (207 files), Vicero `apps/api/app/rag/` and
> `migrations/versions/`.
>
> **This track sits outside the §1 autonomous contract in `CLAUDE.md`, for the same reason
> `docs/11` does.** Each phase adds a dependency, a service, or a per-turn external call.
> Execute a phase only when asked for it by name.

---

## 0a. Status — 2026-08-17 (was 2026-08-13)

**Nothing in the Docling path is enabled for anyone.** The code is built and switched off:
`DOCLING_ENABLED` is false everywhere (blocked on K1-5), structural chunking is off on its own
measurement (K2-5/ADR-067), and the reranker is off platform-wide (blocked on K4-4). Read that as
the honest headline — several phases are "shipped" in the sense that the code exists and is
tested, not in the sense that a client's document goes through it.

| Task | Status |
|---|---|
| **P0-1** FTS literal | ✅ measured and fixed — the `EXPLAIN` is below, and it settles §0 |
| **P0-2** Eval harness | ✅ `make eval-retrieval`, frozen corpus, two CI gates, baselines committed |
| **K1-1…K1-4** Docling converter | ⚠️ ADR-064, behind an interface, outage falls back — **but K1-5 found the service converter was silently sending a malformed request; see below** |
| **K1-5** golden fixtures | ✅ 2026-08-17 — three real (`reportlab`-generated) PDFs, verified live against a running `docling-serve`, not mocked. `DOCLING_ENABLED` stays `false`; K1-5 proves the converter, enabling for clients is still docs/14 §12 |
| **K2-1** real token counts | ✅ cl100k_base; `len/4` was undercounting Tamil/Devanagari ~3× |
| **K2-2/3/4** structural chunking | ✅ built, and **disabled** — see K2-5 |
| **K2-5** measured improvement | ⚠️ ran, and the answer was "not yet": hybrid −0.0029. The gate held; nothing shipped enabled |
| **K2-6** heading in the FTS index | ✅ migration 0021, ADR-067 — recovers half the loss, still short of legacy |
| **K3-1** upload allowlist | ✅ ADR-066, deny-by-default. EPUB/ODF/LaTeX deliberately **not** accepted while Docling is off |
| **K3-2** email gate | ⚠️ shipped, and it was not future work — email had been ingesting for months (ADR-066) |
| **K3-3…K3-6** charts, media queue, ASR, quota | ❌ blocked: no running docling-serve, no real client audio |
| **K4-1/2/3** Reranker | ✅ protocol, no-op default, HTTP cross-encoder, platform key, fails open |
| **K4-5** ADR | ✅ ADR-063 |
| **K4-4** latency delta | ❌ needs a real deployment running a rerank service |
| **K5-1** per-KB `fts_config` | ✅ migration 0019, with the per-config GIN index §5.4 demands |
| **K5-2** PDF page cap | ✅ `MAX_PDF_PAGES`, enforced where upload/URL/re-ingest converge |
| **K5-3** extraction retention | ⚠️ shipped as a **bug fix**: a deleted document was leaving its full text on disk |

**§0's seq-scan question is answered.** Measured on PostgreSQL 16.14 with `enable_seqscan=off`:
the literal form reaches `Bitmap Index Scan on ix_chunks_content_fts` (renamed
`ix_chunks_search_fts_<config>` by migration 0021 — K2-6 changed the indexed expression, and
`make explain-fts` was re-run against the new one); the bind-parameter form
under `plan_cache_mode = force_generic_plan` gets `Seq Scan (cost=10000000000.00..)`. So the
custom-plan escape hatch this section hoped for exists only for the first ~5 executions of a
prepared statement — asyncpg pools connections, so production graduates to the generic plan and
seq-scans. `make explain-fts` re-runs it.

**Two things §5.4 and docs/13 both got wrong, corrected in code:**

1. **PostgreSQL 16 ships `tamil` and `hindi` dictionaries.** §5.4's "be honest about the fix:
   Postgres ships no Tamil or Hindi dictionary, `simple` is the realistic option" is false.
   `tamil` genuinely stems — `கொள்கைகள்` → `கொள்கை` — it is not a `simple` alias. `simple`
   remains right only for a language with no entry at all.
2. **§4.3's pipeline diagram describes something that was not running.** The keyword half scored
   **NDCG@10 0.0278** because `plainto_tsquery` ANDs every term, so RRF had one non-empty list
   and `hybrid` was byte-identical to `dense`. Fixed (ADR-062) → 0.6604. See docs/13 §8.

**What this changes for K1–K3.** §5.1's claim that the eval harness "is the only thing that makes
every other change here falsifiable" is now testable rather than aspirational: `make
eval-retrieval-full` prints a baseline, and a Docling re-chunk that does not move it is a
re-embedding campaign spent for nothing. K2-5's "a number, not a claim" now has somewhere to
come from.

---

## 0. Correction to docs/13

**`docs/13` §3 finding 3 and §4 R4a are WRONG.** I claimed Vicero has no GIN index on the
FTS expression. It does — `migrations/versions/0004_rag_indexes.py` creates both:

```sql
CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_chunks_content_fts    ON chunks USING gin (to_tsvector('english', content));
```

I grepped `alembic/versions/` when the real path is `migrations/versions/`, got no match, and
concluded absence from a failed search. **A negative grep is not evidence of absence — it is
evidence you searched the wrong place.** Recorded here rather than quietly edited.

**The correction found something sharper.** The index is built on a *literal* regconfig, but
SQLAlchemy renders the query with a *bind parameter*:

```python
# app/rag/retrieval.py:69
tsvector = func.to_tsvector("english", Chunk.content)
# renders as:  to_tsvector(%(to_tsvector_1)s, content)
# index is on: to_tsvector('english',        content)
```

An expression index only matches when the planner can prove the expression is identical.
Two-argument `to_tsvector(regconfig, text)` is `IMMUTABLE` only with a constant config. With a
generic plan the `Param` node never matches the index's `Const` node and **Postgres falls back
to a sequential scan**. With a custom plan (first ~5 executions) parameter folding *may* let it
match. **Plan-dependent — measure, do not assert.** Task **P0-1**.

---

## 1. Executive summary

Two repos, two halves of one pipeline. They do not overlap.

| | Docling | ai-cookbook |
|---|---|---|
| Solves | **Getting content out of documents correctly** | **Finding the right chunk, and proving it** |
| Stage | Ingest (offline, background) | Retrieval (online, per turn) |
| Vicero gap | `pypdf` flat text, no headings, no OCR, no tables, 4 formats | No reranker, no eval harness |
| Latency impact | **None** (Celery worker) | **Yes — critical path** |
| Risk | Storage, media queue policy, VLM memory | p50 first-token budget |

- **Docling is the bigger and safer win.** It runs in the background, so it cannot touch the
  417 ms p50 first-token budget. It fixes a live safety bug (§3.2 W1) as a side effect.
- **`docling-slim` is modular.** Base is 8 packages. Run it as `docling-serve` and **Vicero's
  own images carry zero ML dependencies** — the weight lives in Docling's container.
- **The retrieval eval harness is the highest-leverage item overall**, because it is the only
  thing that makes every other change here falsifiable.
- **Total: 5 phases, 20 tasks.** K0–K2 are safe. K3 (media/ASR) is a new job class. K4
  (reranker) touches per-turn latency and needs an explicit decision.

### 1.1 Agreed scope (operator decision, 2026-08-12)

| Decision | Choice |
|---|---|
| Format breadth | **Broad, minus dead weight** — all document/email/EPUB/LaTeX/ODF/image formats; **exclude** XBRL, USPTO, JATS, EBCDIC, video-with-diarization |
| ML pipelines | Layout + table structure · OCR · **Chart understanding** · **ASR (audio/video)** |
| Persist `DoclingDocument` JSON | **Yes** |

**⚠️ Two of these cost more than they appear. Read §3.6 and §3.7 before committing.**
Chart understanding is a **vision-language model**, not a small classifier. ASR is a
**different class of job** from document ingest and needs its own queue, not a bigger timeout.

---

## 2. Understanding the problem

### 2.1 What actually goes wrong today

`docs/11 §9` records grounding as the weakest link: **12/15 fabricated** on
`llama-3.1-8b-instant`, and **no safety phase A–G touches it**. The 2026-08-02 incident showed
the mechanism:

```
visitor asks about opening hours
  → retrieval scores 0.0318, below the 0.35 threshold
  → NO context block is appended at all
  → model invents "Mon–Fri, 9am–5pm"   (KB says Mon–Sat 10am–7pm IST)
```

The prompt was rewritten and re-measured to 0/3 fabricated on that state. **That is a
mitigation, not a fix.** The root cause was retrieval failing to find a chunk that existed.

Every stage here attacks that root cause from a different side:

```
Docling extraction   → the fact is legible at all             (§3.2 W1)
More formats         → the fact is in the KB at all           (§3.5)
Heading-aware chunks → the fact is embedded with its context  (§3.3)
Reranker             → the right chunk beats the near-miss    (§5.2)
Eval harness         → you can prove any of the above         (§5.1)
```

### 2.2 Verified state of the code

| Component | File | Current implementation |
|---|---|---|
| Loaders | `rag/loaders.py:146` | `pypdf` · `python-docx` · `csv` · else `utf-8 decode` — **4 formats** |
| HTML | `rag/loaders.py:47` | `trafilatura` → markdown ✅ already good |
| Chunking | `rag/chunking.py:39` | Recursive **character** split |
| Token count | `rag/chunking.py:14` | `len(text) / 4` — an estimate, not a tokenizer |
| Chunk metadata | `rag/ingest.py:85` | `{filename, source_url}` only |
| Vector dim | `models/knowledge.py:18` | `EMBEDDING_DIM = 768`, **module-level constant** |
| Retrieval | `rag/retrieval.py:83` | pgvector cosine + Postgres FTS + RRF (k=60) |
| Indexes | `migrations/0004` | HNSW ✅ + GIN ⚠️ (§0) |
| Rerank / Eval | — | **neither exists** |

### 2.3 Spec drift already in the repo

`docs/03-DATABASE-SCHEMA.md:79` declares `chunks.metadata (jsonb: {page, heading, ...})`.
`docs/06-AI-ENGINE.md:63` says ingestion should *"attach metadata (page, heading, ordinal)"*.

**Neither is ever populated.** The column exists, the spec requires it, `pypdf` cannot produce
it. Docling closes this without a schema change — the column is already `jsonb`.

---

## 3. Part A — Docling (ingestion)

### 3.1 What Docling is

- **IBM Research Zurich**, hosted under the **Linux Foundation AI & Data**. MIT. v2.119.0,
  production-stable, active changelog.
- Converts **28 input formats** to one `DoclingDocument` — a structured tree (headings,
  sections, tables, figures, captions, reading order), not a text blob.
- **Runs fully locally.** No client data leaves your infrastructure.

### 3.2 The four core wins

**W1 — Layout-aware extraction fixes a live safety bug.**

On 2026-08-03 the PII audit reported `email=2, phone=0` against a KB that visibly contained a
phone number. Cause: PDF extraction delivered the ☎ glyph as `\x01` and the number's internal
spacing as **tabs**, so libphonenumber matched nothing. The fix was a length-preserving cleaned
copy in the detector — correct, but **a backstop on bad input**.

Docling produces structured text with real spacing. **The PII detector then sees a real phone
number.** A safety improvement, not only a quality one.

**W2 — `contextualize()`: chunks embedded with their heading path.**

Highest-value, least-obvious feature. `BaseChunker.contextualize(chunk)` returns the
metadata-enriched serialization intended to feed the embedding model:

```
# what Vicero embeds today
"Refunds are processed within 14 days of the original purchase date."

# what contextualize() produces
"Returns Policy
International Orders
Refunds are processed within 14 days of the original purchase date."
```

A visitor asks *"how long for a refund on my overseas order?"* — today that chunk carries no
signal it is about international orders, because the heading was discarded at chunk time.
**This is exactly the near-miss the 0.35 threshold then rejects.** One method call at ingest.

**W3 — OCR turns a hard failure into a working document.**

Today: `scanned PDF → pypdf → "" → LoaderError → status=failed`. A client uploads a scanned
policy and the product tells them it is broken.

**W4 — Table structure survives.** `do_table_structure=True` reconstructs row/column
relationships into real markdown tables. A pricing table today becomes word soup where numbers
lose their row — precisely the content shape that produces confidently wrong price answers.

### 3.3 Chunking: `HybridChunker`

Two passes over hierarchical chunker output:

1. **Split** only chunks exceeding the tokenizer's limit.
2. **Merge** undersized adjacent chunks *sharing the same headings and captions*
   (`merge_peers=True`).

Table controls: `repeat_table_header=True` re-emits the header on every chunk of a spanning
table; `omit_header_on_overflow` drops it for rows that only fit without it.

`DocMeta` — verified by introspecting installed `docling_core`, not read from docs:

| Field | Type | → `chunks.metadata` |
|---|---|---|
| `headings` | `list[str] \| None` | `heading` (section path) |
| `captions` | `list[str] \| None` | `caption` |
| `doc_items` | `list[DocItem]` | `page` (via provenance) |
| `origin` | `DocumentOrigin \| None` | `mimetype`, `binary_hash` |

### 3.4 Persisting the `DoclingDocument` — the architecture fix

**This was missing from revision 1 and it changes the risk profile of re-chunking.**

`DoclingDocument` round-trips losslessly: `save_as_json()` / `load_from_json()`,
`model_dump_json()` / `model_validate_json()` (verified on the installed package).

Without persistence, every chunking change re-runs the **full ML pipeline** over every document
in every org. With persistence:

```
convert ONCE (expensive: layout + OCR + tables + charts + ASR)
   └─▶ store DoclingDocument JSON
          └─▶ re-chunk N times, FREE          ← tune chunk size, try LineBasedTokenChunker,
                                                 change tokenizer, re-embed — no re-conversion
```

This turns chunking from a one-shot commitment into a **tunable parameter the eval harness can
optimise**. It is what makes §5.1's feedback loop actually usable.

**⚠️ Storage decision required (see §6).** These JSON blobs are large for a big PDF —
per-element geometry, provenance, table cells. Do **not** default them into a Postgres `jsonb`
column without measuring size against real client documents first.

### 3.5 Format coverage — agreed scope

Formats are **cheap**: mostly one small pure-Python parser each, and under `docling-serve` they
live in Docling's image, not Vicero's.

| Format | Extra | In scope | Why |
|---|---|---|---|
| PDF | `format-pdf` | ✅ | Core |
| DOCX / PPTX / XLSX | `format-office` | ✅ | Core client docs |
| HTML / Markdown | `format-web` | ✅ | Already served by trafilatura; Docling adds structure |
| CSV, TXT, AsciiDoc | base | ✅ | Already supported |
| Images (PNG/TIFF/JPEG) | base + OCR | ✅ | Screenshots, scanned pages |
| **Email (EML, MSG)** | `format-email` | ✅ | **See below — biggest omission in rev 1** |
| EPUB | `format-html` + `defusedxml` | ✅ | Manuals, handbooks |
| ODF (ODT/ODS/ODP) | `format-opendocument` | ✅ | LibreOffice clients |
| LaTeX | `format-latex` | ✅ | Cheap, technical clients |
| Box Notes | base | ✅ | Free |
| **Audio / Video** | `format-audio` | ✅ | **§3.7 — own phase, own queue** |
| XBRL | ~~`format-xml-xbrl`~~ | ❌ | Financial filings. Not this product. `arelle-release` is heavy |
| USPTO patents | ~~`format-xml-uspto`~~ | ❌ | Not this product |
| JATS articles | ~~`format-xml-jats`~~ | ❌ | Academic publishing. Not this product |
| EBCDIC | ~~base~~ | ❌ | Mainframe encoding |
| Video + diarization | ~~`format-video`~~ | ❌ | `resemblyzer` → `webrtcvad`, **no wheels, needs a C compiler**. Docling excludes it from its own `all` bundle for this reason |
| HTML render | ~~`format-html-render`~~ | ❌ | Pulls Playwright. Not needed server-side |

**Email deserves its own paragraph.** You are building a **customer support** product. Your
clients' single richest knowledge source is years of resolved support threads — the exact
questions real customers ask, in their words, with the answers that worked. Revision 1 did not
mention it. `format-email` handles `.eml` and `.msg` natively.

**⚠️ Email is the highest-PII-density format you will ever ingest.** Every thread carries
signatures, direct dials, personal addresses, and other customers' details. `scan_document_text()`
already runs at ingest (`ingest.py:73`) and `pii_flags` is populated — but the docs/11 §6 operator
workflow (review flagged documents, clean the source) is **still outstanding** and no code
replaces it. **Do not enable email ingest for clients until that workflow exists.** Egress
redaction is a backstop, not a fix — that is docs/11's own wording.

### 3.6 ⚠️ Chart understanding is a VLM

You selected chart understanding. It is worth having — a pricing or comparison chart is
currently *invisible* to your KB. But be clear on the cost:

```python
# docling/datamodel/chart_extraction_options.py
class ChartExtractionModelKind(str, Enum):
    GRANITE_VISION    = "granite-vision"
    GRANITE_VISION_V4 = "granite-vision-v4"   # default
```

- It runs **Granite Vision**, a vision-language model — not a small classifier.
- It requires `models-vlm-inline`: `transformers`, `accelerate`, `qwen-vl-utils`, `peft`.
- Three prompt modes: `chart2csv` (default on), `chart2code`, `chart2summary` (both default off).
- Gated behind `do_picture_description` / picture classification, so **it only fires on documents
  that actually contain figures** — the cost is per-chart, not per-document.

**Recommendation:** enable `chart2csv` only, leave `chart2code` and `chart2summary` off. A CSV
table is what retrieval and the chunker can use; generated Python is noise in a support KB.

**⚠️ This is the single largest RAM contributor on docling-serve.** Size the container for it, and
treat it as the first thing to disable if the service is memory-constrained.

### 3.7 ⚠️ ASR is a different class of job

You selected audio/video. It is a real product feature — *"upload your onboarding webinar, the
agent answers from it"*. But it does not belong in the document queue.

| | Document ingest | Media ingest |
|---|---|---|
| Typical duration | 1–30 seconds | **Minutes to hours** |
| Input size | KB–MB | **Hundreds of MB** |
| Failure cost | Retry is cheap | Retry is very expensive |
| Model | Layout/OCR | Whisper (`WHISPER_TINY` … `WHISPER_LARGE`) |

**⚠️ Sharing one Celery queue means one client's 2-hour webinar backlog starves every other
client's document ingestion.** This is a multi-tenant fairness problem, and it is the same class
of mistake as the pre-fix n8n visibility default: it looks fine with one tenant and is wrong with
twelve. **Media gets its own Celery queue and its own worker.**

Also required before enabling:

- **Per-org media quota** (minutes/month). Unbounded ASR is unbounded compute spend.
- **Whisper model choice per deployment.** Default is `WHISPER_TINY` — fast, and too weak for
  accented or technical speech. `WHISPER_TURBO` or `WHISPER_DISTIL_LARGE_V3` are the practical
  quality/speed picks. **Measure on real client audio before choosing**; a transcript with wrong
  product names is worse than no transcript, because it grounds the model in falsehoods.
- **Upload size cap + explicit timeout**, separate from `document_timeout`.

### 3.8 ⚠️ Packaging traps found by installing it

**Trap 1 — `chunking-openai` still imports `huggingface_hub`.** Installing
`docling-core[chunking-openai]` (the tiktoken path chosen to *avoid* HF) and importing the chunker
fails:

```
ModuleNotFoundError: No module named 'huggingface_hub'
  chunker/__init__.py → hybrid_chunker.py → line_chunker.py
    → tokenizer/huggingface.py → from huggingface_hub import hf_hub_download
```

`LineBasedTokenChunker` imports the HF tokenizer unconditionally. **Pin `huggingface_hub`
explicitly** even on the OpenAI path. Reproduced, not inferred.

**Trap 2 — never `pip install docling`.** Always the explicit extras list, or `torch`,
`transformers` and `accelerate` land somewhere you did not intend.

**Trap 3 — RapidOCR breaks on read-only filesystems.** Called out in Docling's own
`PdfPipelineOptions` docstring. Provide a writable model-cache volume, or use Tesseract.

**Trap 4 — XML parsers are XXE surface.** Docling pins `defusedxml` for exactly this reason.
Excluding XBRL/USPTO/JATS removes three parsers you had no use for anyway — a security win, not
only a size one.

### 3.9 Target dependency set

```
docling-slim[
  format-pdf, format-office, format-web, format-opendocument,
  format-latex, format-email, format-audio,
  models-local, models-vlm-inline,
  feat-ocr-rapidocr, feat-chunking
]
+ huggingface_hub          # Trap 1
# EXCLUDED: format-xml-*, format-video, format-html-render, models-remote
```

Under `docling-serve` (§4.1 Option B) this is **Docling's image**. Vicero's worker installs
only `docling-slim[service-client]` — `httpx`, `websockets`, `typer`, `rich`.

---

## 4. Architecture

### 4.1 Deployment shape

**Option A — in-process (Docling inside the Celery worker).** Simple, no network hop. But with
`models-local` + `models-vlm-inline` + Whisper the worker image is very large and models load per
worker process. **Not viable at the agreed scope.**

**Option B — `docling-serve` as its own compose service. ★ required at this scope**

```
┌──────────┐   ┌───────────────┐   ┌──────────────────────┐
│ FastAPI  │   │ celery worker │   │    docling-serve     │
│  (api)   │──▶│  q=documents  │──▶│  layout · OCR ·      │
└──────────┘   └───────────────┘   │  tables · charts(VLM)│
                       │            │  · ASR              │
┌───────────────┐      │            └──────────────────────┘
│ celery worker │──────┘                      ▲
│   q=media     │─────────────────────────────┘
└───────────────┘   (§3.7 — separate queue, separate worker)
        │
        ▼
┌────────────┐   ┌──────────────────┐
│ Postgres   │   │  object storage  │
│ + pgvector │   │  DoclingDocument │
└────────────┘   │  JSON (§3.4/§6)  │
                 └──────────────────┘
```

| Criteria | A: in-process | B: docling-serve |
|---|---|---|
| Worker image | **Huge** (torch + VLM + whisper) | **Tiny — no ML deps** |
| Cold start | Slow, per worker | Unaffected |
| Scaling | Coupled to worker count | **Independent** |
| GPU later | Must GPU every worker | **Just the one service** |
| Failure isolation | A bad PDF can OOM the worker | **Contained** |
| Ops complexity | Lower | One more service |

**Design behind an interface regardless**, so A↔B is a config switch:

```python
class DocumentConverter(Protocol):
    async def convert(self, data: bytes, *, filename: str | None,
                      mime_type: str | None) -> ConvertedDocument: ...
```

Implementations: `DoclingServiceConverter` (B) · `DoclingLocalConverter` (A) ·
`LegacyConverter` (today's `loaders.load_bytes`, **never deleted** — it is the fallback).

### 4.2 Target ingest pipeline

```
Document row (queued)
  │
  ├─▶ 1. ROUTE     media (audio/video) → q=media
  │                 everything else    → q=documents
  │
  ├─▶ 2. CONVERT   docling-serve → DoclingDocument
  │                 · layout · reading order · table structure
  │                 · OCR if scanned · chart2csv if figures · ASR if media
  │                 ⤷ on ANY failure: LegacyConverter  ← never regress
  │
  ├─▶ 3. PERSIST   DoclingDocument JSON → object storage (§3.4)
  │
  ├─▶ 4. PII SCAN  scan_document_text(markdown_export)
  │                 (now runs on clean text — §3.2 W1)
  │
  ├─▶ 5. CHUNK     HybridChunker(tokenizer, max_tokens, merge_peers=True)
  │                 ⤷ contextualize() → embed_text
  │                 ⤷ DocMeta → {heading, caption, page}
  │
  ├─▶ 6. EMBED     embedder.embed([c.embed_text ...])   ← NOT c.content
  │
  └─▶ 7. STORE     Chunk(content=raw, meta={...}, embedding=vec)

  RE-CHUNK PATH:   step 3 → 5 → 6 → 7      (skips conversion entirely)
```

**⚠️ Store `content` raw, embed `contextualize()`.** The heading path improves the *vector*; it
must not be shown to the visitor or counted twice against the context budget. `content` remains
the citation text; the enriched string is embedding input only and is not persisted.

### 4.3 Target retrieval pipeline

```
query
  ├─▶ dense  (pgvector cosine, HNSW)  ─┐
  ├─▶ sparse (Postgres FTS, GIN)      ─┤─▶ RRF k=60 ─▶ top-50 ─▶ rerank ─▶ top-k
  └── candidate_k = 50 (today: 20)     │              (exists)   (NEW)
```

---

## 5. Part B — ai-cookbook (retrieval)

### 5.1 The eval harness — do this first

From `knowledge/hybrid-retrieval/docs/build-your-own-eval.md`. Five steps, ~$0.05:

1. Sample ~100 chunks from a real KB.
2. Prompt a small model per chunk: *"generate one realistic question a user would ask that this
   answers, in their own words, not the document's phrasing, under 20 words."*
3. Emit `corpus`, `queries`, `qrels(query_id, source_chunk_id, 1)`.
4. *(Recommended)* LLM-as-judge over the top-20, so a **different but also correct** chunk does
   not score 0.
5. Compute NDCG@10 per variant.

NDCG@10 in pure numpy — no dependency:

```python
def ndcg_at_k(predicted_ids: list[str], relevant: dict[str, int], k: int = 10) -> float:
    dcg = sum(relevant.get(d, 0) / math.log2(r + 2)
              for r, d in enumerate(predicted_ids[:k]))
    ideal = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum(rel / math.log2(r + 2) for r, rel in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0
```

**Why first.** `docs/11` already paid for this: Phase D exists because every recall figure before
it was measured against probes written in the same session as the code — unfalsifiable. **This is
Phase D for retrieval.** It turns `score_threshold=0.35` (moved from 0.7 by feel on 2026-07-21),
`top_k=5` and `chunk_size` from guesses into measurements — and combined with §3.4's persisted
JSON, re-chunking to test a hypothesis costs nothing.

**⚠️ Freeze the generated set.** Regenerating makes runs non-comparable. Commit as a fixture.
**⚠️ Absolute numbers will not match BEIR** — the set is biased by the generating model. The
*relative ordering on your data* is the signal.
**⚠️ Own CI step.** Buried in 773 tests, "1 failed" reads as flake.

### 5.2 The reranker — stage 4

Vicero has stages 1–3 (FTS + dense + RRF k=60). Missing: the cross-encoder. A bi-encoder embeds
query and document **separately**; a cross-encoder feeds both into one model with joint attention.
Much more accurate, much slower — so run it only on the top ~50 fused candidates.

Public BEIR baselines, FiQA-2018, NDCG@10 (`6-evaluate.py` docstring):

| Stage | NDCG@10 |
|---|---|
| BM25 only | ~24 |
| `text-embedding-3-small` only | ~31 |
| **+ cross-encoder rerank** | **~40+** |

**⚠️ Two constraints the cookbook does not have:**

1. **Latency.** NFR-1 is p50 417 ms first token. A hosted rerank call adds a round trip *before*
   generation starts, per turn.
2. **Whose key pays.** ADR-055 established guard models resolve on the **platform** key, never the
   org's, because the fallback chain would silently resolve the wrong credential. **A reranker is
   the same class of decision.** Do not let it fall through `resolve_credential()`.

### 5.3 Recommended: self-hosted `bge-reranker-v2-m3`

| Criteria | Cohere API | bge-reranker-v2-m3 |
|---|---|---|
| Latency | Network RTT every turn | Local inference |
| Cost | Per call, per org, forever | Fixed compute |
| Data egress | Client KB → third party | **None** |
| Multilingual | Good | **Strong — trained multilingual** |
| Ops | None | A model to host (~568 MB) |

**Multilingual is decisive.** `docs/11 §9.2a` records Tamil as a first language in this market.
It also partly compensates for §5.4.

**docling-serve already exists by this phase**, so a second small inference service is a much
smaller marginal step than it would have been. That is an argument for sequencing K1 before K4.

### 5.4 Non-English retrieval is half-off today

`to_tsvector('english', ...)` is hardcoded. For Tamil or Hindi, English stemming is meaningless
and **the keyword half of hybrid retrieval contributes nothing**. The retrieval-side twin of the
known "L1 is English-first" guardrail gap.

**Be honest about the fix:** Postgres ships no Tamil or Hindi dictionary. `'simple'` (tokenize, no
stemming) is the realistic option and is still far better than English stemming on Tamil.

**⚠️ Changing the regconfig requires a matching index** — one GIN index per config in use, or a
generated `tsvector` column. Do not change the literal and leave `0004`'s index behind.

---

## 6. Data model

**No migration is required for the core Docling work.** `chunks.metadata` is already `jsonb` and
already declared as `{page, heading, ...}` in docs/03.

Required additions at the agreed scope:

| Change | Why | Verdict |
|---|---|---|
| `documents.extraction_backend` (`docling`\|`legacy`) | Makes a re-ingest campaign targetable and a regression attributable | **Required** |
| `documents.docling_json_path` (nullable text) | §3.4 persisted document | **Required** |
| `documents.media_duration_seconds` (nullable int) | Per-org ASR quota (§3.7) | **Required with K3** |
| `organizations.media_minutes_quota` | §3.7 | **Required with K3** |
| `knowledge_bases.fts_config` (default `'english'`) | §5.4 | With K5 |
| `knowledge_bases.embedding_dim` | `EMBEDDING_DIM=768` is a module constant, so all KBs share one dim — blocks 1536-dim models per KB | **Out of scope — own ADR** |

**⚠️ Where the DoclingDocument JSON lives is a real decision, not a detail.**

| Option | Verdict |
|---|---|
| Postgres `jsonb` column | ❌ Blobs are large (per-element geometry, provenance, table cells). Bloats the table every tenant query touches, and pushes backup size up fast |
| Object storage / disk, path in DB | ✅ **Recommended.** Same shape as `documents.storage_path`, which already exists |

**Measure real blob sizes against actual client PDFs before committing.** If a 200-page document
produces a 50 MB JSON, retention needs a policy too.

Chunk metadata contract:

```jsonc
{
  "filename": "refund-policy.pdf",
  "source_url": null,
  "heading": ["Returns Policy", "International Orders"],  // NEW — DocMeta.headings
  "caption": null,                                        // NEW — DocMeta.captions
  "page": 4,                                              // NEW — doc_items provenance
  "backend": "docling"                                    // NEW — provenance
}
```

**⚠️ Every consumer must tolerate the old shape.** Existing chunks have `{filename, source_url}`
until re-ingested. Read defensively; never assume `heading` exists.

---

## 7. Implementation plan

### Phase K0 — Measure first (no new dependencies)

| # | Task | Done when |
|---|---|---|
| **P0-1** | `EXPLAIN ANALYZE` the FTS query on real data (§0). If seq-scanning, render the regconfig as a literal | `EXPLAIN` output in the PR body showing Bitmap Index Scan on `ix_chunks_content_fts` |
| **P0-2** | Eval harness: generator, frozen fixture, `ndcg_at_k`, `make eval-retrieval`, own CI step | Baseline NDCG@10 for dense / FTS / hybrid committed as the number to beat |

**⚠️ P0-2 gates everything after it.** Without a baseline, K1–K5 are unfalsifiable.

### Phase K1 — Docling core (documents)

| # | Task | Done when |
|---|---|---|
| K1-1 | `DocumentConverter` protocol + `LegacyConverter` wrapping `load_bytes` | Existing tests pass unchanged through the interface |
| K1-2 | `docling-serve` compose service (§3.9 deps, **no ASR yet**) + `DoclingServiceConverter`; env vars in `.env.example` + `docs/ENV.md` | Service healthy; converter returns markdown for a fixture PDF |
| K1-3 | Wire into `ingest_document` **behind `DOCLING_ENABLED`, default off**, fallback on any failure | A Docling outage degrades to today's behaviour, logged, never a failed document |
| K1-4 | `documents.extraction_backend` + `docling_json_path`; persist the JSON (§3.4, §6) | JSON round-trips via `load_from_json()` in a test |
| K1-5 | Golden-file tests: scanned PDF, table-heavy PDF, **the 2026-08-03 PII-incident PDF** | The PII fixture yields a phone `classify_contact()` detects, no `\x01` |

**K1-5 is the acceptance test for the phase** — it converts a past incident into a regression test,
the same move docs/11 made with the red-team corpus.

> **Status 2026-08-16 — K1-1 … K1-4 shipped; K1-5 is OPEN, so K1 is not done.** ADR-064.
> `app/rag/converters.py`, migration 0020, the `docling` compose service (`expose:` only, no
> published port), `DOCLING_*` settings off by default. Covered by `tests/test_converters.py`
> (13) and `tests/test_ingest_docling.py` (6) — both directions on the same input, including a
> simulated outage still reaching `status=ready` via `LegacyConverter`.
>
> **K1-5 is blocked on binaries, not on code.** It names three real files (a scanned PDF, a
> table-heavy PDF, the PII-incident PDF) and none are in the repo. §11 of this document is
> explicit that hand-typed fixtures cannot substitute — that is precisely how the 2026-08-03 PII
> detector shipped believing it worked, when real extracted text carried `\x01` for ☎ and tabs
> for spacing and libphonenumber matched nothing. Writing a synthetic "scanned PDF" here would
> reproduce that mistake with a green tick on top. **Docling stays disabled for every deployment
> until K1-5 has real files**, which is the honest reading of "K1-5 is the acceptance test".

> **Status 2026-08-17 (later the same day) — K1-5 closed, and it found a real bug in K1-1…K1-4
> in the process.** Three `reportlab`-generated PDFs committed to `tests/fixtures/docling/` (not
> hand-typed — see the folder's README for full provenance and what each one does and doesn't
> prove), verified against a real, running `docling-serve` v2.119.0 — not mocked, not asserted.
> New opt-in test file `tests/test_docling_golden_fixtures.py`, skipped by default (no published
> port on `docling-serve` by design, docs/14 §9), runnable by pointing `DOCLING_ENDPOINT` at a
> reachable instance. `DOCLING_ENABLED` stays `false` — K1-5 proves the converter works, enabling
> it for clients is still the staged rollout in §12, a separate decision.
>
> **⚠️ The real finding: `DoclingServiceConverter` was silently sending a malformed request, and
> every real conversion had `has_structure=False` as a result — not caught by any existing test.**
> It POSTed conversion options as one multipart field named `options` containing a JSON string.
> `docling-serve`'s actual schema (`Body_process_file_v1_convert_file_post`, read from its own
> `/openapi.json`, not guessed) wants `to_formats`/`do_ocr`/`do_table_structure` as **individual
> top-level form fields**. FastAPI accepts the malformed request without complaint and silently
> falls back to its own schema default, `to_formats=["md"]` — so `json_content` came back `None`
> on every real call, verified against all three fixtures before the fix and none after. This
> silently broke K1-4's "persist the `DoclingDocument` JSON" architecture and therefore
> everything K2 depends on for `DocMeta` (heading/caption/page) — **on the enabled path, which is
> off everywhere, so no client was affected, but the code has never actually done what K1-4 and
> K2's own tests believed, because those tests use `MockTransport` and only ever checked that the
> substrings `"to_formats"`/`"md"`/`"json"` appeared *somewhere* in the raw body — true whether
> they're nested under `options` or not.** Fixed in `app/rag/converters.py`'s `_options()`; the
> regression test in `test_converters.py` now parses the multipart body and asserts on actual
> field names, which is the only check that would have caught this. **Lesson, stated because it
> generalises: a mock that only checks a substring is present validates that you *tried* to send
> something, never that the receiving service will *parse* it.**
>
> **Real, measured wins, not narrated ones:**
> - **Table structure** (§3.2 W4): legacy `pypdf` flattens a two-table pricing page to 445 chars
>   of word-soup, no `|` anywhere. Docling reconstructs both tables as real markdown grids,
>   912 chars, columns intact.
> - **OCR** (§3.2 W3): a rasterized page with zero embedded text layer — `pypdf` extracts exactly
>   0 chars. Docling's OCR reads it and extracts 419 chars of real policy text.
>
> **⚠️ Cold-start timeout risk, found by accident, not designed for — fixed the same day
> (`docs/14-FOLLOWUP-PROMPTS.md` task 1).** The *first* conversion against a freshly-started
> `docling-serve` (loading the CPU-only layout model) took **124.6 seconds** — past the 120s
> `DOCLING_TIMEOUT_SECONDS` default — and `convert_with_fallback()` correctly fell back to the
> legacy extractor. `converters.probe_reachable()` now fires a real conversion at API startup
> (gated by `docling_enabled`, never a health-check ping — verified live that pinging `/health`
> never triggers model loading) so the first *client* upload doesn't pay that cost. Verified
> end-to-end on a genuinely fresh container: probe gives up client-side after 5s while
> `docling-serve`'s job worker keeps loading in the background (confirmed in its own logs), and
> the next real conversion dropped to **8.1s**. Narrows the window, does not close it — a
> `docling-serve` crash/restart independent of the API's lifecycle still hits it cold.
>
> **⚠️ The original PII fixture had its own bug, found the same way — by actually running
> `find_pii()` against real output instead of assuming.** Its phone number used five-space digit
> separators, on a (now known wrong) assumption that `pii._matchable()` collapses whitespace
> runs. It maps every character 1:1 by design (so `find_pii()`'s offsets stay valid against the
> original string) and structurally cannot collapse a run without breaking that invariant.
> Verified in isolation: `PhoneNumberMatcher('+91     93453     27506', ...)` matches nothing,
> through *either* extraction path — this was never a Docling-vs-legacy question. Fixed by
> regenerating the fixture with single-space separators (still carrying the `■` phone-icon
> substitution); phone now detected through both paths. **Separately real and NOT fixed:**
> `PhoneNumberMatcher` cannot handle wide irregular spacing between digit groups regardless of
> `_matchable()`, which is a genuine, narrower gap than 2026-08-03's — filed, not silently folded
> into "fixed the fixture." Full detail in the fixtures README.
>
> **K6-A groundwork, captured because it rides on the same call, not required by K1-5:** the raw
> response carries a **top-level** `confidence` object (sibling of `document`, not nested inside
> it) with pre-computed `mean_grade`/`low_grade` strings (e.g. `"excellent"`) — K6-A does not need
> to average component scores itself. `table_score` came back JSON `null` in every real response
> (not float `NaN` as §K6.1 speculated), and a `null` component did not visibly poison
> `mean_grade`. Neither `converters.py` nor any consumer reads this field yet — K6-A still has to
> be built — but the shape is now measured, not assumed.

### Phase K2 — HybridChunker + real tokens

| # | Task | Done when |
|---|---|---|
| K2-1 | Tokenizer wrapper (tiktoken). **Pin `huggingface_hub`** (§3.8 Trap 1). Replace `estimate_tokens` | `token_count` exact for a known fixture |
| K2-2 | `HybridChunker`; map `DocMeta` → metadata (§6) | Heading path on every chunk of a headed document |
| K2-3 | Embed `contextualize()`, persist raw `content` (§4.2) | Test asserts stored `content` has no heading prefix **and** embedding input does |
| K2-4 | **Re-chunk backfill from persisted JSON** (§3.4) — resumable, per-org, no re-conversion | Backfill re-runs safely after interruption |
| K2-5 | **Re-run P0-2.** Record the NDCG delta in this file | A number, not a claim |

**⚠️ Do not ship K2 if K2-5 shows no improvement.** That is what P0-2 is for.

> **Status 2026-08-17 — K2-1…K2-4 shipped; K2-5 ran and its answer was "not yet".** ADR-065.
>
> **K2-5, the number.** 40 documents, 54 queries, `ollama:nomic-embed-text`, threshold 0:
>
> | chunking | fts | dense | **hybrid** |
> |---|---|---|---|
> | legacy (production) | 0.6487 | 0.8983 | **0.8846** |
> | structural, `embed` (§4.2's rule) | 0.6281 | 0.9087 | **0.8745** |
> | structural, `inline` | 0.6388 | 0.9087 | **0.8817** |
>
> `contextualize()` does what §3.2 W2 claims — **dense +0.0104**. But the FTS index is built
> over `chunks.content`, so §4.2's "embedding input only" rule *removes the heading from the
> text the keyword half searches*: **fts −0.0206**, and the fused **hybrid −0.0029 even in the
> best configuration**. So structural chunking stays **off** (it is behind `DOCLING_ENABLED`
> anyway, which is blocked on K1-5). The tokenizer, K2-1, ships enabled — it changes no chunk
> boundary on the legacy path and therefore no retrieval number.
>
> **⚠️ Getting that table required fixing a live retrieval bug first.** The same variant over an
> unchanged corpus scored 0.6247, 0.6247, 0.6220, 0.6397 — the harness was not reproducible, and
> the cause was in the product: `ORDER BY ts_rank DESC` with no tie-break, over a coarse rank
> that the any-term query (ADR-062) ties constantly, so tied chunks came back in physical row
> order. **The same question could answer differently on the same data.** Fixed by ordering on
> `(rank, chunks.id)`. Note the spread was 0.018 against a CI regression tolerance of 0.02 — the
> gate was one unlucky run from a false alarm and blind to anything smaller. P0-2 shipped without
> ever being run twice on the same input.
>
> **⚠️ §3.3's `DocMeta` table is wrong about one field.** `captions` is marked `deprecated=True`
> in the installed `docling-core` *and* the chunker leaves it `None` even for a captioned table
> — the caption is inlined at the top of `chunk.text` instead. Nothing is lost (it is therefore
> in the embedding), but it is not mapped to metadata and reading it would emit a
> `DeprecationWarning` per chunk to populate a key that is always empty. `page` comes from
> `doc_items[].prov[].page_no` as documented, and is omitted rather than defaulted when a format
> carries no page geometry.
>
> **⚠️ P0-2's corpus had a second blind spot, and K2-5 is the phase that hit it.** Every seed
> document was 272–445 characters — one chunk at any chunk size this product uses — so the
> corpus could not measure a chunking change *at all*, and the mechanism W2 improves cannot
> occur in it. Four long multi-section documents and eight queries aimed at their later sections
> were added **before** any conclusion was drawn. A test now fails if that property is lost
> again. Read this next to the 2026-08-13 note about the rigged-against-keyword-search sweep:
> that is twice this corpus has quietly decided an answer before anyone checked it could.
>
> **K2-6 (new): give the keyword half the same context the vector half got.** A
> `chunks.heading` column with the GIN index rebuilt over
> `to_tsvector(config, coalesce(heading,'') || ' ' || content)` — the lexical twin of
> `embed_text`, keeping `content` clean for citations. Deliberately its own commit, because it
> rebuilds the expression index that P0-1 measured, to enable a path that is switched off and
> blocked on K1-5.

> **Status 2026-08-17 — K2-6 shipped. It fixes the mechanism and does not clear K2-5's gate.**
> ADR-067. Migration 0021.
>
> | chunking | fts | dense | **hybrid** |
> |---|---|---|---|
> | legacy (production) | 0.6487 | 0.8983 | **0.8846** |
> | structural `embed`, before K2-6 | 0.6281 | 0.9087 | **0.8745** |
> | structural `embed`, **after K2-6** | 0.6388 | 0.9087 | **0.8817** |
> | structural `inline`, after K2-6 | 0.6400 | 0.9087 | **0.8817** |
>
> **Dense is unchanged to four decimals in every row.** Only the keyword half was touched and
> only the keyword half moved, which is what makes this a controlled result rather than a
> coincidence: **fts +0.0107, hybrid +0.0072** — a little over half the regression ADR-065
> attributed to the heading going missing from the index.
>
> **⚠️ Structural hybrid is 0.8817 against legacy's 0.8846, so structural chunking stays off.**
> That −0.0029 is deterministic now (the harness has been reproducible since the tie-break fix),
> so it is a measured regression on the path production runs, not noise to wave through. The
> residual is no longer attributable to heading text; what is left is chunk **boundaries** — a
> different cause, not investigated. Calling K2-6 the fix because the number moved the right way
> is the error K2-5 exists to prevent.
>
> **No re-ingest was needed, and that was verified rather than assumed.** Every pre-0021 chunk
> has `heading IS NULL`, so the new expression differs from `content` by a leading space that
> `to_tsvector` discards — legacy re-scored byte-identically at 0.6487 / 0.8983 / 0.8846.
>
> **`inline` mode is now dominated.** Its only job was getting the heading into the keyword
> index by pasting it into `content`; the index does that now, both modes land on the same
> hybrid 0.8817, and `inline` still pays by putting the heading in the text a visitor is shown.
> `DOCLING_CHUNK_HEADING_MODE` keeps `embed`; `inline` stays only so the comparison is runnable.
>
> **The index rebuild is the risky half, so it was re-gated.** `make explain-fts` reports
> `Bitmap Index Scan on ix_chunks_search_fts_english` under `force_generic_plan`. The test that
> used to pattern-match an index *definition* now asks the **planner** whether each config's
> index is reachable — a definition string can agree with itself while disagreeing with what
> `fts_statement` renders, and that comparison is the only one P0-1 is about.

### Phase K3 — Format breadth + media

| # | Task | Done when |
|---|---|---|
| K3-1 | Enable document extras: email, EPUB, ODF, LaTeX, images, Box Notes (§3.5). Widen upload validation + UI file types | One golden fixture per newly accepted format |
| K3-2 | **Gate email ingest on the docs/11 §6 PII workflow** (§3.5) | Email uploads refused with a clear message until the workflow ships |
| K3-3 | Chart understanding: `do_chart_extraction=True`, **`chart2csv` only** (§3.6) | A chart fixture yields a CSV table; RAM delta on docling-serve measured and recorded |
| K3-4 | **Separate `q=media` Celery queue + worker** (§3.7) | A long media job provably does not delay a document job — tested, not assumed |
| K3-5 | ASR: `format-audio`, Whisper model chosen **on real client audio**, upload cap, media timeout | Model choice justified with a measured comparison, not a default |
| K3-6 | `media_duration_seconds` + per-org `media_minutes_quota`, enforced before conversion | Over-quota upload rejected with a typed error |

**⚠️ K3-4 before K3-5.** Shipping ASR onto the shared queue is the multi-tenant fairness bug
described in §3.7 — and it will look fine in dev with one tenant.

> **Status 2026-08-17 — K3-2 shipped and it was not future work; K3-1 shipped inverted; K3-3
> to K3-6 not started.**
>
> **⚠️ K3-2's gate was already needed, and this section had the reason backwards.** §3.5 gates
> email on Docling's `format-email`. But `upload_document` validated nothing beyond "the file is
> non-empty", and `loaders.load_bytes` ends in *"anything else → decode as text"*. **An `.eml`
> is RFC-822 text.** It already ingested whole, with no Docling anywhere — every `From:`,
> signature block, direct dial and third party copied on the thread — into a product whose
> docs/11 §6 review workflow still does not exist. Closed now:
> `app/rag/formats.py` refuses `.eml`/`.msg` by extension *and* by `message/rfc822` mime type
> (a thread saved out of a mail client is often `thread.txt`), with a message that says which
> prerequisite is missing. A test pins the extractor behaviour, so the gate cannot be removed
> later on the belief that the path underneath is harmless.
>
> **K3-1 landed as deny-by-default, which is the opposite of "widen".** The task says to widen
> upload validation; there *was* no upload validation. Widening an allowlist that does not exist
> means writing the allowlist. EPUB/ODF/LaTeX/images/Box Notes are deliberately **not** on it:
> they need Docling, Docling is off pending K1-5, and accepting a format the pipeline turns into
> mojibake is worse than refusing it — the client gets a "ready" document that retrieves
> nothing. Media extensions are gated with their own message, because their blocker is K3-4, not
> docs/11 §6, and one shared message would hide that.
>
> **K3-3 to K3-6 are blocked on the same root cause as K1-5**, not deferred by preference:
> - **K3-3** (chart understanding) needs a running docling-serve with Granite Vision loaded; its
>   acceptance criterion is a *measured* RAM delta, which cannot be invented.
> - **K3-4** (`q=media` queue) and **K3-6** (media quota) are infrastructure for a feature that
>   does not exist yet. K3-4's criterion is "a long media job provably does not delay a document
>   job — **tested**, not assumed", and there are no media jobs to test with. Building the queue
>   now would be scaffolding three blockers deep, and §3.7 pairs K3-4 with K3-5 for that reason.
> - **K3-5** (ASR) requires choosing a Whisper model **on real client audio**. There is none in
>   the repo, and a default picked without that measurement is exactly what §3.7 warns against —
>   a transcript with wrong product names grounds the agent in falsehoods.

### Phase K4 — Reranker

| # | Task | Done when |
|---|---|---|
| K4-1 | `Reranker` protocol + `NoOpReranker` default | Retrieval byte-identical with the no-op |
| K4-2 | `BgeReranker` self-hosted (§5.3). `candidate_k=50`. Platform-resolved, **never** `resolve_credential()` | Own metrics bucket, not folded into `TurnResult` |
| K4-3 | Per-agent + platform toggle, **default off**; unavailable → no-op, logged loudly | A reranker that is off must not look like one finding nothing (the Phase C lesson) |
| K4-4 | **Re-run P0-2** + measure p50/p95 added latency | NDCG delta **and** latency delta, both recorded |
| K4-5 | **ADR:** platform infrastructure vs BYO-key org feature | Written before enabling for any client |

**⚠️ Fail open, and say so.** Every guard layer in docs/11 fails open; a reranker must too. A
rerank outage degrades to RRF ordering — never an error, never an empty result set.

### Phase K5 — Language and long tail

| # | Task |
|---|---|
| K5-1 | `knowledge_bases.fts_config`; per-config GIN index; `'simple'` for non-English (§5.4) |
| K5-2 | `document_timeout` tuning (Docling recommends 90–120 s), page-count caps |
| K5-3 | Retention policy for persisted DoclingDocument JSON (§6) |

> **Status 2026-08-17 — K5-1, K5-2, K5-3 shipped. K4-1/2/3/5 shipped 2026-08-13; K4-4 blocked.**
>
> **K5-2.** `MAX_PDF_PAGES` (default 800, `0` disables), enforced in `ingest._enforce_page_cap`
> where the upload, URL and re-ingest paths converge, so the three cannot drift. The message
> carries the page count *and* the limit — "too large" is not something a client can act on,
> "620 pages, the limit is 800" is. `loaders.pdf_page_count()` reads the xref table only, and
> returns `None` rather than raising on a corrupt file: a page cap has no business being the
> thing that decides an encrypted PDF cannot be ingested. `DOCLING_TIMEOUT_SECONDS` was already
> at 120 s, inside Docling's own 90–120 s guidance.
>
> **⚠️ K5-3 was a live data-retention bug, introduced by K1 in this same session.**
> `delete_document` removed `storage_path` and nothing else, so deleting a document left its
> `.docling.json` — the full text, element by element, including anything `pii_flags` had been
> raised on — on disk indefinitely. A second orphan came from re-ingest: when a re-run produced
> no structure the column was cleared while the file stayed, and since deletion works *through*
> that column, nothing would ever remove it.
>
> **The policy is that the JSON's lifetime is the document's** — no separate expiry job. Its only
> purpose is making a re-chunk free (K2-4), which is meaningless once the document is gone; and a
> time-based expiry would silently turn a free re-chunk of a *live* document into a full
> re-conversion. Note `delete_kb` is a **soft** delete, so its documents and their files are
> retained by design — that is the existing contract, not an oversight of this task.
>
> **K4-4 is blocked**, not skipped: it wants a measured NDCG delta **and** p50/p95 latency delta
> from a real deployment, and no rerank endpoint is running. ADR-063 already refuses to enable
> the reranker for any client without exactly that measurement.

---

## 8. Edge cases

| Case | Handling |
|---|---|
| docling-serve down | `LegacyConverter` fallback, log `docling_unavailable`, document still ingests |
| Docling returns empty text | Extraction failure → fallback → only then `LoaderError` |
| Huge PDF (500+ pages) | `document_timeout` 90–120 s; page cap; failure message names the limit |
| Encrypted / corrupt file | Typed error on the document row, never a worker crash |
| **2-hour video** | `q=media`; quota checked **before** conversion; own timeout |
| **ASR produces gibberish** | Low-confidence transcript is worse than none — it grounds the model in falsehoods. Needs a confidence floor and a `failed` status, not silent ingest |
| Chart with no readable data | Chart extraction returns nothing; document still ingests |
| Chunk exceeds embedding max input | `HybridChunker` splits on tokens — structurally impossible after K2 |
| Mixed old/new chunk metadata | Read defensively (§6) |
| Persisted JSON missing on re-chunk | Fall back to full re-conversion; log it |
| Reranker times out | No-op → RRF order. **Never** empty |
| Non-English query | Dense half works; FTS half inert until K5-1 |
| Zero candidates after RRF | Unchanged: no context, fallback message. **Still the fabrication path** — §9 |

---

## 9. Security

- **Tenant isolation unchanged.** Every query in `retrieval.py` filters on `organization_id`. The
  reranker operates on already-filtered candidates — **never** give it a broader pool "for better
  ranking".
- **docling-serve is internal.** Bind to the compose network. **Do not publish its port.** It
  accepts arbitrary documents and URLs — a public port is SSRF plus resource exhaustion, and with
  ASR enabled it is also a compute-exhaustion target.
- **SSRF rules still apply.** `loaders.py` validates IPs/hostnames for URL ingest. If Docling is
  ever handed a URL directly it **must** go through the same validation — a new code path must not
  bypass an existing control.
- **Email is the highest-PII-density format** (§3.5). Gate on the docs/11 §6 workflow.
- **Media files carry PII in a form your detector cannot see.** `scan_document_text()` runs on the
  *transcript* — a spoken credit-card number becomes text and is scannable, but the **source audio
  is not**, and it is now sitting in your storage. Retention and access control for uploaded media
  is a new question this phase creates.
- **Excluding XBRL/USPTO/JATS removes three XML parsers** — an XXE-surface reduction, not only a
  size one.
- **A self-hosted reranker keeps client KB content in-house.** A hosted one ships client document
  text to a third party — contractual and residency, not just technical.
- **Untrusted content stays untrusted.** Better extraction does not make a document trustworthy.
  `neutralize_injections()` must still wrap retrieved chunks. **A cleaner parser is not a
  guardrail** — a well-parsed PDF carries an indirect injection just as well as a mangled one.
  Chart-to-CSV and ASR transcripts are *new* untrusted-content channels and must go through the
  same wrapping.

---

## 10. Performance

| Stage | Where | Latency impact |
|---|---|---|
| Docling conversion | `q=documents` worker | **None on chat.** Ingest slows to seconds–tens of seconds |
| Chart extraction (VLM) | `q=documents` worker | None on chat. Significant per-figure cost + **RAM** |
| ASR | `q=media` worker | None on chat. Minutes per file |
| HybridChunker / `contextualize()` | worker | None |
| Re-chunk from JSON | worker | **Much faster than rev 1's plan** — no re-conversion |
| FTS literal fix (P0-1) | per query | **Improvement** if seq-scanning today |
| `candidate_k` 20 → 50 | per query | Small — both indexes support it |
| **Reranker** | **per turn, pre-generation** | **The only real cost. Measure in K4-4** |

**NFR-1 is p50 417 ms first token. K4 is the only phase that can breach it** — which is why it is
last, defaults off, and requires a measured latency delta before enablement.

---

## 11. Testing

- **Golden files, not hand-typed fixtures.** The 2026-08-03 lesson was explicit: *hand-written
  fixtures cannot tell you what real extracted text looks like*. Commit real files — scanned PDF,
  table-heavy PDF, chart PDF, `.eml` thread, short audio clip, and the PII-incident document.
- **Test the fallback, not just the happy path.** Simulate a docling-serve outage and assert the
  document still ingests via `LegacyConverter`. The 2026-08-04 lesson: when a feature has an allow
  path and a deny path, testing one proves nothing.
- **Assert both directions of `contextualize()`** — stored `content` clean, embedding input
  enriched. One assertion passes when the feature is broken.
- **Test queue isolation explicitly** (K3-4): enqueue a long media job, assert a document job
  completes without waiting. Do not assume it.
- **Never let tests call docling-serve, Whisper, or a rerank model for real.** `conftest.py` must
  disable all three, exactly as it does for L2/L3 guard models — otherwise every ingest test hits a
  live service on a machine where the URL is set.
- **The eval corpus is its own CI gate** (P0-2), separate from the 773-test suite.

---

## 12. Rollout

1. `docling-serve` up, `DOCLING_ENABLED=false`. Nothing changes. Confirm health and RAM headroom.
2. Enable for **one internal org**. Compare conversions side by side against legacy.
3. Enable for new documents platform-wide. Old chunks untouched — mixed state is expected (§6).
4. Backfill re-ingest per org, off-peak, resumable, tracked by `extraction_backend`.
5. K3 formats one at a time, each with a golden fixture. **Email last**, after the docs/11 §6 PII
   workflow. **ASR only after the media queue is proven isolated.**
6. K4 stays off until K4-4 produces both numbers and K4-5's ADR is written.

Rollback at every step is a flag flip, because `LegacyConverter` is never deleted.

---

## 13. Priority

| Rank | Task | Effort | Risk | Payoff |
|---|---|---|---|---|
| 1 | **P0-1** FTS literal (verify + fix) | S | Very low | Possibly large latency win |
| 2 | **P0-2** Eval harness | M | Low | **Makes everything else provable** |
| 3 | **K1** Docling core + persisted JSON | M | Low (fallback) | Quality **+ a safety fix** |
| 4 | **K2** HybridChunker + contextualize | M | Low *(was Med — §3.4 de-risks it)* | Largest retrieval-quality gain |
| 5 | **K3-1/2** Document formats + email gate | M | Low | **Email is the richest untapped KB** |
| 6 | **K5-1** Per-KB FTS config | S | Low | Non-English recall |
| 7 | **K3-3** Chart understanding | M | Med (RAM) | Charts stop being invisible |
| 8 | **K3-4/5/6** Media queue + ASR + quota | L | **Med (fairness, cost)** | New product capability |
| 9 | **K4** Reranker | L | **Med (latency)** | Large accuracy gain |

---

## 14. Future

- Docling MCP server — expose the KB to external agents. Product decision, not performance.
- `granite-docling` VLM pipeline for the hardest layouts — only once P0-2 shows the standard
  pipeline failing on them.
- Video keyframe extraction (without diarization, §3.5).
- LLM-as-judge answer-quality eval on top of the retrieval eval.
- Per-KB `embedding_dim` (§6), unblocking 1536-dim models.
- **Explicitly rejected:** LangChain / LlamaIndex / CrewAI / Haystack integrations. Vicero has
  its own runtime, guardrail layers and tool loop. A second orchestration stack means two places a
  safety bug can hide, and docs/11's layers would not apply to the second one.

---

## 15. Confidence

**High — gap analysis (§2.2, §2.3).** Read from source: `pypdf` at `loaders.py:151`, `len/4` at
`chunking.py:14`, metadata at `ingest.py:85`, `EMBEDDING_DIM` at `knowledge.py:18`, indexes in
`migrations/0004`.

**High — Docling capability, packaging, and the chart/ASR cost claims (§3).** Read from v2.119.0
source and `pyproject.toml`. `DocMeta` fields and JSON round-tripping verified by installing
`docling-core` and introspecting. Trap 1 reproduced, not inferred. `ChartExtractionModelKind =
granite-vision` read from `chart_extraction_options.py`. Whisper defaults from
`asr_model_specs.py`.

**High — K1 and P0-1 being safe.** K1 keeps the legacy path; P0-1 is a rendering fix behind an
`EXPLAIN`.

**Medium — §0's seq-scan conclusion.** The expression/parameter mismatch is certain; whether
Postgres works around it via custom-plan parameter folding is plan-dependent. Stated as "measure
it" because that is the honest reading. **Do not skip the `EXPLAIN`.**

**Medium — the *size* of retrieval gains.** The ~40+ NDCG figure is FiQA (financial forum posts),
not a support KB. The *direction* is well established; the magnitude on Vicero's corpora is
unknown until P0-2 exists. **That uncertainty is the argument for P0-2 first, not against K2/K4.**

**Medium-low — docling-serve RAM at full scope.** Layout + table + OCR + Granite Vision + Whisper
in one container is a lot. I have not measured it. **Size it empirically in K1-2 and K3-3 before
committing to a production instance type.**

**Low — ASR quality on real client audio.** Entirely dependent on audio conditions, accent, and
domain vocabulary. `WHISPER_TINY` is the library default and is almost certainly not the right
choice. K3-5 must measure, not assume.

**Assumptions that would change this plan**

- If ingest latency becomes user-visible (a client watching an upload spinner), chart extraction
  and ASR need progress reporting, not just a status field.
- If most client KBs are HTML/markdown, K1 drops in priority — trafilatura already covers HTML —
  but **K2 stays**, because `contextualize()` and real tokens help every format.
- If a client requires data residency, hosted rerank is off the table and K4 is self-hosted from
  day one.
- If p50 latency is a harder constraint than accuracy, K4 becomes permanently per-agent opt-in,
  like the Phase G web tool.
- If media storage or ASR compute cost exceeds its revenue value, K3-4/5/6 should be cut. It is
  the most expensive phase here and the only one that is a **new product capability** rather than
  a fix to an existing one.

---

# Phase K6 — Extraction confidence + structured facts

> **Added 2026-08-17 (revision 3), after a second pass over the Docling repo.** Revisions 1–2
> treated Docling purely as a text extractor. It also exposes **conversion quality signals** and
> **structured field extraction**, and neither appears anywhere in K0–K5.
>
> **Sequenced after K1-5.** Everything here rides on `DOCLING_ENABLED`, which is off pending
> three real PDF fixtures. Building K6 first means building on a path no client document travels.
>
> K6-A (confidence) is small, cheap and safe. K6-B (facts) is an architecture addition and needs
> its own ADR.

## K6.0 ⚠️ The constraint that shapes this phase

**`DocumentExtractor` is not reachable through `docling-serve`.** Verified against
`docling/service_client/client.py` @ v2.119.0:

| Symbol | Occurrences in the service client |
|---|---|
| `ConfidenceReport` | **3** — confidence **is** available over the service |
| `def extract(` | **0** |
| `DocumentExtractor` | **0** |
| `ExtractionResult` | **0** |

So the two halves of this phase have **completely different costs**:

- **K6-A (confidence)** rides on the conversion call Vicero already makes. Nearly free.
- **K6-B (structured extraction)** has no service path. Using Docling's own extractor means
  running it **in-process**, which puts `torch` + a VLM back into the worker image — and that
  directly contradicts docs/15's deployment thesis (Vicero images carry zero ML dependencies).

**K6-B therefore does NOT use Docling's extractor.** See K6.2 for what it uses instead.

---

## K6-A — Extraction confidence

### K6.1 What it is

`ConversionResult.confidence` (Docling ≥ v2.34.0). Four component scores, two aggregate grades,
at page level **and** document level.

| Field | Meaning |
|---|---|
| `layout_score` | Quality of element recognition |
| `ocr_score` | Quality of OCR-extracted content |
| `parse_score` | 10th percentile of digital text cells — emphasises problem areas |
| `table_score` | ⚠️ **Docling's own docs say "not yet implemented"** |
| `mean_grade` | Average of the components |
| `low_grade` | 5th percentile — highlights the worst pages |

Thresholds, read from `base_models.py:609` (not from documentation):

```python
score < 0.5  → POOR
score < 0.8  → FAIR
score < 0.9  → GOOD
score >= 0.9 → EXCELLENT
```

### K6.2 Why Vicero specifically needs it

**Today a badly-extracted document produces garbage chunks, and retrieval serves them with a
similarity score that looks perfectly healthy.** Nothing anywhere signals that the source was
mangled. That is precisely how the `\x01`-mangled phone number survived until the manual audit
of 2026-08-03 — the detector was fixed, but *the extraction quality itself was never measured*,
so the same class of failure on a different document is still invisible.

Confidence grades close that hole at the only point where it is cheap: ingest.

Docling's own documentation lists the use case verbatim: *"set confidence thresholds for
unattended batch conversions"* and *"identify documents requiring manual review after the
conversion."* That is exactly the operator workflow docs/11 §6 has been missing.

### K6.3 Tasks

| # | Task | Done when |
|---|---|---|
| K6-A1 | `documents.extraction_confidence` (jsonb: component scores + both grades, doc-level and per-page) | Populated on every Docling conversion; **NULL for legacy-extracted documents** — the ADR-054 distinction between "scanned clean" and "never scanned" applies identically here |
| K6-A2 | `DOCLING_MIN_CONFIDENCE_GRADE` (default `POOR` = accept everything). Below it → `status=needs_review`, **not** `failed` | A low-confidence document is still ingested and still retrievable; it is flagged, not withheld |
| K6-A3 | Surface the grade in the document list + detail UI, with the per-page breakdown | An operator can see *which pages* extracted badly, not just that the document did |
| K6-A4 | Structured log + metric on every `POOR` conversion | A client uploading systematically bad scans is visible without opening the UI |
| K6-A5 | Feed grade into the docs/11 §6 review queue alongside `pii_flags` | One queue, two reasons a document needs a human |

### K6.4 ⚠️ Cautions

- **`table_score` is not implemented and will be `NaN`.** `mean_grade` averages the four
  components — **verify what a `NaN` component does to the mean before trusting it.** If it
  poisons the average, use `low_grade` and the individual scores instead. Do not ship a quality
  gate whose arithmetic has not been checked.
- **A new `needs_review` status is a state-machine change.** `status` is currently
  `queued|processing|ready|failed`. Anything branching on "is it ready" must be audited — this
  is the ADR-057 lesson (attention is an axis beside `status`, not a value of it). **Strongly
  consider a separate `review_required` boolean rather than a new `status` value**, for exactly
  the reason ADR-057 gives.
- **Confidence is about extraction, not truth.** An `EXCELLENT` grade on a document full of
  outdated prices is still outdated prices. Never let the grade appear to the model or the
  visitor as a trust signal.
- **Do not gate ingest on confidence by default.** Defaulting to reject would refuse documents
  that retrieve perfectly well. Default `POOR` = accept everything; make the threshold opt-in.

---

## K6-B — Structured facts

### K6.5 The problem it solves

The 2026-08-02 fabrication, reduced to its mechanism:

```
visitor: "what are your opening hours?"
   → embed query → similarity search → best chunk scores 0.0318
   → below the 0.35 threshold → NO context block appended
   → model fills the gap → "Mon–Fri, 9am–5pm"   (KB says Mon–Sat 10:00–19:00 IST)
```

The prompt was rewritten and re-measured to 0/3 fabricated. **That is a mitigation.** The
mechanism — a threshold, a similarity score, and a model with nothing to ground on — is intact,
and `docs/11 §9` still records grounding as the weakest link at 12/15 with no phase A–G touching
it.

Two different shapes of question are being served by one mechanism:

| Question | Right mechanism |
|---|---|
| *"Explain your return policy"* | **RAG** — fuzzy, discursive, needs prose |
| *"What is the refund window?"* | **A lookup** — one fact, one value, no similarity involved |

K6-B gives the second kind its own path:

```
business_hours = "Mon–Sat 10:00–19:00 IST"   ← a row, retrieved by key
```

**No embedding. No threshold. No similarity. No fabrication surface.** The agent either has the
fact or says it does not.

### K6.6 ⚠️ How to extract — NOT with Docling

Per K6.0, Docling's extractor is unreachable over `docling-serve` and would drag a VLM into the
worker. **Use the LLM infrastructure Vicero already has:**

```
Docling markdown (already produced by K1)
        │
        ▼
small model + Pydantic schema  ← structured output; 13 providers already wired
        │
        ▼
facts table (typed, keyed, cited back to a chunk)
```

Why this is the better answer regardless of the constraint:

| | Docling `DocumentExtractor` | LLM + Pydantic |
|---|---|---|
| Deployment | Needs torch/VLM **in the worker** | **Nothing new** |
| Contradicts docs/15? | **Yes** | No |
| Provider choice | Fixed | Any of 13 |
| Schema | Pydantic | Pydantic (same) |
| Precedent in repo | None | `ai-cookbook/models/openai/04-structured-output/` |

The ai-cookbook review (docs/13) already covered the structured-output and Instructor patterns
this needs. This is where they finally earn their place.

### K6.7 Tasks

| # | Task | Done when |
|---|---|---|
| K6-B1 | **ADR: facts as a first-class store beside chunks.** Schema, ownership, precedence vs RAG, staleness | Written and accepted **before** any code |
| K6-B2 | `kb_facts` table: `organization_id`, `knowledge_base_id`, `document_id`, `chunk_id` (citation), `key`, `value`, `confidence`, `extracted_at` | Tenant-filtered at the query layer like every other table |
| K6-B3 | Per-KB fact schema — operator defines which keys matter (`business_hours`, `refund_window_days`, `support_email`…). **Ships empty** | An unconfigured KB extracts nothing. Deciding which facts matter is not a judgement code should make (the Phase G precedent) |
| K6-B4 | Extraction at ingest: markdown → small model → validated Pydantic → `kb_facts`. **Every fact carries the `chunk_id` it came from** | A fact with no citation is not stored |
| K6-B5 | Fact lookup **before** RAG in `retrieve_for_version`; a hit is injected as a distinct, clearly-labelled block | A fact hit is visibly not a retrieved chunk in the assembled prompt |
| K6-B6 | Conflict policy: two documents, two values for one key | **Surface the conflict to the operator; do not silently pick.** A confidently wrong fact is worse than a missing one |
| K6-B7 | Re-run P0-2 **plus** a fabrication A/B on the no-context state (the 2026-08-02 protocol) | The number that justifies the phase |

### K6.8 ⚠️ Cautions

- **A wrong fact is worse than no fact, and much worse than a wrong chunk.** A retrieved chunk is
  hedged by surrounding prose and a visible citation; a fact is stated flatly as truth. **The
  extraction step needs its own precision measurement, not just K6-B7's recall.**
- **Facts go stale silently.** A chunk is re-embedded on re-ingest; a fact extracted six months
  ago sits there until something re-extracts it. **Facts must be deleted and re-extracted with
  their source document** — the K5-3 lesson, where deletion left orphaned extraction on disk.
- **This is a new untrusted-content channel.** Facts are model output derived from client
  documents. A document engineered to produce `support_email = attacker@evil.com` is a stored
  injection with a straight path into a prompt. **Facts must go through
  `neutralize_injections()` and the PII allowlist exactly as retrieved chunks do.**
- **Precedence must be explicit, not emergent.** If a fact and a retrieved chunk disagree, which
  wins? Decide in K6-B1 and write it down. "Whichever the model happens to weight" is not an
  answer.
- **⚠️ Do not let facts silently suppress the fallback message.** If a fact lookup misses, the
  turn must degrade to exactly today's behaviour — RAG, then fallback. Fail open, like every
  other layer in docs/11.

---

## K6.9 Priority

| Rank | Task | Effort | Risk | Payoff |
|---|---|---|---|---|
| 1 | **K6-A1…A5** confidence | S | Low | Extraction quality stops being invisible |
| 2 | **K6-B1** the ADR | S | — | Gates the rest |
| 3 | **K6-B2…B7** facts | L | **Med — a wrong fact states itself as truth** | **The only item in docs/14 that attacks fabrication at its mechanism** |

**K6-A can ship the day K1-5 unblocks.** K6-B should not start until its ADR is accepted.

## K6.10 Confidence

**High** — that `ConfidenceReport` is reachable over the service client and `DocumentExtractor`
is not. Both counted directly in `docling/service_client/client.py` @ v2.119.0. Grade thresholds
read from `base_models.py:609`, not from docs.

**High** — that K6-B should not use Docling's extractor. Follows from the counts above plus
docs/15's deployment constraint; it is an architectural conclusion, not a preference.

**Medium** — the *size* of K6-B's effect on fabrication. The mechanism is sound (a keyed lookup
has no threshold to fall below), but how many real client questions are fact-shaped rather than
prose-shaped is **unmeasured**. If most support questions need discursive answers, K6-B is a lot
of machinery for a thin slice. **K6-B7 must measure this before the phase is called a success.**

**Low** — extraction *precision* on real client documents. Entirely dependent on document
quality and how well the operator specifies the schema. Untested; K6.8's first caution stands.
