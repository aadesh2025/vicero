# docs/15 — Production capacity & deployment topology

> **Why this file exists:** `docs/14` was written architecture-first and **deployment-blind**.
> It said "measure RAM empirically" without ever asking what hardware exists. At the scope
> agreed in docs/14 §1.1, that omission is not cosmetic — **the plan may not fit on one VPS.**
>
> **This file also records three production bugs that exist TODAY**, independent of Docling.
> They block real client hosting on their own. Fix them before anything in docs/14.
>
> **Status:** analysis + capacity plan. Sizing figures are **estimates, not measurements** —
> see §7 for confidence.
>
> **Committed 2026-08-17, after re-verifying every claim in §2 against the code.** Two of this
> file's forward-looking statements were overtaken by work that landed the same day — see **§8**.
>
> **All three PROD bugs were then fixed for the compose deployment — PROD-1/PROD-2 in ADR-068
> (§9), PROD-3 in ADR-069 (§10). The k8s manifests are documented rather than fixed (§9.5).**
> §8–§10 are what to read first if you are picking this up later; §2–§7 are preserved as the
> original analysis, including where it turned out to be wrong.

---

## 1. Answer to the question

**docs/14 was written for correctness, not for a VPS.** Concretely, what it assumed without
saying so:

| docs/14 said | Silent assumption | Reality on a VPS |
|---|---|---|
| "add a docling-serve compose service" | RAM is free | It is the largest process in the stack |
| "self-hosted bge-reranker" | CPU is free | It is on the **critical path** and will contend with batch ML |
| "object storage for DoclingDocument JSON" | Object storage exists | **You have none.** No S3, no MinIO service |
| "separate `q=media` worker" | Another worker is cheap | Another worker is another few GB |
| "measure RAM in K1-2" | Someone will | No sizing target existed to measure against |

**`docs/09-DEPLOYMENT.md` contains no sizing guidance at all** — no RAM, no CPU, no VPS
recommendation, no instance type. Verified by grep. So this gap predates docs/14; docs/14 just
made it expensive.

---

## 2. ⚠️ Three production bugs that exist today

These are **not** caused by the Docling plan. They are in `infra/docker-compose.prod.yml` now.

> **Update 2026-08-17 — all three are FIXED for the compose deployment.** PROD-1 and PROD-2 in
> ADR-068 (**§9**), PROD-3 in ADR-069 (**§10**). The analysis below is kept as written, because it
> is the record of what was wrong and why it was invisible; §9 and §10 record what shipped, what
> they did not cover, and the four things the fixes turned up that this section had wrong.

### PROD-1 — File uploads cannot be ingested in production ⛔

**Severity: blocks real clients. This is the important one.**

```
apps/api/app/core/config.py:57
    upload_dir: str = "./var/uploads"        ← container-local path

infra/docker-compose.prod.yml
    api:      (no volumes)                   ← writes the file HERE
    worker:   (no volumes)                   ← reads the file THERE
```

`api` and `worker` are **separate containers with separate filesystems** and **no shared
volume** — verified, there is no uploads volume anywhere in the prod compose.

The failure path:

```
1. client uploads refund-policy.pdf
2. api writes  ./var/uploads/<id>.pdf   (inside the api container)
3. api enqueues ingest task, returns 200 — the UI shows "processing"
4. worker picks it up in a DIFFERENT container
5. ingest.py:33  Path(storage_path).read_bytes()  → file does not exist
6. LoaderError("Stored file is missing")  → document.status = "failed"
```

**Why it is invisible in dev:** on this machine api and worker run from the same directory on
the same filesystem, so the path resolves. `CLAUDE.md` §12 describes exactly that setup. **The
bug only appears once the two are containerised separately — i.e. only in production.**

**Scope:** file uploads fail. URL ingest and pasted-text ingest are unaffected (no file on
disk), which is why a smoke test could pass while the feature is broken — the same shape as the
2026-08-02 empty-200 outage.

**Same bug, worse, in `infra/k8s/app.yaml`:** no `volumeMounts`, no PVC. Pods are ephemeral and
may land on different nodes, so even a hostPath would not save it.

**Fix options:**

| Option | Verdict |
|---|---|
| Shared named volume mounted into `api` + `worker` | ✅ Simplest correct fix for single-VPS. **Breaks the moment you scale to 2 nodes** |
| S3-compatible object storage (MinIO service, or real S3/R2/Spaces) | ✅ **Correct long-term.** Also gives docs/14 §3.4 somewhere to put the DoclingDocument JSON |
| Store bytes in Postgres | ❌ Bloats the DB and every backup |

**Recommendation: do the shared volume now (one line, unblocks clients), plan the S3 move with
docs/14 K1-4**, since that phase needs blob storage anyway.

### PROD-2 — The default embedding provider is not deployed ⛔

```python
# app/core/config.py:51-53
ollama_base_url: str = "http://localhost:11434"
embedding_provider: str = "ollama"
embedding_model:   str = "nomic-embed-text"

# app/modules/knowledge/schemas.py:19
embedding_provider: str = Field(default="ollama", ...)
```

`docker-compose.prod.yml` passes `OLLAMA_BASE_URL: http://ollama:11434` — but **there is no
`ollama` service in the prod compose.** Services are: postgres, redis, migrate, api, worker,
beat, web, caddy, backup.

So a knowledge base created with defaults in production points at a hostname that does not
resolve. Every embed call fails; every document fails to ingest.

**Fix:** either add an `ollama` service to prod compose (it is another ~1–2 GB resident), or
change the production default to a provider that is actually reachable. **Whichever you pick,
`docs/09-DEPLOYMENT.md` must state it** — a default that only works in dev is a trap for the
next deploy.

**⚠️ This is the same failure family as 2026-08-02 (`.env` comment as an API key) and
2026-07-31 (`LLM_FORCE_FAKE` on port 8010): configuration that is correct in dev and silently
wrong in production.** A startup check that resolves the configured embedding provider and logs
loudly would have caught all three.

### PROD-3 — No resource limits on any service ⚠️

No `deploy.resources`, `mem_limit`, or `cpus` on any service in the prod compose — verified.

On a single VPS with everything co-located, **any container can consume all RAM and the kernel
OOM-killer picks a victim.** It will often pick Postgres, because Postgres is the largest
resident process. `restart: unless-stopped` then restarts things into the same condition.

This is tolerable today because nothing in the stack is memory-hungry. **It stops being
tolerable the moment docling-serve exists**, because that container's whole job is to load
several ML models.

**Fix:** memory limits on every service, sized per §4. Postgres and Redis must be protected
from the ML containers by construction, not by hope.

---

## 3. Capacity arithmetic

**All figures are estimates.** They are derived from what each component loads, not from a
measured run of this stack. Treat as planning numbers to validate in K1-2, not as truth.

### 3.1 Vicero today

| Service | Est. RSS | Notes |
|---|---|---|
| postgres (pgvector) | 1–2 GB | Depends on `shared_buffers`; HNSW index build is spiky |
| redis | 0.3–0.5 GB | Cache + Celery broker + rate limits |
| api (uvicorn, 2 workers) | 0.5–1 GB | |
| worker (concurrency=4) | 0.8–1.5 GB | 4 processes |
| beat | ~0.1 GB | |
| web (Next.js) | 0.5–1 GB | |
| caddy | ~0.05 GB | |
| **Subtotal** | **≈ 3.5–6 GB** | Comfortable on an 8 GB VPS |

### 3.2 What docs/14's agreed scope adds

| Component | Est. RSS | Notes |
|---|---|---|
| torch runtime (loaded) | 0.8–1 GB | Base cost before any model |
| Layout model + TableFormer | 1–2 GB | `models-local`. **The core value — non-negotiable** |
| RapidOCR (onnxruntime) | 0.3–0.7 GB | |
| **Granite Vision (chart)** | **4–7 GB** | ⚠️ **A multi-billion-parameter VLM.** The single largest item |
| Whisper | 0.1–3 GB | `tiny` ≈ 0.1 · `turbo` ≈ 1.6 · `large` ≈ 3 |
| `q=media` worker | 0.3–0.5 GB | Thin — real work is in docling-serve |
| bge-reranker-v2-m3 | 1–2 GB | ⚠️ **On the critical path** |
| **Subtotal (full scope)** | **≈ 8–16 GB** | |

### 3.3 Totals

| Scope | Est. total RAM | Verdict on one VPS |
|---|---|---|
| Today | 3.5–6 GB | 8 GB box ✅ |
| + Docling **core** (layout, tables, OCR) | 6–10 GB | **16 GB box ✅** |
| + chart VLM | 10–17 GB | 32 GB box, tight |
| + ASR (turbo) | 12–19 GB | 32 GB box |
| + reranker | **13–21 GB** | **32 GB minimum, and see §3.4** |

### 3.4 ⚠️ RAM is not the binding constraint — CPU contention is

Memory you can buy. The real problem at full scope is that **two workloads with opposite
requirements fight over the same cores**:

| Workload | Requirement | Behaviour |
|---|---|---|
| **Reranker** | **p50 must stay under 417 ms** (NFR-1) | Small, frequent, latency-critical |
| Chart VLM + Whisper | Throughput | Large, long, saturates every core it is given |

On a shared CPU box, one client uploading a chart-heavy PDF or a webinar will **pin every core
for minutes**. The reranker — which sits directly in front of token generation for *every
visitor on every tenant* — is then queued behind it.

**Concretely: a batch ingest job can blow your first-token SLA for every live conversation on
the platform.** That is a multi-tenant fairness problem of the same shape as the `q=media` queue
issue in docs/14 §3.7, one layer down — and a Celery queue does not fix it, because the
contention is for CPU, not for queue slots.

**Also CPU-only inference is slow.** Rough orders of magnitude, no GPU:

- Chart VLM: **tens of seconds per figure**
- Whisper `turbo` on 1 hour of audio: **plausibly 20–60 minutes**

Both are acceptable *as background work on their own machine*. Neither is acceptable sharing
cores with a latency-critical path.

---

## 4. Topology options

### Option 1 — Single VPS, reduced scope ★ recommended first step

```
┌──────────── VPS · 16 GB · 8 vCPU ────────────┐
│ caddy · web · api · worker · beat            │
│ postgres · redis                             │
│ docling-serve  (layout + tables + OCR ONLY)  │
│ shared uploads volume  ← fixes PROD-1        │
└───────────────────────────────────────────────┘
   NO chart VLM · NO ASR · NO self-hosted reranker
```

- Delivers **W1 (PII/extraction safety fix), W2 (`contextualize`), W3 (OCR), W4 (tables)** —
  i.e. the entire retrieval-quality thesis of docs/14.
- Chart VLM and ASR deferred; reranker deferred or hosted.
- **Cheapest path to real clients**, and the one that fits your current stack shape.

### Option 2 — Two VPS: app box + ML box

```
┌── VPS A · 8 GB ────────┐   ┌── VPS B · 16–32 GB ─────────┐
│ caddy web api worker   │──▶│ docling-serve (full scope)  │
│ beat postgres redis    │   │ bge-reranker                │
└────────────────────────┘   └─────────────────────────────┘
      private network only — docling-serve NEVER public
```

- **Isolates the CPU contention** described in §3.4. This is the point of the option.
- ⚠️ Adds a network hop to the reranker — partially undoing the latency argument that favoured
  self-hosting over Cohere in docs/14 §5.3. **Same-datacentre private networking keeps this near
  1 ms; cross-region does not.** If the boxes are not adjacent, use hosted rerank instead.
- Requires shared blob storage (S3/MinIO), because uploads now cross a machine boundary — which
  is PROD-1's proper fix anyway.

### Option 3 — App VPS + on-demand GPU for ML

```
VPS A (always on, small)  +  GPU worker that scales to zero
```

- Ingest is **bursty**: a client onboards, uploads 40 documents, then nothing for a week. Paying
  for an idle 32 GB ML box between bursts is the wrong cost shape.
- GPU makes chart VLM and Whisper **10–50× faster**, turning "acceptable as overnight batch"
  into "acceptable while the client watches".
- ⚠️ Cold start (model load) is 30 s–several minutes. Fine for ingest. **Never acceptable for
  the reranker** — keep that always-on and separate.

### Option 4 — Managed / hosted

- Cohere for rerank, a hosted document API for conversion.
- Highest per-unit cost, near-zero ops. ⚠️ **Sends client KB content to third parties** — kills
  any data-residency story and needs to be in your client contracts (docs/14 §5.3).

### Decision matrix

| Criteria | 1: One VPS reduced | 2: Two VPS | 3: On-demand GPU | 4: Managed |
|---|---|---|---|---|
| Monthly cost | **Lowest** | Medium | Low–medium (bursty) | Highest per unit |
| Ops complexity | **Lowest** | Medium | **Highest** | Lowest |
| p50 protected | ✅ (no contention) | ✅ | ✅ | ⚠️ network RTT |
| Full docs/14 scope | ❌ | ✅ | ✅ | ✅ |
| Data stays in-house | ✅ | ✅ | ✅ | ❌ |
| Scales past ~1 box | ❌ | Partly | ✅ | ✅ |

**Recommended sequence: 1 → 2 or 3.** Ship Option 1, get real clients on it, and let measured
ingest volume decide whether chart VLM and ASR are worth a second machine. **Do not buy a 32 GB
box for a workload you have not measured** — docs/14's P0-2 exists precisely so these decisions
stop being guesses.

---

## 5. What must change in docs/14

| docs/14 item | Change |
|---|---|
| §4.1 "add a compose service" | Add a **sizing target and a memory limit** per §3 |
| §6 "object storage" | ⚠️ **You have none.** Add MinIO to compose, or use real S3/R2/Spaces. Blocks K1-4 |
| §5.3 self-hosted reranker | Holds on Option 1/2 **only if same-host or same-datacentre**. Re-open the Cohere comparison otherwise |
| §3.6 chart VLM | Move **behind** a measured decision. It is 4–7 GB for a feature no client has asked for yet |
| §3.7 media queue | A separate Celery queue is necessary but **not sufficient** — §3.4 shows the contention is for CPU |
| §7 K1-2 | Add: record measured RSS and CPU-seconds per document type, against §3's estimates |
| Whole plan | Add **PROD-1/2/3 as blocking prerequisites** |

### Additional VPS-only concerns docs/14 never mentioned

- **Model weight caching.** Docling downloads model weights on first use — several GB. Without a
  persistent volume for the HF cache, **every container restart re-downloads them.** On a
  metered VPS that is bandwidth cost and minutes of downtime per deploy.
- **Backups.** `infra/scripts/backup.sh` covers Postgres. It does **not** cover uploaded files or
  the persisted DoclingDocument JSON. After K1-4 those become part of the recovery story —
  restoring Postgres alone would leave every document row pointing at a blob that is gone.
- **Disk.** Persisted DoclingDocument JSON + original uploads + media files grow without bound.
  Needs a retention policy (docs/14 K5-3) and disk alerting.
- **`document_timeout` interacts with the VPS.** Docling recommends 90–120 s; on a small
  CPU-only box a chart-heavy PDF can exceed that and fail. Tune it against the machine you
  actually run on, not the default.

---

## 6. Recommended order

| # | Task | Why now |
|---|---|---|
| ~~**1**~~ | **PROD-1** shared uploads volume (api + worker) | ✅ **shipped** 2026-08-17, ADR-068 — compose only, k8s documented (§9.5) |
| ~~**2**~~ | **PROD-2** reachable embedding provider + startup check | ✅ **shipped** 2026-08-17, ADR-068 — and §2's proposed fix would not have worked (§9.2) |
| ~~**3**~~ | **PROD-3** memory limits on every service | ✅ **shipped** 2026-08-17, ADR-069 — §10. Ceilings + reservations on the datastores |
| 4 | Sizing section in `docs/09-DEPLOYMENT.md` | The gap that produced this whole file |
| 5 | Blob storage (MinIO or S3) | Proper PROD-1 fix; unblocks docs/14 K1-4 |
| 6 | docs/14 **P0-1**, **P0-2** | Unchanged — still first among the retrieval work |
| 7 | docs/14 **K1** + **K2** on Option 1 | The whole quality thesis, on a 16 GB box |
| 8 | Measure. Then decide on chart VLM / ASR / reranker | Buy hardware for a measured workload |

**PROD-1 and PROD-2 are not part of the Docling project.** They are live defects that block
hosting clients regardless of whether a single line of docs/14 is ever implemented.

---

## 7. Confidence

**High — PROD-1, PROD-2, PROD-3.** Read directly from `infra/docker-compose.prod.yml`,
`app/core/config.py:51-57`, `app/modules/knowledge/schemas.py:19`, `app/rag/ingest.py:33`, and
`infra/k8s/app.yaml`. The absence of an uploads volume and of an `ollama` service were both
confirmed by grepping the compose file. **These are code-level facts, not inferences.**

**High — the CPU-contention argument (§3.4).** Does not depend on exact numbers. Batch VLM/ASR
saturating cores while a latency-critical reranker waits is structural, not empirical.

**Medium — the RAM estimates (§3).** Derived from what each component loads, **not measured on
this stack**. Granite Vision in particular is a range because the exact variant and precision
docling-serve loads was not verified. **Treat §3 as a planning target to validate in K1-2, not
as truth.** They are very unlikely to be wrong by enough to change §4's conclusion — the gap
between "6–10 GB" and "13–21 GB" is too wide to close with estimate error.

**Low — CPU timing figures (chart seconds/figure, Whisper minutes/hour).** Orders of magnitude
from general knowledge of these model classes, on unspecified hardware. Directionally reliable
(VLM and ASR on CPU are slow); numerically not. **Do not size a machine off them — measure.**

**Not assessed:** actual client document volume, upload frequency, mix of formats, and how many
tenants will run concurrently. Every number in §4 is sensitive to these and I have none of them.
That is the next thing to establish, and it is information only you have.

---

## 8. Re-verification and corrections — 2026-08-17

This file was written before docs/14 K1–K5 were executed. Everything in §2 was re-checked against
the code on the day it was committed; two forward-looking claims elsewhere did not survive
contact with what actually shipped.

### 8.1 §2's three bugs are all still present

Re-read, not assumed — and the line references still land:

- **PROD-1** — `infra/docker-compose.prod.yml` declares volumes on `postgres`, `redis`, `caddy`
  and `backup` only. `api` (line 60) and `worker` (line 81) have none, and the named-volume list
  is `pgdata, redisdata, caddydata, caddyconfig, backups` — **no uploads volume.**
  `infra/k8s/app.yaml` still has no `volumeMounts` and there is no PVC anywhere (only
  `datastores.yaml` mounts anything).
- **PROD-2** — the service list is `postgres, redis, migrate, api, worker, beat, web, caddy,
  backup`. **Still no `ollama`**, while line 15 still passes
  `OLLAMA_BASE_URL: http://ollama:11434` and `config.py:51-53` still defaults to it.
- **PROD-3** — no `deploy.resources`, `mem_limit` or `cpus` on any service.
- **§1's claim about `docs/09-DEPLOYMENT.md` still holds**: grepping it for RAM, GB, vCPU, sizing
  or instance type returns nothing. The gap that produced this file is still open.

One reference to fix rather than trust: §7 cites `app/rag/ingest.py:33` for the failing read. The
read now lives in **`ingest._stored_bytes()`** — same behaviour, moved by K1. Cite the function,
not the line.

### 8.2 ⚠️ Correction: object storage did **not** block K1-4

§5 says the missing object storage "Blocks K1-4". It did not. K1-4 shipped persisting the
`DoclingDocument` to **local disk beside the source file** (`Path(storage_path).with_suffix(
".docling.json")`), so the phase completed without any blob store.

That is worth understanding rather than filing as a miss, because the design turns out to
interact well with PROD-1's recommended fix: because the JSON is written *beside* the upload,
**one shared uploads volume covers both files automatically.** A fix that gave `api` and `worker`
separate volumes each would not, and would be the tempting shortcut.

It does not make object storage unnecessary — §4 Option 2 still requires it the moment uploads
cross a machine boundary — but it is no longer a prerequisite for a docs/14 phase.

### 8.3 The backup gap is now live, not future — ✅ closed 2026-09-23 (ADR-082)

§5 says the persisted JSON becomes part of the recovery story "after K1-4". K1-4 has landed, so
that file exists today. `infra/scripts/backup.sh` covered Postgres only, so **neither the
uploads nor the extracted text were backed up** — restoring Postgres alone left every
`documents` row pointing at a `storage_path` and a `docling_json_path` that were gone.

**Closed.** `backup.sh` now also `tar -czf`s the `uploads` volume (mounted read-only into the
prod `backup` service at `/uploads`, the same volume `api`/`worker` write to) alongside the
`pg_dump`, same timestamp, same rotation, same `backups` volume. `restore.sh` takes the archive
as an optional second argument. Verified with a real backup→restore round trip against this
project's own dev database and its real `uploads` directory (48 tables, 2993 files) — restored
`organizations` row count and a `diff -rq` of the restored directory both matched the source
exactly. Backward compatible: `UPLOADS_DIR` unset still produces the DB dump alone, loudly
flagged rather than silently gapped. See ADR-082 and `docs/09-DEPLOYMENT.md` §4.

### 8.4 The retention half of §5's disk bullet is done; the alerting half is not

K5-3 shipped: deleting a document removes both its files, and re-ingesting without structure
discards the orphan. Deliberately no expiry job — the JSON's lifetime is the document's. What
remains from that bullet is **unbounded growth from documents that are still live**, plus disk
alerting, neither of which K5-3 addresses. `MAX_PDF_PAGES` (K5-2) caps the pathological single
upload but says nothing about aggregate volume.

### 8.5 §6's ordering: items 6 and 7 are done, and item 8 is now the live question

P0-1 and P0-2 shipped 2026-08-13; K1 (bar K1-5), K2, K2-6, K3-1, K3-2, K5-2 and K5-3 shipped
2026-08-17. So the sequence has reached **item 8, "Measure. Then decide"** — except that the
measuring is itself blocked:

**§3.2's docling-serve RAM figures are still estimates, and now have a named blocker rather than
just an absence of effort.** Docling is enabled nowhere because K1-5 needs three real binary
fixtures that are not in the repo, so no run of this stack has ever loaded the layout model,
TableFormer, RapidOCR or Granite Vision. §7's "validate in K1-2" is outstanding for that reason.
Until then §4's recommendation — **Option 1, one 16 GB VPS, no chart VLM, no ASR, no self-hosted
reranker** — stands on the §3.4 CPU-contention argument, which §7 rates high confidence precisely
because it does not depend on the numbers.

**Nothing in §2 is waiting on docs/14.** PROD-1 and PROD-2 block hosting a real client today.

---

## 9. PROD-1 and PROD-2: what shipped — 2026-08-17

ADR-068. Both fixed for the **compose** deployment, which is the one §4 Option 1 recommends and
the one a real client would be hosted on.

### 9.1 PROD-1 — shared uploads volume

An `uploads` named volume mounted into **api and worker at the same path**, with
`UPLOAD_DIR=/app/var/uploads` named explicitly in `x-api-env` so the setting and the mount are
visibly one decision. Because the persisted `DoclingDocument` is written *beside* the source file,
one volume covers both — see §8.2.

**⚠️ A trap this section did not know about, found while fixing it — and measured, not reasoned.**
Docker seeds an empty named volume from the image's directory *including its ownership*, but if the
path does not exist in the image it creates the mountpoint **root-owned**, and the api runs as uid
10001. Two minimal images differing only in that `mkdir`, each with a fresh named volume mounted at
the path:

```
WITH  mkdir in the image:  drwxr-xr-x appuser appuser  /app/var/uploads   -> WRITE OK
WITHOUT:                   drwxr-xr-x root    root     /app/var/uploads   -> Permission denied
```

So mounting the volume alone would have replaced "file not found" with "permission denied",
surfacing at the first client upload rather than at build time. `apps/api/Dockerfile` now creates
the directory in the same `RUN` as the `useradd`, before the `chown`, and a test asserts that
ordering.

### 9.2 PROD-2 — the embedding service, and the half of it §2 got wrong

An internal-only `ollama` service (`expose`, no `ports` — it has no authentication, so a published
port is an open inference endpoint) with a weights volume so a restart does not re-download.

**⚠️ §2's fix — "add an `ollama` service to prod compose" — would not have fixed it.**
`ollama/ollama` starts **empty**: it serves an API with no models, and embedding against a model it
has not pulled is an *error*, not an implicit download. So the service pulls `nomic-embed-text` on
start and **its healthcheck asserts the model is present rather than that the port answers** — a
healthy-but-empty Ollama is exactly the state that produced PROD-2 in the first place.

**⚠️ §2's other fix option — "change the production default to a provider that is actually
reachable" — does not exist.** `build_embedding_provider` accepts `openai` and `gemini` and
constructs an **Ollama** client for both, because no adapter for either was ever written. An
operator setting `EMBEDDING_PROVIDER=openai` to escape a missing Ollama would change nothing
whatsoever and have no way to tell. Startup now warns (`embedding_provider_has_no_adapter`). Note
also that `chunks.embedding` is `vector(768)`, so a 1536-dimension model is a migration, not an
env var — the "just point it somewhere else" fix was never one line.

**api and worker depend on it with `service_started`, never `service_healthy`.** Embeddings are
required by the knowledge base, not by the platform; a worker held back by a failed model download
would take webhooks, email and campaigns down with it, whereas a queued ingest task waits in Redis.
That is a delay instead of an outage.

### 9.3 The startup check §2 asked for

§2 said *"a startup check that resolves the configured embedding provider and logs loudly would
have caught all three."* It exists: `embeddings.probe_reachable()`, one `GET /api/tags` capped at
2s, logging `embedding_provider_unreachable` or `embedding_model_missing` at **error** level under
`ENV=prod` and warning elsewhere (a dev box without Ollama is normal). It **never fails startup** —
a diagnostic that can stop the API coming up is worse than the bug it reports — and it is off in
the test suite, because a suite that does network I/O has a result that depends on the host.

### 9.4 What tests can now catch, and how that was verified

The reason these two bugs shipped is that **no test looked at how the thing is deployed**: every
line of application code was correct and the suite was green. `tests/test_infra_prod_compose.py`
parses the prod compose and asserts the *shape*, not specific lines:

- every service that touches uploads mounts **the same** volume at the same path (a test naming
  `api` and `worker` would wave a future `q=media` worker straight into the same bug);
- every internal hostname the containers are pointed at is a service declared in the same file,
  with an explicit `_EXTERNAL_HOSTS` allowlist so a new external dependency is a decision;
- the pulled model matches `Settings.embedding_model`, so the hardcoded string cannot drift;
- ollama publishes no port, and nothing gates startup on it.

**Each of those was run against a mutated compose file to confirm it goes red** — the original
PROD-1 and PROD-2 states, plus the plausible half-fixes: volume on the api only, a *different*
volume per service, service added but model never pulled, model drifted, port published, and
`service_healthy` added. All eight fail. A check that has never failed has not been tested either.

### 9.5 Not fixed, deliberately

- **k8s (`infra/k8s/app.yaml`)** — **documented, not fixed**, and that is a trade rather than an
  oversight. 2 api + 2 worker replicas need `ReadWriteMany`; a `ReadWriteOnce` PVC would not fix
  it, and an RWX PVC on a cluster without an RWX StorageClass stays `Pending` and leaves every api
  and worker pod in `ContainerCreating` — trading a broken *feature* for a broken *platform*. The
  manifest now opens with an unmissable warning, the exact YAML to add if the cluster has RWX, and
  a pointer to object storage as the real answer.
- ~~Backups still do not cover the `uploads` volume~~ — **✅ closed 2026-09-23, ADR-082** (§8.3).
  Still true for **k8s** specifically, since no RWX volume/k8s backup story exists there yet —
  the fix above is the single-box (`docker-compose.prod.yml`) path only.
- **`ollama`'s resident size is still unmeasured.** §3.2 estimates 1–2 GB, but that figure assumed
  a chat model; serving only `nomic-embed-text` should be far smaller. Not measured, so not
  claimed — it is a planning correction to validate, exactly as §7 says of every number in §3.

---

## 10. PROD-3: what shipped — 2026-08-17

ADR-069. A memory limit on **all ten services**, plus memory reservations on Postgres and Redis.

**The mechanism was verified, not assumed.** Non-swarm Compose does honour `deploy.resources`:
`limits.memory` → `Memory`, `reservations.memory` → `MemoryReservation`, `limits.cpus` →
`NanoCpus`, confirmed by `docker inspect` on a throwaway stack. And a container that overruns its
own limit is killed **alone** — `OOMKilled=true`, exit 137 — which is the entire point of §2's
"protected by construction": the blast radius becomes the offending service instead of whatever
the kernel judged largest, which was Postgres.

**A limit is not protection, and that distinction is the reason for the reservations.** A limit
only stops a service *growing*. Under host pressure the kernel reclaims from containers that are
**above** their reservation first — so `POSTGRES_MEM_RESERVATION` and `REDIS_MEM_RESERVATION` are
what actually make the datastores preferred, and a test asserts both exist.

**⚠️ The ceilings sum to ~16.25 GB on a 16 GB box, deliberately.** They are ceilings, not a budget.
Sizing every service at its worst case would leave most of the machine idle; steady state is
§3.1's ~3.5–6 GB plus ollama, `migrate` is one-shot, and `backup` sleeps 24h at a time. What they
buy is that no *single* service can take the host.

**⚠️ And the honest risk of this fix: a limit set too low turns a working service into a
crashloop.** §7 rates §3's RAM figures only medium confidence and they were never measured on this
stack, so every default has headroom over the estimate and **every one is overridable** — the box
decides, not this file. Postgres is the one to watch: an HNSW index build is spiky and is not the
steady state. `docker inspect <container> --format '{{.State.OOMKilled}}'` distinguishes "limit too
low" from "bug" in one command, and `docs/09` §3 says so.

### 10.1 ⚠️ A correction to §3.4: embeddings are latency-critical too

§3.4's table splits the work into **latency-critical** (the reranker, p50 under 417 ms) and
**throughput** (chart VLM, Whisper). It leaves embeddings out — but `retrieval.search()` embeds the
visitor's **query** inline on every RAG turn, so `ollama` sits on the p50 first-token path *exactly
as the reranker does*, and it is the one ML container in the stack today.

So **no CPU limit on `api` or `ollama`**: capping either would add latency to every grounded answer
to buy nothing, because a runaway there is a memory problem, not a CPU one. §3.4's argument is
untouched and its conclusion is unchanged — CPU caps belong on the **batch** ML services
(docling-serve, ASR) when they land, and that is where §3.4's contention actually bites. A test
pins that neither of the two latency-path services acquires a CPU cap.

### 10.2 The check that stops PROD-3 coming back

`test_every_service_declares_a_memory_limit` asserts it for **every** service rather than a listed
set, because the way this returns is someone adding a service — docling-serve, an ASR worker, a
rerank container — without thinking about its ceiling, and those are precisely the memory-hungry
ones in §3.2. Verified against mutations: no limits anywhere → red; a new unlimited `docling`
service → red; reservations dropped from Postgres → red; a CPU cap added to `ollama` → red. The
legacy `mem_limit` spelling stays green on purpose, since Docker applies it identically (measured:
both produce `Memory=314572800`).

### 10.3 ⚠️ Found while fixing this: the deploy command in the docs did not work

Not a PROD bug, but it would stop a first deploy dead. Two mechanisms read env from two different
files: `env_file: ../.env` supplies the *containers*, but `${VAR}` **interpolation** is resolved by
Compose itself from the shell and `infra/.env` — **never `../.env`**. Verified: with
`POSTGRES_PASSWORD` present in `../.env` and cleared from the shell, `docker compose config` still
fails with *"required variable POSTGRES_PASSWORD is missing a value"*; adding `--env-file ../.env`
gets past it. So the documented `docker compose -f docker-compose.prod.yml up -d --build` could
never have worked from the file the header told you to populate. It fails **loudly**, before
anything starts, which is the one good thing about it. Both the compose header and `docs/09` §3 now
carry `--env-file ../.env` and name which variables belong to which mechanism.

## 11. First-ever concurrency/load test — RISK-REGISTER R4 (2026-09-23)

Every prior verification in this doc, and in every phase entry in `docs/PROGRESS.md`, is
correctness under low, sequential, test-shaped traffic — one request at a time. Nothing had ever
sent concurrent requests at this stack before. `RISK-REGISTER.md` carried this as **R4**, P0.
New `infra/perf/load_test.py` — see its own header for the exact reproduction command and why it
must run against a dedicated instance (`provider="fake"`, guard L2/L3 disabled) rather than the
operator's normal dev API: a real concurrency test against Groq would risk repeating the
2026-08-10 incident that exhausted the free tier on 61 *sequential* probes.

### 11.1 Numbers (8-org pool, this dev machine, single uvicorn process + single `--pool=solo` Celery worker)

```
chat  c=  1: p50=49ms   p95=49ms    (baseline)
chat  c=  5: p50=678ms  p95=684ms
chat  c= 10: p50=1011ms p95=1036ms
chat  c= 25: p50=1207ms p95=1514ms
chat  c= 50: p50=2220ms p95=2420ms  — 45x the baseline

wf-simple  c= 1: p50=1581ms  (dispatch + one Celery round trip)
wf-simple  c=25: p50=3700ms p95=7302ms

wf-tool    c= 1: p50=355ms
wf-tool    c=25: p50=4057ms p95=7637ms
```

Zero HTTP-level errors at any concurrency level, on either path — the platform did not fall over.
It did degrade close to linearly with concurrency, and the **two causes are identifiable, not
mysterious**:

- **Chat**: `app/db/session.py`'s `create_async_engine()` sets no `pool_size`/`max_overflow`,
  so SQLAlchemy's asyncpg default applies — a **hard ceiling of 15 concurrent DB connections**
  per process. 50 concurrent chat requests queue behind that ceiling; the queueing-shaped
  (roughly linear, not exponential) latency growth is exactly what that predicts. Single dev
  `uvicorn` process (no `--workers N`) is the other half of the ceiling.
- **Workflow runs**: `--pool=solo` is the CLAUDE.md §12-documented Windows dev convention — one
  process, no real parallelism. Both graphs plateau around 3-3.6 req/s regardless of submitted
  concurrency: this is the worker serializing, not the API. Production sizing (more workers, a
  non-solo pool) is a capacity-planning decision for §3-4 of this doc, not a code bug.

**Neither of these is fixed in this pass** — R4's ask was to produce real numbers where there
were none, not to re-tune connection pools or worker counts. `pool_size`/`max_overflow` and
Celery worker concurrency are now identified, specific knobs for whoever sizes the production
deployment, rather than an unmeasured unknown.

### 11.2 ⚠️ A real, previously-invisible bug, found only because this test disconnects like a browser does — ✅ fixed 2026-09-24 (ADR-083, RISK-REGISTER R14)

> **Update:** fixed the next day. The account below is left as originally written because it is
> what the fix was built against; the "**Not fixed in this pass**" paragraph at the end is
> superseded. Two things it got wrong or missed, found while fixing: the load test's drops
> actually take **two** shapes (`CancelledError` ~1/3, `GeneratorExit` ~2/3 — a first draft that
> handled only the first still lost 61 of 91 replies), and when the drop lands early the
> **conversation and user message are lost too**, not only the reply (the "user messages always
> survive" reading came from load-test timing, not a guarantee). Result after the fix: 91/91
> conversations, 91/91 user messages, **90/91** replies; one unexplained straggler remains.

The chat load test's own client — deliberately, and realistically — closes the SSE stream as
soon as it has read the first token, the same thing a browser tab does on navigation or a
visitor closing the page mid-reply. Every prior test in this codebase drains a stream to
completion. This one didn't, and found:

- **Every early-disconnected chat turn logged an `unhandled_exception`** (38 occurrences across
  ~150 requests): a `CancelledError` from the client disconnect propagates through
  `SecurityHeadersMiddleware`/`RequestContextMiddleware` (both `BaseHTTPMiddleware` subclasses —
  a known Starlette interaction where a client disconnect during a streamed response surfaces as
  an unhandled exception in that middleware layer, not a clean cancellation) into a SQLAlchemy
  session left mid-flush, producing a second, more confusing error
  ("*This Session's transaction has been rolled back due to a previous exception during
  flush...*").
- **⚠️ It is not just log noise — the assistant's reply is never persisted.** Queried directly:
  every one of 53 user messages sent in the concurrent chat runs has **zero** matching assistant
  message in `messages` (`role='assistant'` count = 0, `role='user'` count = 53, across all 8
  test agents). **100% reproduction, not a race.** A control request — identical setup, but the
  client reads the stream to completion instead of stopping at the first token — persisted both
  messages correctly on the first try. The HTTP status the client sees is still `200` (SSE
  headers are already on the wire before the stream breaks — the same "can't 404 from inside a
  `StreamingResponse`" shape the 2026-08-10 session log already found once for a different
  reason), so nothing in the response itself signals that the reply was lost.
- **Effect on the running system**: an operator (or the client-facing widget, or the Playground)
  who has ever navigated away, closed a tab, or refreshed mid-reply has silently lost that
  assistant turn from conversation history — not flagged anywhere until this test disconnected
  on purpose to look for it.

**Not fixed in this pass** — this needs a design decision (does a disconnected stream persist
whatever content had been generated so far, or discard it deliberately; does the middleware
order change; does `run_turn`'s persistence move earlier, before the point a cancellation can
interrupt it) that touches `app/main.py`'s middleware stack and/or `app/chat/runtime.py`'s
streaming/persistence path — the same category of "stop and flag rather than guess" this
project's own session-log convention already uses for exactly this shape of finding. Recorded
here and in `RISK-REGISTER.md` as a new, previously-undocumented gap, per this session's own
instruction to record rather than silently fix anything non-trivial found along the way.
