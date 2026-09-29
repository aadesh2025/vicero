---
name: session-log
description: Session-by-session build history for Vicero — incident postmortems, root-cause analyses, ADRs referenced, and hard-won gotchas about app/chat/, app/rag/, prompt assembly, infra, and deployment. Load before starting significant work, before touching an area with unfamiliar history, or when session context beyond git log is needed.
---

# Vicero session log (append-only; newest first)

> Contract above is stable. This section is a running note of what shipped per session so a
> fresh session has context beyond git log. Full detail lives in `docs/PROGRESS.md` +
> `docs/DECISIONS.md`; keep entries here to a few lines.

### 2026-09-25 — Phase S1: self-serve signup + 10-day trial (ADR-088, ADR-090; docs/18)
- **Reverses "workspaces are provisioned per client".** Signup → one trial workspace → limits → silent stop. All
  limits are in `app/core/plans.py`; **`Organization.plan` defaults to `legacy` and migration 0028 backfilled every
  existing org to it** — never change that default to `trial`, or a forgotten code path locks a client out.
  `trial_expired` is computed, not stored.
- **The choke point is `chat/inbound.InboundTurn.events()`** (widget HTTP/SSE/WS + every channel). The gate sits after
  the visitor message is persisted and before any cost or text. `billing/usage.py` (not `core/` — architecture test
  forbids core importing models) does the atomic reserve in its **own short transaction** (a row lock held across a
  streamed reply serialised every visitor of the org).
- **Suite convention:** conftest turns `SELF_SERVE_ENABLED` off (autouse) so the ~1300 existing tests keep bootstrapping
  unmetered tenants; opt in with the `self_serve` fixture (`test_self_serve_*.py`, `test_message_metering.py`,
  `test_trial_emails.py`). The limiter is memory-only in tests (a shared Redis carried lockout counters between runs).
  The E2E API must also run `SELF_SERVE_ENABLED=false` (CI updated).
- **Gotchas:** newer FastAPI wraps included routers (`app.routes` is not flat — read a router's own `.routes`);
  the tool wrapper's shell parser chokes on apostrophes inside heredocs (write files with the Write tool);
  `test_architecture.py::test_no_new_package_import_cycle` is a **baseline failure** (a `core`/`db`/`llm`/`models` cycle
  already on HEAD). A stuck test process from a previous session held a lock on the dev DB and blocked `ALTER TABLE` —
  I ran everything on a scratch database (`vicero_s1`) instead of touching it.
- **Not verified live:** Google/Facebook OAuth, real SMTP, Meta behaviour (mocked). Human steps: docs/18 plan §7.
- A **second Claude session was committing to the same repo** during this work (n8n signature gate, ADR-089); files
  were kept separate by path-scoped commits.

### 2026-09-24 — R14 fixed (ADR-083): a dropped chat stream no longer loses the reply
- Hand the reply to a Celery task (`chat.finalize_turn`) from the disconnect handler; the worker
  recreates the conversation/user message under the same client-assigned UUIDv7 if they never
  committed. **Do not retry persistence inline in the handler — it cannot work:** `CancelledError`
  is a `BaseException` (skips `get_session()` rollback *and* commit) and anyio's cancel scope
  re-raises at *every* later checkpoint, so every recovery `await` is cancelled again, including
  `anyio.to_thread` (the `queue_email` pattern). A plain synchronous `.delay()` with no `await` in
  the function is what works. Catch **both** `CancelledError` and `GeneratorExit` (~1/3 vs ~2/3).
- Verified: load test 91/91 convs, 91/91 user msgs, 90/91 replies (was 0/91). One ~0.5%
  straggler unexplained. Recovered reply = what had streamed (may be truncated).
- The "pre-existing unrelated failure" `test_playground_without_handoff_feature_does_not_trigger`
  was a **stale test** (default provider is groq; Playground 502s instead of echoing since
  2026-07-31) — fixed. Don't carry it forward as "known failing".
- Windows reserves TCP 5430–5729 after a reboot → Postgres can't bind 5433. Use
  `POSTGRES_HOST_PORT=5750` + per-command `DATABASE_URL` (no file edits).

### 2026-09-23 — RISK-REGISTER R4 measured, and a real data-loss bug (R14) found doing it
- **New `infra/perf/load_test.py`** — the first concurrency test ever run against this stack;
  every prior verification anywhere in this repo is sequential, one-request-at-a-time
  correctness. Deliberately isolates platform throughput from LLM-provider latency: every test
  agent uses `provider: "fake"` (instant, no network) against a DEDICATED API instance with
  guard L2/L3 disabled and its own Redis DB index — a real concurrency test against Groq would
  risk repeating the 2026-08-10 incident (free tier exhausted by 61 *sequential* probes), and
  mixing its Celery traffic into the operator's normal dev queue would have muddied both.
- **Numbers**: chat first-token p50 49ms→2220ms (c=1→50), workflow-run p50 355-1581ms→
  3700-4057ms (c=1→25), **zero HTTP-level errors at any level** — the platform didn't fall over,
  it degraded close to linearly. Two causes identified, not fixed (a production sizing decision,
  not a code bug): no `pool_size`/`max_overflow` on the async DB engine (asyncpg default caps at
  15 concurrent connections/process); the documented Windows dev `--pool=solo` Celery worker
  (one process, no parallelism) caps workflow throughput near 3.3 req/s regardless of load. Full
  numbers in `docs/15-DEPLOYMENT-CAPACITY.md` §11.1.
- **⚠️ R14 (new, filed to `RISK-REGISTER.md` as P0): a 100%-reproducible data-loss bug, found
  only because the load test's own client disconnects the SSE stream after the first token — the
  same thing a browser tab does on navigation, a pattern no prior test in this codebase
  exercised.** Every early-disconnected chat turn (38/38 in this run) logs an
  `unhandled_exception` (a `CancelledError` propagating through `BaseHTTPMiddleware` into a
  SQLAlchemy session mid-flush) — but worse, **the assistant's reply is never persisted**:
  queried directly, 53/53 user messages from the concurrent runs have zero matching assistant
  message, while a matched control (identical setup, stream drained to completion) persisted
  correctly first try. Client sees HTTP 200 throughout (SSE headers already on the wire before
  the stream breaks) — nothing signals the loss. **Not fixed** — needs a design decision (persist
  partial content on disconnect vs. discard on purpose; middleware order; move persistence
  earlier in `app/chat/runtime.py`) that touches the chat streaming/persistence path, flagged
  rather than guessed at.
- `RISK-REGISTER.md` (R4 marked measured, R14 added), `docs/15-DEPLOYMENT-CAPACITY.md` (new §11)
  updated. See `docs/PROGRESS.md` for the full write-up.

### 2026-09-23 — RISK-REGISTER R1 closed: backups now cover the `uploads` volume
- **ADR-082.** `infra/scripts/backup.sh` had only ever `pg_dump`ped Postgres — the `uploads`
  volume (client-uploaded documents + each one's persisted `DoclingDocument` JSON) was never
  backed up, flagged at docs/15 §8.3/docs/09 §4 and carried forward unfixed into
  `RISK-REGISTER.md` as **R1**, the top item on the P0 list. Extended the existing `backup`
  script/service rather than adding a second one: a **read-only** mount of the SAME `uploads`
  volume `api`/`worker` already write to, a new `UPLOADS_DIR` env var, `tar -czf` alongside the
  `pg_dump`, same timestamp/rotation/volume. `restore.sh` takes the archive as an optional
  second argument. Backward compatible — `UPLOADS_DIR` unset still produces the DB dump alone,
  now with a loud warning instead of a silent gap, so a bare Postgres-only environment keeps
  working unchanged.
- **Verified against real Postgres and real client-shaped data, not fixtures.** Ran the actual
  `backup` service's own command (`postgres:16` image, network-joined to the real dev
  `vicero-postgres-1`, the real dev `apps/api/var/uploads` bind-mounted read-only) — 48 tables
  dumped, 2993 real uploaded-file entries archived. Restored BOTH into a throwaway database and
  a throwaway directory: `organizations` row count matched (2 = 2) and `diff -rq` between the
  original uploads directory and the extracted archive came back identical. Also confirmed the
  no-`UPLOADS_DIR` path still works and `docker compose -f docker-compose.prod.yml config`
  resolves the intended mount (`uploads` volume, `read_only: true`, `/uploads`) rather than a
  duplicate volume under a new name.
- `docs/09-DEPLOYMENT.md` §4, `docs/15-DEPLOYMENT-CAPACITY.md` §8.3, and `RISK-REGISTER.md` R1
  all updated to reflect the closure rather than left stale next to code that now does the
  thing they say it doesn't. k8s (R11) unaffected — no RWX volume/backup story exists there yet,
  unchanged and not pulled forward here.

### 2026-08-17 — docs/15 PROD-3: memory ceilings, and the deploy command that never worked
- **ADR-069.** Memory limit on **all ten** prod services (every value overridable, defaults for the
  16 GB VPS of docs/15 §4 Option 1) plus memory **reservations** on Postgres and Redis. Before this,
  any container could take all RAM and the kernel OOM-killer chose a victim **host-wide** — usually
  Postgres, being the largest RSS — and `restart: unless-stopped` restarted into the same state.
- **Verified, not assumed.** Non-swarm Compose *does* honour `deploy.resources`: `limits.memory` →
  `Memory`, `reservations.memory` → `MemoryReservation`, `limits.cpus` → `NanoCpus` (checked with
  `docker inspect` on a throwaway stack). And an overrunning container is killed **alone**
  (`OOMKilled=true`, exit 137) — the property §2 actually asked for.
- **A limit is not protection; the reservation is.** A limit only stops a service growing. Under
  host pressure the kernel reclaims from containers **above** their reservation first, so
  `POSTGRES_MEM_RESERVATION` / `REDIS_MEM_RESERVATION` are what make "protected by construction"
  true. A test asserts both exist.
- **⚠️ The ceilings sum to ~16.25 GB on a 16 GB box, deliberately** — ceilings, not a budget; sizing
  each service at worst case would idle most of the machine. **⚠️ And the risk this fix introduces
  is real: a limit set too low is a crashloop.** §7 rates the §3 RAM numbers medium confidence and
  they were never measured here, so defaults carry headroom and all are overridable. Postgres is the
  one to watch (an HNSW build is spiky). `docker inspect --format '{{.State.OOMKilled}}'` separates
  "limit too low" from "bug"; docs/09 §3 says so.
- **⚠️ A correction to docs/15 §3.4 — embeddings are latency-critical too.** §3.4 splits work into
  latency-critical (reranker) and throughput (chart VLM, Whisper) and omits embeddings, but
  `retrieval.search()` embeds the visitor's **query** inline on every RAG turn, so `ollama` is on the
  p50 first-token path exactly as the reranker is. Hence **no CPU cap on `api` or `ollama`**; CPU
  caps belong on the batch ML services when they land. §3.4's argument is intact, its target moves.
- **⚠️ Found while fixing this, and worth more than the fix: the documented deploy command never
  worked.** `${VAR}` interpolation is resolved by Compose from the shell and `infra/.env` — **never**
  from the `../.env` the header told you to populate. Verified both ways: with `POSTGRES_PASSWORD` in
  `../.env` and cleared from the shell, `docker compose config` still failed; `--env-file ../.env`
  got past it. Fails loudly before anything starts, which is its one virtue. Compose header and
  docs/09 §3 now carry `--env-file` and attribute each var to the mechanism that reads it.
- **`--maxmemory` on Redis was rejected on purpose**: it is the Celery **broker** as well as the
  cache, so eviction would silently drop queued ingest/email tasks. A restart loses nothing (AOF);
  a vanished task is invisible.
- Mutation-checked again: no limits → red, a new unlimited service → red, reservations dropped →
  red, CPU cap on `ollama` → red. Legacy `mem_limit` stays green because Docker applies it
  identically (measured). Suites: **920 pytest**, ruff + mypy clean.
- Note for the next session: the pytest suite now takes **~18 minutes** on this machine, against
  195s earlier the same day. Not investigated (it is green), and **not** the orphaned-process
  contention it first looked like — a clean run with nothing else touching the test database took
  the same time. Budget for it rather than assuming a hang.

### 2026-08-17 — docs/15 PROD-1 + PROD-2: file ingest was broken in production, and no test could see it
- **ADR-068. Both were deployment bugs: every line of app code was correct and the suite was
  green.** PROD-1 — `api` and `worker` are separate containers with separate filesystems and **no
  shared uploads volume**, so the api wrote the file, the worker read it elsewhere, got "Stored file
  is missing", and the document landed `failed`. PROD-2 — `x-api-env` passed
  `OLLAMA_BASE_URL=http://ollama:11434` while the prod compose declared **no `ollama` service**.
  Both invisible in dev (one host directory, host-installed Ollama) and invisible to a smoke test,
  because URL and pasted-text ingest never touch the filesystem. Same family as ADR-044.
- **⚠️ docs/15's own PROD-2 fix would not have fixed it.** `ollama/ollama` starts **empty** — it
  serves an API with no models, and embedding against an unpulled model is an error, not a
  download. Adding the service alone gives a healthy port that cannot embed, i.e. the same symptom.
  So it pulls the model on start and **the healthcheck asserts the model is present, not that the
  port answers.**
- **⚠️ And its other option — "point it at a reachable provider" — does not exist.**
  `build_embedding_provider` accepts `openai` and `gemini` and builds an **Ollama** client for
  both; no adapter for either was ever written. Setting `EMBEDDING_PROVIDER=openai` to escape a
  missing Ollama changes nothing and there is no symptom that says so — now warned at startup.
  `chunks.embedding` is `vector(768)` too, so a 1536-dim model is a migration, not an env var.
- **⚠️ The volume alone would have swapped one bug for another, and this was measured.** Docker
  seeds an empty named volume from the image's directory *including ownership*, but creates a
  **root-owned** mountpoint when the path is absent from the image — and the api runs as uid 10001.
  Two minimal images differing only in that `mkdir`: with it `appuser appuser` and the write
  succeeds, without it `root root` and `touch` fails with `Permission denied`. `/app/var/uploads`
  is now created in the same `RUN` as the `useradd`, and a test pins the ordering. "Permission
  denied at the first client upload" is no better than "file not found".
- Dev compose's `ollama` had the identical never-pulls-a-model gap; fixed there too. Its published
  `11434` still collides with the host Ollama that CLAUDE.md §12 actually uses — noted in the file,
  deliberately not changed. Dev has no PROD-1 because api and worker share one `../apps/api:/app`
  bind mount.
- **Embeddings deliberately do not gate startup.** `service_started`, never `service_healthy`, and
  the probe never raises: embeddings are required by the knowledge base, not the platform, and a
  worker held back by a model download would take webhooks, email and campaigns with it. A queued
  ingest task waits in Redis — a delay, not an outage.
- **The root cause was that nothing tested deployment shape.** `tests/test_infra_prod_compose.py`
  now asserts properties, not lines: every service touching uploads mounts *the same* volume at the
  same path (naming `api`/`worker` would wave a future `q=media` worker into the same bug); every
  internal hostname is a declared service, with an explicit `_EXTERNAL_HOSTS` allowlist so a new
  external dependency is a decision; the pulled model matches `Settings.embedding_model`; ollama
  publishes no port. **All eight assertions were run against a mutated compose** — both original
  bug states plus six plausible half-fixes (volume on api only, a different volume each, model
  never pulled, model drifted, port published, `service_healthy`) — and all go red.
- **k8s is documented, NOT fixed**, and that is a trade: 2 api + 2 worker replicas need RWX; a
  `ReadWriteOnce` PVC would not fix it and an RWX PVC on a cluster without an RWX StorageClass
  stays `Pending`, leaving every pod in `ContainerCreating` — a broken platform instead of a broken
  feature. The manifest opens with the warning, the exact YAML for RWX clusters, and the pointer to
  object storage. **PROD-3 (resource limits) still open.** `backup.sh` still does not cover uploads.
- `pyyaml` was undeclared while `app/rag/evaluate.py` imports it at module level — arriving
  transitively and working by luck. Declared. New env var `EMBEDDING_PROBE_ENABLED` (default on,
  off in the suite so it does no network I/O). Suites: **917 pytest**, ruff + mypy clean.

### 2026-08-17 — docs/14 K2-6: the keyword index gets the heading, and the gate still says no
- **ADR-067, migration 0021.** `chunks.heading` + the GIN indexes rebuilt over
  `coalesce(heading,'') || ' ' || content` — the lexical twin of `TextChunk.embed_text`, so the
  heading informs both retrievers while `content` stays exactly what a citation shows a visitor.
  This is the follow-up ADR-065 filed against itself.
- **It fixes the mechanism ADR-065 identified: fts +0.0107, hybrid +0.0072, dense unchanged to
  four decimals.** Dense not moving is what makes it a controlled result — only the keyword half
  was touched and only the keyword half moved. **⚠️ And it does not clear K2-5's gate:** structural
  hybrid **0.8817 vs legacy 0.8846**, i.e. still **−0.0029** on the path production runs, and
  deterministic now rather than noise. Structural chunking **stays off**. The residual is no longer
  heading text — it is chunk *boundaries*, a different cause, uninvestigated. Recovering half a
  regression is not the same as removing it, and the number moving the right way is not permission
  to stop measuring.
- **No re-ingest needed, verified rather than argued.** Every pre-0021 chunk has `heading IS NULL`,
  so the new expression differs from `content` by a leading space `to_tsvector` discards; legacy
  re-scored **byte-identically** (0.6487 / 0.8983 / 0.8846). That equality *is* the safety check.
- **⚠️ The index rebuild is P0-1's exact hazard, so it was re-gated, not assumed.** An expression
  index serves only the expression it was built from. `make explain-fts` (updated to
  `ix_chunks_search_fts_english`) reports `Bitmap Index Scan` under `force_generic_plan`. And the
  test that used to pattern-match an index *definition* now asks the **planner** per config — a
  definition string can agree with itself while disagreeing with what `fts_statement` renders,
  which is the only comparison that decides whether keyword retrieval touches an index at all.
  A second test pins `fts.SEARCHABLE_SQL` against the migration's copy, since a migration cannot
  import it.
- **`DOCLING_CHUNK_HEADING_MODE=inline` is now dominated, not merely redundant.** Its only job was
  getting the heading into the keyword index by pasting it into `content`; the index does that now,
  both modes land on the same hybrid 0.8817, and `inline` still pays by putting the heading in text
  a visitor reads. Default stays `embed`; `inline` kept so the comparison is runnable. `.env.example`
  and `docs/ENV.md` both described the old trade and were corrected — a stale env comment is how
  the next session picks the wrong default.
- **docs/14 §0a was dated 2026-08-13 and said "K1–K3 not started".** Rewritten as the first thing a
  fresh session reads, with the honest headline: **nothing in the Docling path is enabled for
  anyone** — `DOCLING_ENABLED` off (K1-5), structural chunking off (its own measurement), reranker
  off (K4-4). "Shipped" here means the code exists and is tested, not that a client's document
  goes through it.
- Infra, recorded because it silently reverts: `POSTGRES_HOST_PORT=5433` now lives in `infra/.env`
  and the root `.env`'s `DATABASE_URL` matches. It was being passed per-command, so the next
  session would have pointed `alembic upgrade head` at **another project's** Postgres on 5432.
- Suites: **899 pytest**, ruff + mypy clean. K1-5 / K3-3…K3-6 / K4-4 still blocked, same reasons.

### 2026-08-17 — docs/14 K1+K2: the eval harness was not reproducible, and that was a product bug
- **K1 (Docling) shipped behind a converter interface; K2 (real tokens + structural chunking)
  shipped with structural chunking deliberately OFF.** ADR-064, ADR-065. Migration 0020.
- **⚠️ `ORDER BY ts_rank DESC` had no tie-break, so the same question could answer differently
  on the same data.** Found by running the eval's `fts` variant four times over an unchanged
  corpus: **0.6247, 0.6247, 0.6220, 0.6397** — a spread of 0.018, wider than every effect K2 was
  measuring and wider than the **0.02 tolerance CI fails on**. `ts_rank` is coarse and ADR-062's
  any-term query makes ties the normal case, so tied chunks came back in physical row order and
  `LIMIT` kept a different set each run. Now `ORDER BY rank DESC, chunks.id`. **P0-2 shipped
  without ever being run twice on the same input** — do that before trusting a benchmark.
- **⚠️ K2-5 says do not ship K2 without an improvement, and the honest answer was "not yet".**
  40 docs / 54 queries / `ollama:nomic-embed-text`: legacy **hybrid 0.8846**, structural `embed`
  **0.8745**, structural `inline` **0.8817**. `contextualize()` does what docs/14 W2 promised —
  **dense +0.0104** — but the FTS index is built over `chunks.content`, so §4.2's
  "embedding-input-only" rule *takes the heading out of the text the keyword half searches*
  (**fts −0.0206**) and the fused path ends up net negative. Structural chunking stays off; the
  fix is filed as **K2-6** (a `chunks.heading` column with the GIN index rebuilt over
  `coalesce(heading,'') || ' ' || content`), deliberately not in the same commit because it
  rebuilds the index P0-1 measured to enable a path that is switched off anyway.
- **⚠️ The corpus had a second blind spot and K2-5 walked into it.** Every seed document was
  272–445 characters — **one chunk at any chunk size this product uses** — so it could not
  measure a chunking change at all, and the first run would have killed the phase on a benchmark
  artifact. Four long multi-section documents + 8 queries aimed at their *later* sections were
  added **before** any conclusion was drawn, and a test now fails if the property is lost. Second
  time this corpus has quietly decided an answer before anyone checked it could (see 2026-08-13).
- **K1's rule: a Docling outage is not an ingest failure.** `convert_with_fallback()` catches
  everything — HTTP error, timeout, bad JSON, and an extraction that is merely *empty* — and
  re-runs the legacy extractor. Nothing re-drives a failed document, so propagating an outage
  would leave every upload during it as `failed` with an error about an internal service.
  `docling-serve` is `expose:`d with **no published port** (SSRF + resource exhaustion), and
  **URL ingest deliberately stays on trafilatura** so it cannot route around `load_url`'s
  private/loopback checks.
- **⚠️ K1-5 is OPEN, so K1 is not done and Docling is enabled nowhere.** It needs three real
  binaries (scanned PDF, table-heavy PDF, the 2026-08-03 PII-incident PDF) that are not in the
  repo. docs/14 §11 says commit real files; a synthetic "scanned PDF" would reproduce the exact
  2026-08-03 mistake — where hand-typed fixtures hid that real extracted text carries `\x01` for
  ☎ and tabs for spacing — with a green tick on top.
- **Two corrections to docs/14 from introspecting the installed package:** `DocMeta.captions` is
  deprecated **and always `None`** (the chunker inlines captions into `chunk.text`), so it is not
  mapped; `page` comes from `doc_items[].prov[].page_no` and is **omitted, never defaulted to 0**
  — a wrong page in a citation is worse than no page.
- `estimate_tokens` is now cl100k_base, not `len/4`. Honest about what that means: it is not the
  embedder's own tokenizer, but `len/4` was undercounting **Tamil and Devanagari by ~3×**, which
  is the market docs/11 §9.2a already flags as underserved. Degrades to the old heuristic if
  tiktoken cannot download its ranks — a *counter* must never fail an ingest.
- Infra: `POSTGRES_HOST_PORT` (5432 is taken on this machine by another project; pointing
  Vicero at it would have run `alembic upgrade head` on someone else's database). `numpy<2.5`
  is a **typecheck** pin, not a runtime one — its 2.5 stubs use 3.12 syntax and under
  `python_version = "3.11"` (what the Dockerfile ships) mypy stops checking our code entirely.
- **⚠️ K3-2: email was already ingesting, months before anything "enabled" it.** docs/14 gates
  email on Docling's `format-email`. But there was **no upload validation at all** — only
  "is the file non-empty" — and `load_bytes` ends in *"anything else → decode as text"*. An
  `.eml` is RFC-822 text, so threads went in whole: headers, signature blocks, direct dials,
  every third party copied in. docs/14 §3.5 calls email the highest-PII-density format there is
  and docs/11 §6's review workflow still does not exist. Uploads are now **deny-by-default**
  (ADR-066) with three outcomes — accepted / gated / unknown — matching email on extension
  **and** `message/rfc822`, since a saved thread is often `thread.txt`. A test pins that the
  extractor still reads an `.eml` in full, so the gate cannot be dropped later on the belief
  that the path underneath is harmless.
- **K3-1 landed inverted:** the task says *widen* upload validation; widening an allowlist that
  does not exist means writing one. EPUB/ODF/LaTeX are deliberately **not** accepted — they need
  Docling, which is off, and accepting them would hand a client a `ready` document that is
  mojibake and retrieves nothing. **K3-3 to K3-6 not started**, blocked on the same root cause as
  K1-5 (no running docling-serve, no real client audio); K3-4's own criterion is "tested, not
  assumed", and there are no media jobs to test with.
- **⚠️ K5-3 was a live retention bug that K1 introduced earlier in the same session.**
  `delete_document` removed `storage_path` and nothing else, so a deleted document left its
  `.docling.json` — its full text, element by element, including anything `pii_flags` flagged —
  on disk forever. A second orphan came from re-ingest: losing structure cleared the column while
  the file stayed, and deletion works *through* that column, so nothing would ever remove it.
  Policy: **the JSON's lifetime is the document's**, no expiry job — a time-based one would turn
  a free re-chunk of a *live* document into a full re-conversion.
- **K5-2:** `MAX_PDF_PAGES` (default 800, 0 disables), enforced where the upload, URL and
  re-ingest paths converge. The message names the count *and* the limit, because "too large" is
  not actionable and "620 pages, the limit is 800" is. `pdf_page_count()` returns `None` rather
  than raising on a corrupt file — a page cap must not be what decides an encrypted PDF fails.
- **K4-4 blocked** (needs a running rerank endpoint for the p50/p95 delta ADR-063 demands before
  enabling it for anyone). K4-1/2/3/5 and K5-1 shipped 2026-08-13.
- ADR-064/065/066. Migration 0020. `make rechunk` (re-chunk from persisted JSON, no re-conversion).

### 2026-08-13 — docs/13 executed: the eval harness went first and immediately found two bugs
- **`docs/13-AI-COOKBOOK-REVIEW.md` implemented in its own §5 priority order.** R2 (eval harness),
  P0-1 (FTS index), R1 (reranker), R4b (per-KB FTS language) shipped. **R3 (Docling) and R5
  (tool-call approval) deliberately not started** — see docs/13 §8 for why each was sequenced out
  rather than narrowed away.
- **⚠️ The hybrid retrieval this product advertises was dense-only in production.** `_fts_hits`
  built its query with `plainto_tsquery`, which joins every lexeme with `&`. A customer types a
  sentence, so "ordered a kurta to delhi last week and it doesn't suit me, how long have i got"
  became nine ANDed stems and matched no chunk: **NDCG@10 0.0278, one query in thirty-six.** With
  one non-empty list, RRF had nothing to fuse and `hybrid` scored **byte-identically to `dense`**.
  docs/13 §1 called that stage "done". Fixed to an any-term tsquery (ADR-062) → **0.6604**.
- **⚠️ Fixing it then made `hybrid` regress below `dense`** (0.8977 → 0.8162) — textbook RRF
  weights both lists equally and these two are not comparable. Now weighted (ADR-058, default
  **0.05**). **That default is a floor chosen to avoid shipping a measured regression, not a fitted
  optimum**, and the ADR says so: per-query, fusion beat *both* retrievers where the keyword list
  had signal (q001 0.37/0.52 → 0.92). A relative `ts_rank` floor was built and then **deleted** —
  the noise is at the top of the keyword list, not its tail, and it moved NDCG by ≤0.01.
- **⚠️ The corpus had a hole and the first weight sweep found it.** Every query was written with
  deliberately low lexical overlap, which is a fair test of dense retrieval and a **rigged one
  against keyword search** — so the sweep "proved" hybrid was worthless at every weight. Ten
  exact-identifier queries (order refs, decline codes, style codes, form numbers) were added
  before any constant was tuned. **Do not fit a production constant to a benchmark you have not
  checked for blind spots.**
- **P0-1 is measured, not asserted**, which is what docs/14 §0 asked for. PostgreSQL 16.14, with
  `enable_seqscan=off`: literal regconfig → `Bitmap Index Scan on ix_chunks_content_fts`; bind
  parameter under `force_generic_plan` → `Seq Scan (cost=10000000000.00..)`. The custom-plan
  escape hatch only covers a prepared statement's first ~5 executions, and asyncpg pools
  connections — so production graduates to the generic plan and scans the whole table.
  `make explain-fts` prints both and exits 1 if the literal form stops matching.
- **⚠️ docs/13 R4b and docs/14 §5.4 are both wrong that Postgres ships no Tamil/Hindi dictionary.**
  PostgreSQL 16 ships both, and `tamil` genuinely stems (`கொள்கைகள்` → `கொள்கை`) rather than
  aliasing `simple`. Corrected in code with a test pinning it. Migration 0019 adds
  `knowledge_bases.fts_config` **plus a per-config GIN index** — changing the regconfig without one
  is P0-1 reintroduced through the front door.
- **The reranker is built and enabled nowhere** (ADR-063). Platform key never `resolve_credential()`
  (the ADR-055 rule), off platform-wide *and* per agent, fails open to RRF order, own metrics
  bucket. docs/14 K4-4 wants a measured p50/p95 delta on a real deployment first.
- **⚠️ The seed eval corpus was hand-authored in the same session as the code** — the exact
  unfalsifiability docs/11 Phase D exists to remove. Stated in `app/rag/evaluate.py` §2 rather than
  implied away. Generating one from a real client KB is the outstanding follow-up.
- ADR-058/061/062/063. Migration 0019. Suites: **pytest**, **181 vitest**, ruff + mypy + tsc +
  eslint clean. CI gains two retrieval steps, separate from the main suite for Phase D's reason.

### 2026-08-10 — first live run of docs/12; four fixes, two of which needed a second half
- **`docs/12-AGENT-TEST-CHECKLIST.md` run end to end against the published `aurozenai` agent**
  over the real widget endpoint: 61 items, **45 pass · 6 partial · 8 fail · 2 N/A**. Results and
  evidence in docs/12 §13 (run log) — that section is the thing to read before re-running.
- **⚠️ Checklist 12.3 was worse than "session resume": any visitor could post into any
  conversation.** `_get_or_create_conversation()` checked a resumed conversation's agent, org and
  channel and never *whose* it was, and the whole prior transcript is then loaded into the model's
  history — "summarise what we discussed" reads it back out. A `conversation_id` is not a secret.
- **⚠️ Shipping only the server-side check would have broken every anonymous chat.** The widget
  sent no `visitor` at all, so `visitor_id_for()` minted a fresh `anon-…` per request and nothing
  could own its own conversation. **Checked against the live DB before writing the check** — real
  traffic is `anon-` ids, and one conversation had already continued past its first turn purely
  because nothing looked. The widget now keeps a visitor id in `localStorage` beside the
  conversation id, and drops a stored conversation on a 404 so shared devices, deleted threads and
  every pre-fix conversation degrade to "start a fresh chat" instead of an error bubble.
- **⚠️ A 404 cannot be returned from inside a `StreamingResponse`** — the status line is already on
  the wire, so Starlette aborts mid-body and the caller sees a truncated 200. **The new test caught
  this in my own fix**, on the exact transport the widget uses. Resolution moved to
  `resolve_turn()`, awaited in the router before any response starts.
- **Checklist 4.5 was not a code bug.** `public_contacts` was `[]` for **all 12 orgs** — correctly
  read as "share nothing", never populated. **A new client is not configured until their contacts
  are published**; it presents as a model quality problem, not a settings gap. The widget path
  (`inbound.py::_pii_allowlist()`) had no test coverage at all — only the dashboard path did — and
  now has both directions plus two cases pinning that the allowlist is read *per turn*.
- **⚠️ `public_chat_once()` returned pre-guard text.** It summed `token` events and never read
  `replace`, so `{"stream": false}` served unredacted PII while the *persisted* message was
  correct — invisible afterwards. It landed before the 4.5 work because four of those allowlist
  tests failed on this bug rather than on the allowlist.
- **⚠️ A long-lived WS session pinned every row it had loaded.** `expire_on_commit=False` + one
  session per socket meant `session.get()` answered from the identity map forever: the org's
  allowlist, its guard toggles and the agent's published version were frozen per connection.
  `session.expire_all()` per frame. HTTP was never affected (fresh session per request), which is
  why it only bit the transport with no coverage.
- **Checklist 1.6 slipped every layer**: L1 was anchored on the possessive ("*your* instructions"),
  and "the rules you operate under" uses a relative clause instead. Fixed at L1, bounded by grammar
  rather than vocabulary — a product noun attaches with a preposition ("rules **for** the trial"),
  the assistant's own rules with a relative clause — 6 regression + 6 benign near-miss fixtures,
  still 0 false positives. **L2 scored that message 0.0011 and that is recorded, not closed**
  (docs/11 §9.2a): L1 catching this family does not make L2 better at paraphrase.
- **⚠️ docs/11 §9.2 says Tamil scores 0.9993; the string used in this run scored 0.0104.** Same
  model, different sentence. Read as: non-English scores are phrasing-sensitive, and one probe
  cannot support "Tamil is covered" in a market where Tamil is a first language.
- **⚠️ Still failing, deliberately or unfixed:** 4.2 (founder's number is in the live KB — docs/11
  §6 operator work — and L5 corrects it only *after* it has streamed, which needs a latency
  decision, not a patch); 7.2/7.3 (abuse aimed at the agent raised no flag live while the
  classifier returns `abuse=True` on a direct re-run — **§7/§8 results are one sample each, run
  them three times**); 12.2 (no per-agent embed domain restriction); 10.3 (`web_search_monthly_quota`
  declared and read nowhere).
- **⚠️ Running the checklist exhausts the Groq free tier.** 61 probes hit the daily token cap on
  the 70B model, and L3's safeguard model then 429s — which silently stops distress grading
  mid-run while dashboards look normal. Pace ~25s per turn; check `guard_l3_unavailable` before
  trusting any §7/§8 result. No org holds its own provider credential, so every client and both
  guard models share one platform key.
- 4 fixes, 4 commits (+1 pre-existing lint chore). Suites: **773 pytest**, **175 vitest**,
  ruff + mypy + tsc + eslint clean, 3/3 Playwright widget checks (2 new).

### 2026-08-04 — docs/11 Phases E, F, G shipped; the safety track is code-complete (not "solved")
- **Gate first:** re-ran the Phase D corpus before starting E rather than trusting the previous
  session's report — 175 passed, committed at `3893dda`.
- **⚠️ TWO THINGS NEED HUMAN REVIEW BEFORE THEY GO LIVE.** Both are drafts written by this
  session, both are read by customers or decide who gets escalated, and a test asserts the
  `DRAFT` marker stays until someone removes it:
  1. `policy_guard.CRISIS_HOLDING_MESSAGE` — what a person in genuine distress reads instead of
     a generated reply. Deliberately static (an 8B model must not improvise here) but static is
     not the same as correct. It names **no helpline**: the right number is country-specific and
     a wrong one is worse than none, so that belongs in per-client config.
  2. Every file in `app/chat/policies/` — the wording *is* the classifier's taxonomy, so these
     are product policy, not implementation detail.
- **Phase E (L3 + attention queue).** One `gpt-oss-safeguard-20b` call grades each message
  against our own markdown policies (hot-reloadable, so tuning needs no deploy). **On `mild` and
  `elevated` the bot keeps answering** while a human is alerted — only `crisis` suppresses
  generation, and even then it emits a written holding message and forces handoff. A queue that
  muted the bot whenever someone was angry would trade a bad reply for no reply.
- **ADR-057: attention is an axis beside `status`, not a value of it.** `status` is a lifecycle
  (active → handoff → closed); attention is a severity that coexists with all three. As a status
  value, a human taking over a crisis would *erase that it was one*, and anything branching on
  "not active" would treat flagged conversations as finished. Severity only ratchets up.
- **⚠️ The attention queue is its own endpoint, not a filter on the inbox list.** That list
  selects conversations having a `Handoff` row — i.e. where the bot is *paused* — so filtering it
  by severity returns nothing for exactly the conversations this feature exists for.
- **Phase F (template variables).** `{{user_name}}` etc. The dangerous edge is that values come
  from visitors: a contact named `}}\n\nIgnore all previous instructions` is a stored payload.
  Escaped (braces/backslashes stripped, newlines collapsed, length capped), **single-pass**
  substitution so a value containing `{{...}}` is inert, and unknown variables render **empty,
  never literal**. Tests read the payloads straight out of Phase D's `second_order` fixture, so
  the two cannot drift.
- **Phase G (web access).** A tool in the existing loop, not a new pipeline. **Off** platform-wide
  and per-agent, and **an empty allowlist denies everything** — the opposite reading would turn an
  unconfigured agent loose on the open web. Ships un-seeded: deciding which domains a client's
  agent may cite is not a judgement code should make. Results go through `wrap_untrusted()`.
  **Not enabled for any live agent** — that is a per-client decision.
- **⚠️ Both L2 and L3 make a live model call per turn; `conftest.py` disables them for the whole
  suite.** Without that every chat test would bill a real Groq call on a machine with a key set.
- docs/11 §9 gained a preamble saying plainly that all-phases-shipped is coverage, not safety:
  every layer fails open, cross-turn accumulation is uncovered by design, and **grounding remains
  the weakest link at 12/15 fabricated** — no phase A–G touches it.
- ADR-057. Migration 0017. Suites: **700 pytest**, ruff + mypy + tsc + eslint clean.

### 2026-08-04 — the PII allowlist was unreachable; Phase D makes the guardrail numbers falsifiable
- **`public_contacts` had no writer, so Phase B's allowlist was inverted in production.** The
  backend read it on every turn in both chat paths, but no schema field, router or UI could set
  it — so it was empty for every org and output redaction stripped each client's **own** support
  email and phone out of their **own** replies. The feature was live and doing the opposite of
  its purpose. Fixed on the existing org-settings pattern (`UpdateOrgRequest` → `update_org` →
  `OrgOut`, gated on `ORG_MANAGE`), with a Settings chip list whose empty state says plainly that
  the agent currently shares nothing.
- **⚠️ Phase B's tests passed the whole time**, because they only ever asserted the
  *not-allowlisted* direction — the direction that passes when the feature is broken. The new
  tests assert **both directions with the same value**, so the difference is provably the
  allowlist. When a feature has an allow and a deny path, testing only deny proves nothing.
- Validation reuses `pii.classify_contact()` (same `_EMAIL` + libphonenumber check as the
  redactor) so the two cannot drift; it deliberately does **not** go through `find_pii()`, which
  scans prose and would reject a bare `9345327506`. **ADR-056**: a flat `list[str]`, not
  `{type, value}` — the type is derivable and storing it invites disagreement — and a URL is
  **rejected** rather than stored inert, since redaction only acts on emails and phones.
  Provisioning now seeds the owner's email so a new org is never born broken.
- **Phase D — the corpus that can go red.** Every recall figure through Phase C was measured
  against probes written in the same session as the code, i.e. unfalsifiable. Now **89 cases in
  5 files**, split by the layer that actually owns each: `attacks`/`benign` (L1),
  `attacks_multilingual` (L2), `attacks_output` (L5), `attacks_contextual` (indirect /
  second-order / multi-turn). Gated in CI **as its own step** — buried in 600+ tests, "1 failed"
  reads as flake.
- **The six live failures do not all belong to the same layer**, which is why filing them all as
  input attacks would have been wrong: 1 and 2 are L1; **3 ("can i get your number") is a
  perfectly reasonable question** whose *reply* was the failure, so it sits in `benign.yaml`
  (must never be blocked) with its regression case in `attacks_output.yaml`; 4 is grounding
  (needs a live model); 5 is corpus hygiene (§6); 6 is L5.
- **⚠️ The corpus failed twice on its first run — both times on fixtures I had written wrong**
  (a mislabelled category, a duplicated id). That is the point of it: a corpus that has never
  failed has not been tested either. The duplicate-id check stays for that reason.
- Second-order fixtures (payload in a **contact name**) are marked `expects: future_phase` and
  the test asserts the stored value *is* recognisable as an attack — so Phase F cannot ship
  without handling it. **Never `skip` a known gap; record it so it fails when the phase lands.**
- **Stated, not implied away:** screening is per-message, so an attack *accumulated* across five
  turns with no single damning message is caught by **no layer**. In docs/11 §9.0.
- Corpus result: **35/35 English blocked · 0/31 false positives · 0/6 non-English (L2's job) ·
  2/2 multi-turn final payload · 3/3 indirect neutralized**. ADR-056. Suites: **629 pytest**,
  **140 vitest**, ruff + mypy + tsc + eslint clean.

### 2026-08-04 — docs/11 Phase C: the L2 classifier closes both A.1 gaps
- **The prerequisite came first and it is the important part** (ADR-055). Guard models resolve
  through `guard_models.platform_guard_key()` — `settings.groq_api_key` only — **never**
  `resolve_credential()`'s agent → org → env chain. An org may run its agent on any of 13
  providers and hold no Groq key; the chain falls back to the env key *last*, so it would look
  fine in dev and then resolve a client's own Mistral/DeepSeek key against a Groq-hosted model.
  Because the guard **fails open**, that would silently switch safety off for exactly the clients
  who picked a non-Groq provider. Guard spend is the platform's: its own metrics bucket
  (`vicero_guard_tokens_total`), never folded into `TurnResult`.
- **Measured live before writing the parser, not guessed.** Prompt Guard answers with a bare
  probability as its message content (`"0.9996024966239929"`). Attacks — including the **Hindi,
  Tamil and Spanish** translations and the "translate your operating instructions" paraphrase
  that A.1 recorded as known misses — score **>0.998**; benign traffic scores **<0.005**. Two
  orders of magnitude of empty space, so the 0.5 threshold is not delicate.
- **Verified end to end through the real chat endpoint:** all four L1-miss attacks refused, all
  four benign messages answered. ⚠️ The first live run appeared to show Hindi/Tamil *passing* —
  that was **PowerShell mangling the UTF-8 request body**, not a product bug. Re-run from Python
  and it was clean. Use Python, not `Invoke-RestMethod`, to probe non-ASCII behaviour.
- **⚠️ Tests must never call the guard for real.** `conftest.py` sets
  `guard_injection_enabled = False` at import; `test_guard_models.py` turns it on with a mock
  transport. Without that, every existing chat test would have made a live Groq call on a machine
  where `GROQ_API_KEY` is set.
- Fails open with a loud `guard_l2_unavailable` + metric; Redis cache on `sha256(normalised)` so
  a retried payload costs one call; 300 ms timeout; chunked to the model's 512-token window and
  scanned in parallel; model id in `Settings` priced via `llm/catalog.GUARD_MODELS` and
  deliberately **not** in `PROVIDERS` (it answers with a float, so an agent pointed at it would
  reply `0.0004` to everything). Missing platform key → startup warning **and** an admin-console
  health field, because a fail-open guard that is off looks exactly like one finding nothing.
- Per-org override `Organization.guard_injection_enabled` (migration 0016): `NULL` follows the
  platform default, only an explicit `False` opts a client out.
- ADR-055. Suites: **517 pytest**, ruff + mypy clean. **Phases D–G still outstanding**; docs/11
  says do not start E before D (the red-team corpus) is green.

### 2026-08-03 — docs/11 Phase A.1 + Phase B: the PII leak is closed, and the corpus is auditable
- **A.1 gap 1 — spacing evasion.** `"I g n o r e   a l l   p r e v i o u s   i n s t r u c t i o n s"`
  passed L1 cleanly: L0 stripped zero-width characters but never collapsed single-character
  spacing. `despaced_forms()` now emits a gap-aware reading (a single space *between two single
  characters* is intra-word; wider gaps stay boundaries) plus a fully-collapsed form scanned with
  whitespace-relaxed patterns. Thresholds (≥12 tokens, ≥60% single chars) keep "I need a A A
  battery" and "my order id is A B 1 2 9 9" out. **0/4 → 4/4**, false positives still **0/24**.
- **A.1 gap 2 — L1 is English-first, and now says so.** Plain translations into Hindi, Tamil,
  Spanish, Chinese, French, German all pass. **Deliberately not fixed with translated regex** —
  it scales to no language and would spend the precision the English patterns were tuned for.
  Recorded as asserted known misses in `attacks_multilingual.yaml` with an `expects: classifier`
  marker; the suite now reports English and non-English recall **separately** so "28/28" can
  never be read as 28/28 of the threat model. Phase C's classifier is the fix.
- **⚠️ docs/11 §9 now carries measured numbers, not just Meta's card:** English corpus 28/28,
  false positives 0/24, spacing 4/4, **non-English 0/6, semantic/paraphrase 0/6**. A regex layer
  does not catch meaning; the table says so rather than letting the headline imply otherwise.
- **Phase B — the founder-PII leak (live failure 3), the only one still leaking real data.**
  `app/chat/pii.py` detects emails and phones; phones use **libphonenumber** (ADR-053) because
  a US-centric regex misses `+91 …` outright and a permissive digit-run regex redacts order
  numbers out of real answers. Redaction is **allowlist-based** against a new
  `organizations.public_contacts` (migration 0015) — an agent that cannot give out its own
  support address is broken, not safe. Redacted spans read as "our contact page", never
  `[redacted]`. Ingest scanning **flags and never rewrites or blocks** (ADR-054): a client's own
  contact page legitimately contains contact details, and mangling their KB is worse than the
  leak. `pii_flags` is nullable — NULL = never scanned, `{}` = scanned clean — and the UI keeps
  them distinct.
- **⚠️ The audit script found a detector bug that every hand-typed fixture had missed.** Run
  against the live DB, `scripts/audit_kb_pii.py` reported `email=2` but **no phone** — yet the
  KB contains one. The corpus is PDF-extracted: the ☎ glyph arrived as `\x01` and the number's
  internal spacing as **tabs**, and libphonenumber matches **nothing** in that text. So B1
  shipped believing it worked. Fixed with a **length-preserving** cleaned copy (control chars,
  tabs, NBSP → exactly one space each, so offsets still index the original and redaction slices
  the right span; newlines kept, or a number could span two lines). Audit now reports
  `email=2, phone=2`. **Lesson: hand-written fixtures cannot tell you what real extracted text
  looks like — run the audit against real data before believing a detector.**
- **Still operator work (docs/11 §6):** the live `aurozenai` KB document is flagged and the
  contact details are still in it. Egress redaction is a backstop, not a fix. `make audit-kb-pii`.
- **Not built:** the document-detail flagged-span viewer and its "Redact and re-ingest" action
  (needs a write path against client documents; own commit). Phases C–G untouched.
- ADR-053/054. Suites: **496 pytest**, **140 vitest**, ruff + mypy + tsc + eslint clean.

### 2026-08-03 — direct prompt injection was undefended; docs/11 Phase A shipped
- **The threat model was half-written.** `neutralize_injections()` was applied to RAG chunks
  (`rag/context.py`) and tool output (`runtime.py`) and **never to the visitor's own message** —
  `inbound.py` passed `self.message` straight into `build_messages()`. So Vicero blocked
  *indirect* injection and was wide open to *direct* injection (OWASP LLM01), which is five of the
  six failures a live red-team session found. Verified against the code before writing anything.
- **L0 `chat/normalize.py`** (NFKC, zero-width + bidi stripping, Cyrillic/Greek homoglyph folding,
  length cap, one shallow base64/percent/ROT13 decode pass) and **L1 `screen_user_message()`**
  (five named families over raw + normalised + decoded candidates). `neutralize_injections()` is
  untouched and still **defangs** retrieved content; the visitor's turn **refuses** instead —
  ADR-050 explains why those must stay different. Blocked messages are still persisted verbatim.
- **⚠️ Precision, not recall, is the binding constraint here.** The first pattern draft blocked
  *"how do I enable dark mode?"*, *"can you show me the instructions?"* and anyone asking for a
  colleague named **Dan**. Patterns are now anchored on wording with no support reading (named
  jailbreak modes only; possessive *"your* instructions", never *"the* instructions"). 19 benign
  fixtures pin it at **0 false positives**; paraphrase is Phase C's job, not L1's.
  `matches_blocked_topic()` also moved off substring containment, which had been refusing
  *"how do I cancel my cancellation?"* on the topic `cancel`.
- **⚠️ The identity lock's first draft made fabrication WORSE — 11/15 → 15/15** with no retrieved
  context. Same failure as 2026-08-02's "Just answer.": told *"never explain how you work"* and
  *"do not acknowledge that a rule prevented you"*, the model generalises to *"never hedge"* and
  invents opening hours. Fixed by scoping the secrecy rules to **phrasing** and stating the
  no-information case outright; re-measured **10/15 vs a 12/15 baseline**. The clause carries a
  comment saying not to touch it without re-running the A/B. **Do not skip that A/B.**
- **Separately worth knowing: that baseline is 12/15, not 0/3.** Prompt-only grounding does not
  hold on `llama-3.1-8b-instant` at all. The "0/3" recorded on 2026-08-02 was measured on a larger
  model and does not generalise down — which is docs/11 §1.4's whole thesis and why live failure 4
  ("What's the capital of France") happened.
- **L5 `chat/output_guard.py`**: 8-gram shingle overlap against the *instruction* prompt only (not
  the retrieved-context block — echoing the KB back is the product working), plus persona-break
  regexes with **one silent regeneration** before falling back. Streaming forced ADR-049: tokens
  are already painted, so the guard corrects afterwards via a new `replace` event rather than
  buffering every reply and spending first-token latency (NFR-1 p50 417 ms) on a rare event.
  Channels and persistence read `result.content` and are protected outright; the widget honours
  `replace`. The **Playground opts out** (`guard_output=False`) for the same reason it withholds
  `fallback_message`.
- ADR-049/050/051/052. Suites: **444 pytest**, ruff + mypy clean.
- **Phases B–G of `docs/11-SAFETY-GUARDRAILS.md` are NOT built.** B (PII egress + ingest scanning,
  which is what actually fixes the founder-PII leak), C (Prompt Guard 2 classifier), D (full
  red-team corpus), E (distress detection + attention queue), F (template variables), G (web
  access). The operator's own KB clean-up (docs/11 §6) is still outstanding and no code replaces it.

### 2026-08-03 — provider keys are managed per provider, and the Model tab only offers what runs
- **The builder offered providers the org had no key for** (`5418b39` backend, `64db9cb` frontend,
  **ADR-047/048**). The Model tab rendered `providerCatalog`, a hardcoded list in
  `lib/mock/builder.ts`, so all seven appeared whether or not a key existed — the obvious way to
  configure an agent was to select one that could not answer, and it failed later as a dead agent
  instead of then and there. It was stale too: still offering Groq's retired
  `mixtral-8x7b-32768`. Settings → Provider keys is now a grid of every provider (click → paste
  key → save, one key per provider via `PUT /v1/credentials/providers/{name}`), and the Model tab
  lists only providers with `configured: true`.
- **Model lists are a seed, not the truth.** `app/llm/catalog.py` is the one source (providers,
  models, endpoints, pricing) but where a key exists `GET /v1/credentials/providers/{name}/models`
  asks the provider. Live verification justified it on the spot: the real Groq account returned
  `qwen/qwen3.6-27b`, `groq/compound`, `allam-2-7b` — none seeded — and several seeded ids were
  absent. Discovery failure returns `source: "catalog"` + the reason, **never a 5xx**; an
  unreachable provider still has to render a usable dropdown.
- **Six providers added with no adapter** (Mistral, DeepSeek, xAI, Together, Fireworks, Cerebras):
  they speak the OpenAI wire format at a fixed `base_url`, so each is one catalogue entry. All
  BYO-key — **no new env vars**.
- **⚠️ Two things must never be silently rewritten.** `configured` means "will a turn work?", not
  "is there a credential row" — a platform **env** key counts, which is how the live Groq agents
  run with no row at all; filtering on rows would have hidden the provider they already use. And
  an unavailable provider/model stays **selected and selectable** (flagged `no key — this agent
  cannot reply` / `(not offered)`), because dropping it leaves the `<Select>` unmatched and the
  debounced autosave then persists a provider nobody chose.
- **Two quiet-wrong-number fixes:** pricing is `None` when unpublished rather than `0`, with a
  `pricing_unknown` log for a paid model with no rate ($0 in a cost report is wrong, not free);
  and provider errors are stripped of key-shaped runs before reaching the client — a rejected key
  comes back as `Incorrect API key provided: sk-live-*******8888`.
- Deleted `providerCatalog` (the ADR-041 follow-up) and the unused `providerLabel` map.
  `vitest.setup.ts` gained ResizeObserver/pointer-capture stubs — jsdom has neither and every
  Radix Slider/Select test failed on render, not on its assertion.
- Suites: **430 pytest**, **131 vitest**, ruff + mypy + tsc + eslint clean, 3 new Playwright checks.
- **Note:** built alongside a concurrent session implementing `docs/11-SAFETY-GUARDRAILS.md`
  Phase A in the same working tree. Commits were path-scoped (`git commit -m … -- <paths>`) so
  neither swept up the other's in-flight files.

### 2026-08-02 — a `.env` placeholder comment was being sent as an API key (silent live outage)
- **The live agent served empty replies with HTTP 200 to every visitor**, for an unknown period.
  Log: `...?key=%23+%5BHUMAN%5D+Google+Gemini+free+tier` — the placeholder comment *was* the key.
  `.env.example` writes unset vars as `KEY=<spaces># [HUMAN] note`; python-dotenv strips a trailing
  comment via `re.sub(r"\s+#.*", "", value)`, which needs whitespace **before** the `#`, but on a
  blank line the spaces after `=` are eaten as the separator, so `#` lands at position 0 and the
  comment becomes the value. **Probed before fixing: `KEY=dev # note` → `dev` (fine); only the
  blank-value shape breaks.** Non-empty, so it sailed past the blank-is-unset guard (ADR-020).
  `SENTRY_DSN` was failing identically; ~20 more `[HUMAN]` placeholders were one edit away.
- **Two fixes, ADR-044.** `Settings` gained a `model_validator(mode="before")` dropping
  comment-only values → **every existing `.env` is correct with no hand-editing**. `.env.example`
  reformatted (comment on its own line above each var) because `.env` also goes to containers via
  compose `env_file`, which no Python validator reaches. A test pins the file shape.
- **⚠️ Do NOT "just strip everything after the first `#`".** Measured against the real loader that
  turns `SECRET_KEY=abc#def` → `abc` and `http://x/y#frag` → `http://x/y`. Keys, DB passwords and
  URL fragments contain `#`. The narrow rule's only false positive is a secret *starting* with `#`,
  which degrades to "not configured" — loud, not silent.
- **Why it stayed invisible:** a `ProviderError` left `content` empty and `public_chat_once` only
  accumulates `token` events, so the error was dropped and the widget returned an empty 200.
  `run_turn` now takes `fallback_message` and emits it as a real token on provider failure — wired
  into `InboundTurn` (widget + channels), deliberately **not** the Playground (operators want the
  raw error). `result.error` still set; the provider message never reaches the visitor. New
  `malformed_key` startup warning for a key with whitespace or `#`.
- Suites: **349 pytest**, ruff + mypy clean.

### 2026-08-02 — replies stopped reading like a citation list; agents now start from a role
- **The research-paper voice was one clause** (`b25c1c6`, **ADR-045**): `build_context_block()`'s header told the
  model to "cite sources as [n] when relevant", so customers got "According to the documents [1]
  and [2]…". The numbering is *our* bookkeeping — the caller returns the same citations as
  structured data for the widget to render — so the header now forbids visible markers outright.
  System prompt held constant: old header **3/3** replies with `[n]`, new **0/3**, and a live call
  still returned `citations: 1`. The structured citation payload is untouched.
- **Live client agent republished** (operational, no commit): "aurozen ai"'s *stored* prompt — the
  one real visitors get — carried its **own** citation instruction ("briefly reference the relevant
  document or section") plus an explicit grounding escape hatch ("unless the user explicitly asks
  for general knowledge"). Rewritten with the ADR-045 clauses, applied to draft **v5** and
  published, which also moved the live model **gemini → groq** (v4/v5 were otherwise identical, and
  gemini was the provider hit by the key bug below). v4 stays published, so rollback works. Note
  `DEFAULT_SYSTEM_PROMPT` was never this agent's prompt — editing code would not have fixed it.
- **Role templates at creation time** (`a557418` backend, `a22d1ff` frontend, **ADR-046**): four prebuilt roles
  seeding the first draft's prompt/welcome/suggested prompts/tone/model, plus "Start from scratch"
  which is unchanged. Catalog is static data in `app/db/templates.py` (a fifth role is one entry —
  no schema change, no migration), served by `GET /v1/agent-templates`, applied via optional
  `template_id` on `POST /v1/agents`. Where a role needs a KB/CRM/calendar the builder shows a
  **suggestion banner, never an auto-attached tool** — a silent integration with no credentials
  behind it makes agents that look configured and fail at runtime.
- **⚠️ The tone fix caused a real accuracy regression; read this before touching a prompt**
  (`e088101`). Live, the agent invented "Mon–Fri, 9am–5pm" and a 30-day refund window against a KB
  saying Mon–Sat 10am–7pm IST / 14 days, `citations: 0` — retrieval had **legitimately missed**
  (0.0318 vs the 0.35 threshold) so **no context block was appended at all**. A/B on that exact
  state: **old prompt 0/3 fabricated, rewritten prompt 3/3.** Told only "never mention the
  documents", the model reads it as "don't hedge"; the rewrite's trailing "Just answer." made it
  explicit. The same A/B then caught a second instance in the `customer_support` template, whose
  "resolve it in as few messages" body overpowered the softer shared `_GROUNDING`. Fixed in both:
  drop "Just answer.", state the no-context case outright, scope never-narrate-your-sources to
  *phrasing*. Now **0/3** fabricated across the default prompt and all four templates, and still
  answering correctly from a real `build_context_block()` with **0/3** markers. **Never soften
  these lines without re-running the no-context A/B** — a unit test cannot see this.
- **App-wide dialog bug found on the way** (`203984a`): `animate-fade-up` ends on
  `transform: translateY(0)` with fill-mode `both`, permanently overriding `DialogContent`'s
  `-translate-*` centering — **every** dialog was anchored *at* the viewport centre, not centred on
  it. Small ones still fit, so it hid for months; the taller role picker got clipped and its
  "Start from scratch" option was unclickable. Now `inset-0 m-auto h-fit` + a scroll cap.
- Suites: **340 pytest**, ruff + mypy clean, **124 vitest**, tsc + eslint clean, **60/60 Playwright**.

### 2026-07-31 — Playground answered "echo: …"; a missing key did the same to real customers
- **Root cause was environmental, not code.** The dev web server was pointed at `:8010` — the
  keyless **E2E** API, which runs `LLM_FORCE_FAKE=true`. `get_chat_provider()` checks that flag
  *before* any per-agent config, so every agent answers with the stub echo. Proved by asking the
  same agent the same question on both ports: `:8000` → grounded Groq answer with citations,
  `:8010` → `echo: …`. The agent's stored config was never `fake` (published v1–v3 are Groq).
  Fixed by repointing the web at `:8000`; verified with a real multi-turn conversation.
- **The serious find, live in production code:** `conversations/service._resolve_provider` caught
  provider-resolution failures and substituted `FakeChatProvider()` — so an expired or revoked
  API key would answer **every visitor on a client's site** with `echo: <their own words>`. Now
  returns `RefusalProvider(fallback_message)` + logs `chat_provider_unavailable` at error level.
  The Playground re-raises the typed `llm.provider_unavailable` instead of stubbing, and
  `LLM_FORCE_FAKE=true` now announces itself loudly at startup (error-level under prod).
- **Latent bug it exposed:** `provider: "fake"` never actually resolved (`fake` isn't in
  `PROVIDER_CATALOG`, so it hit the requires-a-key branch) — it only worked because those same
  catch-alls swallowed the error. Resolved before the key check now.
- **⚠️ Needs a human:** the "aurozen ai" **draft** (v4) is `openai/gpt-4o` with no `OPENAI_API_KEY`,
  and the Playground always runs the draft — it will now say "No API key configured for 'openai'"
  rather than echo. Switch the Model tab back to Groq or add a key; published v3 is fine.
- Suites: **331 pytest**, ruff + mypy clean.

### 2026-07-31 — n8n visibility flipped to deny-by-default + staff automations console
- **The permissive default was wrong and live testing proved it.** ADR-040 kept "untagged =
  visible to every org" as a transition aid; a brand-new, empty org still saw every untagged
  workflow — other clients' automations and internal ones alike — because untagged is the state
  every workflow starts in. `workflow_visible_to_org` now grants access **only** on the org's
  slug tag or the new opt-in `shared-template`; internal tags beat both. Verified live: a fresh
  org gets `[]` where it used to get the whole list. ADR-042 supersedes ADR-040.
- **Provisioning tags at clone time**, using the org's real slug from the API (not
  `slugify(name)` — the backend suffixes on collision) and **before** the bind, since
  `/v1/tools/n8n/bind` applies the same rule and would refuse an untagged clone.
- **New staff console section** (ADR-043): `GET /v1/admin/automations` + an Automations table on
  `/admin` — every workflow across every tenant with tag-derived owner, active state and its
  bindings. Unowned rows sort first; it doubles as the tagging backlog. n8n being down returns a
  typed `error`, not a 500. Plus `scripts/tag-n8n-workflows.mjs` (dry-run by default, idempotent).
- **⚠️ The tagging itself is BLOCKED and nothing is tagged yet:** the n8n API key has workflow
  scopes but **403s on all tag endpoints**, so every workflow is currently invisible to every org.
  Bound tools keep working (the runtime uses the stored `webhook_url`), but nothing can be
  discovered or re-bound until a key with tag scopes is minted and the script re-run. `:5678` is
  a different instance entirely and 401s Vicero's key.
- Suites: **327 pytest**, ruff + mypy clean, 10 `node --test`, tsc + eslint clean.

### 2026-07-31 — logged out right after logging in (AuthGate treated an abort as a 401)
- **A navigation during session bootstrap logged the user out.** `AuthGate` runs
  `Promise.all([me(), listOrgs()])` on every page load under a bare `catch` that called
  `clearAuth()` + `/login`. Clicking a link while those are in flight **aborts** them, and an
  aborted `fetch` rejects with a `TypeError`, not a 401 — so "you navigated quickly" was read as
  "your token is invalid" and `bf_access`/`bf_org` were deleted. Traced live: both requests
  `net::ERR_ABORTED`, **zero 401s**, cookies gone. Now only `ApiError` status 401 ends a session.
- **Same fix applied to two sibling paths:** `tryRefresh()` cleared auth on any non-ok refresh
  (now 401 only), and the BFF refresh route dropped the httpOnly refresh cookie on any ≥400 —
  including a 5xx from a restarting API. It now separates a rejected token (401/400 → clear)
  from an unavailable upstream (→ 503, cookie kept), and no longer 500s when the API is down.
- **Exposed, not caused, by `fbf8543`** — wiring the dashboard to five real requests widened the
  in-flight window so an ordinary click landed inside it. PRD criterion 1 went 0/3 → 3/3 with no
  change to the test. 5 unit tests pin abort/503 ≠ sign-out.
- Suites: **320 pytest**, **116 vitest**, **54/55 Playwright** (the one failure is the known
  hardcoded-rate-limit flake — passes in isolation; see the roadmap).

### 2026-07-30 — mock data purged from the dashboard, profile and versions tab
- **Three screens rendered fixtures as real records.** `dashboard/page.tsx` imported
  `{ agents, recentConversations }` from `lib/mock/data` — a brand-new org saw four invented
  agents ("Support Concierge", "Sales Qualifier", …) and five conversations that never happened;
  `settings/profile/page.tsx` imported `currentUser`, showing **"Aadesh Sree / aadesh@aurozen.ai"
  to whoever was logged in** plus two fake devices; `versions-tab.tsx` imported `versions`,
  giving every agent the same four-entry history with dead Publish/Roll back buttons. All now
  read the API with a skeleton + a written empty state (zero-of-everything is the normal state of
  a freshly provisioned client — exactly what the fixtures hid). `lib/mock/data.ts` deleted;
  `builder.ts`'s `versions`/`makeDraft`/`knowledgeBases`/`tools` removed. See ADR-041.
- **Recent conversations reads `/v1/conversations`, not `/v1/inbox/conversations`** — the inbox
  is the *handoff queue*, so a bot resolving everything would look idle, and `viewer` lacks
  `inbox:handle` and would have hit a 403 where a fixture used to render.
- **Backend bug found on the way:** `GET /v1/auth/sessions` hardcoded `current=False`, so "This
  device" could never show. Access tokens now carry a `sid` claim; tokens without it degrade to
  the old behaviour, so rotation is unaffected. Profile name/email are **read-only** — there is
  no `PATCH /v1/auth/me`, and the old Save button silently discarded the edit (roadmap).
- **Flagged, not changed:** `providerCatalog` duplicates `GET /v1/credentials/providers` and can
  drift; switching it changes which models are selectable, so it wants its own commit (ADR-041).

### 2026-07-30 — n8n discovery scoped per org, real email delivery, one-command provisioning
1. **n8n multi-tenancy** (`6fead42`): `list_n8n_workflows` had **zero** tenant filtering — every
   org's Automations page listed every workflow in the shared n8n, platform-internal ones included
   ("SHARED — Master Router"), and could bind a tool to them. New
   `workflow_visible_to_org(tags, name, org_slug)`: tagged with an org's slug → only that org;
   tagged `internal`/`shared-internal`/`platform-internal` → hidden from everyone (fails **closed**);
   untagged → visible to all (permissive so existing untagged client workflows didn't vanish);
   `"SHARED — …"` treated as internal even untagged. Enforced at discovery **and** in
   `bind_n8n_workflow` by `workflow_id` (typed `tools.n8n_forbidden` 403), closing the
   bind-by-guessed-id hole. Binding by pasted webhook URL is deliberately **not** covered — ADR-040.
   Caught from a live screenshot, not a test: no test had two orgs' workflows in one instance.
   **The fix is inert until workflows are tagged** — audited live, 6 of 9 on the AUROZEN n8n
   (`:5678`) and all 3 on Vicero's own (`:5679`) are still untagged. See `docs/PROGRESS.md`.
2. **Email actually sends** (`898c68d`): `get_email_backend()`'s `"smtp"` branch logged
   `smtp_backend_not_implemented` and fell back to console, so no invite/verification/reset/magic-link
   had ever reached an inbox. `SmtpEmailBackend` over **aiosmtplib** + HTML templates; TLS mode from
   the port (465 implicit, else STARTTLS). Sending moved to an `email.send` Celery task — and measuring
   found `.delay()` blocks the loop and kombu retries a dead broker 20× (**30-second hung signup**), so
   the enqueue is threaded under a bounded 2s timeout and abandoned. Console mode stays **inline** (eager
   Celery would hit `asyncio.run()` inside the running loop — the §12 ingestion trap) which is what
   keeps the test outbox synchronous.
3. **One-command provisioning** (`86e0d3b`): `scripts/provision-client.mjs` → org + published agent +
   cloned/activated n8n automation bound as a tool + client invited as `editor`. Staff **login**, not a
   `bf_` key (org creation is gated on `is_staff`). Per-client webhook path — cloning the template
   verbatim would aim every client's tool at one shared URL. Two idempotency traps fixed: a pending
   invite would be re-sent (`create_invitation` only rejects *active* members), and re-patching an
   existing agent forked a draft every run **and would overwrite the client's own persona edits**.
   Dev `n8n` gained `N8N_HOST_PORT` + `N8N_DIAGNOSTICS_ENABLED=false` (it exits when
   `telemetry.n8n.io` won't resolve) and now runs on **5679**, isolated from the unrelated n8n on 5678.

Suites: **319 pytest**, **73 vitest**, **46/46 Playwright**, 8 `node --test`.

### 2026-07-29 — Edit/publish split, live widget config, invite acceptance
1. **RBAC split** (`59b8f6f`): new `AGENTS_PUBLISH` gates publish/rollback; `editor` is now the
   **client role** (edit, knowledge, own channel credentials, inbox, Playground — no publish).
   Builder shows "Awaiting review"; admin console gains an **Awaiting review** column per org.
2. **Widget config** (`60c91b2`): moved out of `AgentVersion.persona.widget` into an unversioned
   `widget_configs` table (migration `0014`). `_theme()` reads it by `agent_id`, bypassing
   `_live_version()`. New `GET/PATCH /v1/agents/{id}/widget-config` on `AGENTS_WRITE`, never
   `AGENTS_PUBLISH`. Legacy `persona.widget` writes are **routed** to the new store so old
   callers keep working instead of writing to a field nothing reads.
3. **Invite acceptance** (`0ef0b65`): the real bug — no frontend route called `accept_invitation()`.
   Page added at **`/invitations/accept`**, the path the emails already use (building at `/invite`
   would have stranded every already-sent invitation).

Suites: **297 pytest**, **73 vitest**, **46/46 Playwright**.

### 2026-07-29 — URL extraction fix, CRM rename + auto-capture
1. **URL ingest** (`49e66ba`): `strip_html()` was structure-blind, so a docs site's nav menu
   opened the extracted text and dominated the first chunk. Added **trafilatura** +
   `extract_main_content()`, `strip_html` kept as fallback. Real docs.n8n.io committed as a
   fixture; one test pins the *old* behaviour so the file records the bug.
2. **CRM rename + manual creation** (`d93b5ec`): nav/title → "CRM" (`/contacts` route kept),
   `POST /v1/contacts` with `channel="manual"` + synthetic `external_id`. `manual` added to
   `channel-meta.ts`'s `REPORTING_ONLY` so it can never become an Inbox tab.
3. **CRM auto-capture** (`a120b1f`): `CrmContact` = canonical person, `Contact` = per-channel
   handle linked via `crm_contact_id`; CRM fields moved up with a backfill (migration `0013`).
   Regex gate → one small-model call only when email/phone-shaped text exists. **Matching is by
   email/phone, never name.** Org toggle `auto_crm_capture_enabled`, on by default. Hook is in
   `_persist_user_message` (covers handoff-paused messages too).
   **Note:** the CRM now lists *people*, so a visitor who never shares an email/phone doesn't
   appear there — only in the Inbox.

Suites: **284 pytest**, **73 vitest**, **41/41 Playwright**. The E2E suite now intermittently
trips the hardcoded `public_chat`/`channel_webhook` rate limits on a full back-to-back run —
see the roadmap note in `docs/PROGRESS.md`.

### 2026-07-29 — Seven-feature batch (WhatsApp window → team performance)
Built in dependency order, one commit each:
1. **WhatsApp 24h window** (`fb6140f`) — a silent-failure bug, not a gap. `last_inbound_at`
   (separate from `last_message_at`, which our own outbound moves), `check_can_send()` hook run
   *before* persisting, typed `whatsapp_window_closed` (409), `send_template()`, Meta `131047`.
2. **Canned responses** (`39e582a`) — `/` picker in the composer (triggers only at start/after
   whitespace). Also fixed a pre-existing 500: any `field_validator` raising `ValueError`
   returned `internal_error` instead of 422.
3. **Macros** (`987feda`) — reuse the inbox service fns the manual buttons call; validate every
   step *before* applying any (a rollback can't un-send a message), then SAVEPOINT.
4. **Contacts CRM** (`37826fa`) — extends the existing `Contact`; `/contacts` page + nav.
5. **Help Center** (`61f1788`) — separate store from RAG, public pages, opt-in KB sync into a KB
   the agent *actually reads*. In-repo markdown renderer escapes before formatting.
6. **Campaigns** (`3b27bf8`) — widget triggers live; **broadcasts draft-only** (ADR-039).
7. **Team performance** (`9281d27`) — `/v1/analytics/agents`, people not channels.

Migrations `0007`–`0012`. Suites after the batch: **251 pytest**, **68 vitest**, **38 Playwright**.
**Note:** run the API *without* `CELERY_TASK_ALWAYS_EAGER` when a real worker is up — eager mode
makes the API run the async ingest task inline, where it never awaits, so ingestion never lands.

### 2026-07-29 — Inbox shows every channel tab (connected or not)
- **Reverses** the 07-28 filtering rule: `visibleChannelTabs` → `inboxChannelTabs()` (all 7, always)
  + new `isChannelConnected()`. Unconnected tabs are dimmed with a hollow dot and an `aria-label`
  of `"{Channel} (not connected)"` — status drives styling, not presence, so a user can discover
  and connect a platform from the Inbox instead of needing to know the builder exists.
- Selecting an unconnected tab replaces **both** panels with a connect prompt; the
  connected-but-quiet `"Nothing in the inbox yet."` is deliberately kept separate.
- **One credential form still**: the Inbox resolves which agent owns the channel (direct / picker /
  create-agent) and deep-links `/agents/{id}?tab=channels&connect={type}`; `MessagingChannels`
  consumes the param on mount to open the existing `ConnectDialog`, then strips it.

### 2026-07-28 — Per-channel analytics (Dashboard + Analytics breakdown)
- **Backend** (`modules/analytics/`): `Overview.by_channel` (new `ChannelBucket`), `group_by=channel`
  on `usage()`, a `channels` CSV export kind, and a `channel=` filter on every endpoint. Channel set
  = channels **with traffic ∪ channels connected & enabled** (+ always `widget`) — a plain
  `GROUP BY channel` drops a live-but-silent channel, the exact case that matters when Meta channels
  go live before any real message. Rates guard zero conversations.
- **Frontend**: one `ChannelBreakdown` component shared by Dashboard + Analytics; zero-traffic rows
  render flagged with an em dash (not `0%`). `dashboard`/`api`/`web` got real labels in
  `channel-meta.ts` as reporting-only channels (never inbox tabs).
- **Flagged, not fixed** (see `docs/PROGRESS.md` roadmap): the dashboard's `AgentsPanel` /
  `ConversationsPanel` still render `@/lib/mock/data`; the Analytics page has no date/agent filter UI
  despite the API supporting the params.

### 2026-07-28 — Unified multi-channel inbox (contacts, IG/Messenger, channel tabs)
- **Contacts** (`models/contacts.py`, migration `0006_contacts`, `app/contacts/service.py`): new
  `contacts` table unique on `(org, channel, external_id)` + `conversations.contact_id`. One upsert
  helper for every inbound path (channels **and** the widget's `Visitor`); `COALESCE(new, old)` +
  JSONB merge so a nameless payload never erases a known name. New `BaseChannel.fetch_profile` hook,
  called only while a contact still lacks a name/avatar.
- **Instagram + Facebook Messenger**: `channels/instagram.py` + `facebook.py` over a shared
  `meta_messaging.py`; Meta's signature/challenge logic factored out of `whatsapp.py` into
  `meta_signature.py` (all three Meta surfaces now share one verification path). Deliveries matched
  on webhook `object`; echoes/read receipts ignored. **Comment moderation deliberately excluded** — ADR-037.
- **Inbox**: nested `contact` on list/detail (batched), `?channel=` filter, and a channel tab bar
  (Web Chat always; others only once an enabled `Channel` row exists — **superseded 2026-07-29**,
  all tabs now always render) with contact avatars +
  platform badges. ADR-036 (no Telegram avatar — its photo URLs embed the bot token), ADR-038.

### 2026-07-27 — Widget preview single-host + app sidebar
- **Widget preview overlap** (`packages/widget/src/widget.js`): live Playwright diagnosis in the
  real Channels tab confirmed a **single** `#vicero-widget` host (not a duplicate mount). The
  white/black "overlap" was an unstyled first-paint frame. Fixed `build()`: remove any pre-existing
  host (idempotency) + build **detached** and theme via `applyConfig()` *before* `appendChild` — no
  unstyled frame. Playwright asserts one host across config posts + open/close.
- **App sidebar** (`apps/web/src/components/shell/sidebar*.tsx`): footer (Scale plan + Collapse) was
  pushed below the fold — the `<nav>` lacked `min-h-0` and the `aside` was `lg:static` under a
  `min-h-screen` shell (unbounded). Fixed with `min-h-0` on the nav + `lg:sticky lg:top-0 lg:h-screen`
  on the aside; added a **header collapse toggle** next to the logo (both states) sharing
  `toggleCollapsed`. Commits `73a949c`, `f1db62e`.

### 2026-07-21 — Widget customization parity + fixes
- Full widget customization (styles/colors/fonts/logo/launcher designs/live preview) — ADR-035.
- Fixes: preview no longer force-opens on config change (`build`/`applyConfig` split); transparent
  glass palette; real launcher SVGs in the picker; mobile launcher-vs-panel collision hidden
  `<768px` via a `bf-open` host class. RAG fix: support-bot default prompt + `score_threshold` 0.7→0.35.

### 2026-07-19/20 — Phases 19–20 complete; repo published
- Phase 19 (E2E for PRD criteria 1–7, Next 14→16 upgrade, docs, a11y) + Phase 20 (prod compose +
  Caddy TLS, Redis pub/sub hub, webhook retry sweep, httpOnly refresh via BFF, /metrics + Sentry +
  backups, K8s manifests, release CI/CD). Phase 18 (billing) deferred by design. Tagged
  `phase-19-complete`, `phase-20-complete`. Pushed to `github.com/aadesh2025/vicero` (private).

