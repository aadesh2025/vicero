# docs/24 — Hetzner paid VPS, single-box production plan (replaces Oracle free tier)

> **Update 2026-10-07 — what actually happened.** Production went live on a **temporary** 4 vCPU /
> 3.7 GB host ("Cloud on Fire Plus"), not on Hetzner, for 1-2 months before a proper cloud. The box
> cannot build images, so it **pulls** `ghcr.io/aadesh2025/vicero-{api,web}:master` built by the Release
> workflow (cache lines removed, trigger branch `master`) through a server-local override file. See
> **ADR-107** (host, memory limits, `--workers 1` because OAuth state is per-process) and **ADR-108**
> (encrypted daily Google Drive backups). Everything below remains the plan for the real move and is
> otherwise unchanged; the §4 memory limits do **not** apply to the temporary host.

> **Status: PLAN, NOT YET EXECUTED.** Written 2026-10-02 after Oracle Always Free ARM capacity
> failed to materialise across two days of automated retries.
>
> **Recorded as ADR-104 (2026-10-02), which supersedes ADR-096** (Oracle Always Free single box).
> ADR-096's *architecture* decision — app and self-hosted Postgres together on one box — is
> **unchanged and still correct**. Only the *box vendor and the CPU architecture* change.
>
> **Where this doc and `docs/19-ORACLE-SINGLE-VPS-PLAN.md` disagree, this doc wins.**
> Where this doc is silent, docs/19 and `docs/16-VPS-MIGRATION.md` still apply verbatim —
> §8 lists exactly which of their sections survive and which are now dead.
>
> **Prices and locations verified 2026-10-02** against hetzner.com, docs.hetzner.com and two
> independent price trackers. Hetzner raised prices in **April 2026**; any figure you remember
> from before then is wrong. **The two trackers disagree with each other (§2.2) — confirm the
> real number in the Hetzner console before committing.**

---

## 0. The headline, before anything else

**Three things, in order of how much they will change your decisions.**

1. **The architecture does not change.** `infra/docker-compose.prod.yml` is vendor-neutral. You
   are changing which machine runs `docker compose up`, not what it runs. Every one of docs/16's
   five "things the compose file cannot fix for you" (CORS, n8n-not-in-prod-compose, embeddings
   locked to Ollama, `NEXT_PUBLIC_API_BASE_URL` baked at build time, backup coverage) survives
   this move completely untouched. **Moving vendor fixes none of them.**

2. **₹500/month does not buy the box this project's own sizing calls comfortable.** docs/15 §3.1
   puts steady state at **3.5–6 GB** and says "comfortable on an **8 GB** VPS". At the rate used
   in §2.3, 8 GB on Hetzner is **≈ ₹755/month**, not ₹500. ₹500 buys **4 GB**. That is a real
   constraint with named consequences (§5), not a rounding error.

3. **You are leaving India.** Oracle Hyderabad was in-country (~20–30 ms from Chennai). Hetzner
   has no Indian region. Every option costs latency, money, or both (§3). **docs/19 never had to
   think about this because the free box happened to be in your home region.** It is the one
   genuinely new problem this move creates.

---

## 1. Why Oracle was abandoned (the record, so this is not re-litigated)

| | What happened |
|---|---|
| Target | `VM.Standard.A1.Flex`, 2 OCPU / 12 GB, ap-hyderabad-1, AD-1 |
| Method | `~/create_vicero.sh` retry loop in OCI Cloud Shell, 60 s backoff on capacity, 90 s on 429 |
| Result | **~35 attempts across two sessions over two days. Zero successes.** Every attempt returned `Out of host capacity`, interleaved with `TooManyRequests` rate limiting |
| Side effect | Two `VM.Standard.E2.1.Micro` boxes (1 OCPU / 1 GB each) were created as a stopgap and used to prove Postgres + n8n run. **They cannot host Vicero** — 1 GB against a 3.5–6 GB steady state — and were terminated 2026-10-02 |

This is exactly the failure docs/19 §1 predicted and pre-authorised an exit from:

> *"Escape hatch: if CPU saturates or Oracle capacity blocks you for more than a week, the same
> compose file runs unchanged on a EUR 4-6/month x86 VPS (Hetzner). Do not lose a month over free."*

⚠️ **Two corrections to that sentence, both found by actually doing it:**

- **"EUR 4-6/month" is pre-April-2026 pricing and is now only true for a 4 GB box.** The 8 GB box
  docs/15 actually recommends is €6.99–8.99/month inclusive of the IPv4 surcharge (§2).
- **"more than a week" was too patient given the evidence.** Oracle ARM capacity exhaustion in a
  given region is widely reported to persist for days-to-weeks and there is no signal that
  distinguishes "free tomorrow" from "free never". Two days of a 60-second retry loop producing
  zero successes is already enough information. The retry script costs nothing to leave running
  (§11) — but nothing should *wait* on it.

**What was NOT a reason to leave:** nothing about the Oracle plan's engineering was wrong. The
free box was the better machine (12 GB, 200 GB disk, in-country). It simply could not be obtained.

---

## 2. The box

### 2.1 What the application actually needs — the full compute spec

**Verified against the code on 2026-10-02, not inferred from the architecture.** Every claim in
2.1.1 is a file this doc read; the RAM figures in 2.1.3 remain docs/15's estimates and are marked
as such.

#### 2.1.1 ⚠️ GPU / VRAM: none. Zero. Not now, not for this feature set.

**Vicero is an AI product that requires no GPU.** The evidence, read directly:

| Checked | Finding |
|---|---|
| `apps/api/pyproject.toml` | **No `torch`, no `transformers`, no `onnxruntime`, no `accelerate`, no CUDA anything.** The file carries an explicit comment that `docling-core[chunking-openai]` — the tiktoken path — was chosen *"to avoid pulling `transformers`/`torch` in"*. This is a deliberate decision, already made and already recorded |
| `app/core/config.py:93` | `docling_enabled: bool = False` — the heavy document-ML path is off |
| `app/core/config.py:58-59` | `embedding_provider: "ollama"`, `embedding_model: "nomic-embed-text"` |
| `app/models/knowledge.py:18` | `EMBEDDING_DIM = 768` |

**Where the GPU work actually happens:**

| AI workload | Executes on | Cost to this box |
|---|---|---|
| Chat / agent responses (the LLM itself) | **Groq, Gemini, OpenRouter, OpenAI, Anthropic** — their hardware | **None.** Priced per API call |
| Guard models (L2/L3 safety grading) | Same cloud providers | **None** |
| Embeddings — `nomic-embed-text`, **137 M parameters** | **This box, CPU only** | ~0.5–1 GB RAM, no VRAM |
| Chart/figure VLM (Granite Vision, 4–7 GB) | **Not built.** docs/14 scope, deferred by docs/15 §4 | None |
| Audio transcription (Whisper) | **Not built.** Deferred | None |
| Self-hosted reranker (bge-reranker-v2-m3) | **Not built.** Deferred or hosted | None |

⚠️ **The one thing that would change this:** turning on `DOCLING_ENABLED` with the chart VLM, or
self-hosting a chat model, or self-hosting the reranker. docs/15 §3.3 prices that honestly at
**13–21 GB RAM and a 32 GB box minimum**, and §3.4 argues it needs its *own machine* regardless of
RAM because batch ML saturates every core it is given while the embedding path is latency-critical.
**None of that is on the roadmap for the pilot. Do not let anyone add it to this box.**

🔶 **Do not offer the `ollama` chat provider to tenants.** The VPS Ollama pulls only
`nomic-embed-text`; 2 shared vCPUs cannot serve a chat model, and nothing in the UI stops an
operator selecting it (docs/19 §12).

#### 2.1.2 CPU — the binding constraint

| Target | Cores |
|---|---|
| Absolute minimum | 2 |
| Comfortable for a pilot | 4 |
| docs/15 §4 Option 1's original specification | 8 |

**What consumes CPU, in order of how much it matters:**

1. ⚠️ **Query embedding, inline on every RAG turn.** `retrieval.search()` embeds the visitor's
   question before retrieval, so `ollama` sits directly on the **p50 first-token path** (NFR-1,
   417 ms). docs/15 §10.1 corrects §3.4 on exactly this point and is why the compose file sets
   **no CPU limit** on `api` or `ollama` — capping either adds latency to every grounded answer.
2. **Document ingestion** — chunking plus one embedding call per chunk, in Celery. Background
   work; slow is acceptable, and `--concurrency` is the knob (§4.2).
3. Postgres query execution, including HNSW vector search.
4. Next.js SSR, Caddy TLS termination — minor.

⚠️ **docs/15 §3.4 rates "CPU, not RAM, is the binding constraint" at HIGH confidence**, and says so
independently of any of the RAM estimates below. **2 vCPU on CX23 is the real compromise in that
choice — more than the 4 GB is.**

#### 2.1.3 RAM

From `docs/15-DEPLOYMENT-CAPACITY.md` §3.1, re-read 2026-10-02, **estimates, not measurements**
(docs/15 §7 rates them medium confidence):

| Service | Est. RSS | Status |
|---|---|---|
| postgres (pgvector) | 1–2 GB | estimate (⚠️ measured **46–86 MiB idle and empty** 2026-10-01 — that is *not* the loaded number) |
| redis | 0.3–0.5 GB | estimate |
| api (uvicorn, 2 workers) | 0.5–1 GB | estimate |
| worker (celery, concurrency 4) | 0.8–1.5 GB | estimate |
| beat | ~0.1 GB | estimate |
| web (Next.js) | 0.5–1 GB | estimate |
| caddy | ~0.05 GB | estimate |
| **docs/15 subtotal** | **3.5–6 GB** | |
| ollama (`nomic-embed-text` only) | 🔶 0.5–1 GB | docs/15 §3.2 guessed 1–2 GB **assuming a chat model**; §9.5 says unmeasured and "should be far smaller" |
| **n8n** | **0.6 GB** | ⚠️ **measured** 2026-10-01 — see §4.3 |
| **Realistic total** | **4.5–6.5 GB without n8n · 5–7 GB with it** | |

🔶 **Size against 4.5–6.5 GB, not against the 3.5–6 GB subtotal that usually gets quoted** — that
subtotal silently omits both ollama and n8n.

#### 2.1.4 Storage

**Your client data is the smallest thing on the disk.** The arithmetic, because it is
counter-intuitive:

| Consumer | Size | Grows with |
|---|---|---|
| Ubuntu + Docker engine | ~3 GB | no |
| **8 container images** (api, web, postgres, redis, caddy, ollama, backup, +n8n) | **~5–7 GB** | rebuilds — run `docker image prune` |
| `ollamadata` — `nomic-embed-text` weights | ~0.3 GB | no |
| `uploads` — original files (+ `.docling.json` when Docling is on) | ~2 MB per document | **documents** |
| `pgdata` — vectors: ⚠️ 768 dims × 4 bytes = **3 KB raw**, ~**6–8 KB per chunk** with the HNSW index (docs/19) | ~0.7 MB per 20-page doc | **documents** |
| `pgdata` — orgs, agents, conversations, messages, contacts | KB per conversation | usage |
| `backups` — nightly `pg_dump` + uploads tarball × `BACKUP_RETENTION_DAYS` | **a multiple of live data** | retention |

🔶 **Worked example — 100 documents of ~20 pages each:**

```
uploads        100 docs x ~2 MB                     =  200 MB
vectors        100 docs x ~100 chunks x 7 KB        =   70 MB
rows           orgs/agents/conversations/messages   =  <10 MB
                                                      --------
live data                                              ~280 MB
```

**Against 40 GB: OS + images ~10 GB, live data ~0.3 GB, 3-day backups ~1.5 GB, leaving ~28 GB
free.** Storage is comfortably the least-tight resource on this box.

⚠️ Two caveats that make it tighter than that sounds: **backups share the disk with the data**
(§6.2), and **disk resizes up but never down** (§2.6). `max_pdf_pages` defaults to **800**
(`config.py:130`), which caps the single pathological upload but says nothing about aggregate
volume — docs/15 §8.4 notes disk *alerting* is still not implemented.

#### 2.1.5 Network / egress — not a constraint

Hetzner includes **20 TB/month** on every plan in §2.2. Vicero moves text: chat payloads of a few
KB, a ~20 KB `widget.js` per visitor, small file uploads inbound. 🔶 Even a heavy month is
comfortably under 100 GB. **Egress will not be what limits this box.**

For contrast, Oracle Always Free offered 10 TB/month — so this is a doubling, not a loss.

#### 2.1.6 The spec sheet

| Resource | Required (pilot) | Comfortable | CX23 (decided) | CX33 (upgrade) |
|---|---|---|---|---|
| **GPU / VRAM** | **none** | **none** | none | none |
| **CPU** | 2 cores | 4 cores | **2 vCPU** ⚠️ | 4 vCPU ✅ |
| **RAM** | 4.5 GB | 6.5–8 GB | **4 GB + 4 GB swap** ⚠️ | 8 GB ✅ |
| **Storage** | ~12 GB | 40 GB+ | **40 GB** ✅ | 80 GB ✅ |
| **Egress** | <100 GB/mo | — | 20 TB ✅ | 20 TB ✅ |
| **n8n included?** | optional | yes | **no** (§5.1) | yes |

⚠️ **Read the ⚠️ marks as "this is the compromise", not "this is broken".** CX23 runs the product;
it runs it without margin. §2.4.1 records why that trade was accepted and what reverses it.

### 2.2 ⚠️ The two price sources disagree — do not trust either blindly

Post-April-2026 shared-vCPU pricing, **German/Finnish locations**, EUR/month, **excluding** the
**€0.50/month IPv4 surcharge** (both sources agree the surcharge exists and that an IPv6-only
server avoids it):

| Plan | Arch | vCPU | RAM | SSD | Traffic | bitdoze | costgoat |
|---|---|---|---|---|---|---|---|
| CX23 | Intel/AMD | 2 | 4 GB | 40 GB | 20 TB | €3.99 | €5.49 |
| **CX33** | **Intel/AMD** | **4** | **8 GB** | **80 GB** | **20 TB** | **€6.49** | **€8.49** |
| CX43 | Intel/AMD | 8 | 16 GB | 160 GB | 20 TB | €11.99 | €15.99 |
| CAX11 | Arm (Ampere) | 2 | 4 GB | 40 GB | 20 TB | €4.49 | €5.99 |
| CAX21 | Arm (Ampere) | 4 | 8 GB | 80 GB | 20 TB | €7.99 | €10.49 |

**The specs agree across both sources; only the prices differ**, by roughly 30%. Likely causes:
VAT treatment (German VAT is 19%), a location surcharge, or one tracker being stale. **This is not
resolvable from outside the console.** Treat the lower column as the floor and the higher as the
ceiling, and read the real number at checkout.

⚠️ **On Hetzner, Arm is more expensive than x86 at identical specs** — the opposite of the Oracle
situation, where Arm was the only free shape. This inverts docs/19 §2's entire premise: see §2.5.

### 2.3 In rupees

Converted at **€1 = ₹108** (spot, 2026-10-02 — this rate moves; recheck before budgeting):

| Plan | RAM | EUR + IPv4 | ₹/month (low) | ₹/month (high) | Fits ₹500? |
|---|---|---|---|---|---|
| CX23 | 4 GB | €4.49 – €5.99 | **₹485** | ₹647 | ⚠️ only at the low price |
| **CX33** | **8 GB** | **€6.99 – €8.99** | **₹755** | **₹971** | ❌ no |
| CX43 | 16 GB | €12.49 – €16.49 | ₹1,349 | ₹1,781 | ❌ no |

### 2.4 ⚠️ Recommendation, and it is not the one that fits the budget

**Recommended: CX33 (4 vCPU / 8 GB / 80 GB), ≈ ₹755–971/month.**

Reasons, in order:

1. **8 GB is this project's own stated comfortable number** (docs/15 §3.1). 4 GB is below the top
   of the realistic range in §2.1, which means the box is sized for the *best case* of an estimate
   that has never been measured. docs/15 §7 rates those RAM figures only **medium** confidence.
2. **The web image build needs 2–3 GB on its own** (docs/19 §2, for `npm ci` + `next build`). On a
   4 GB box with Postgres already resident, that build is the single most likely OOM in the whole
   deployment — and it happens on *every* deploy that changes the frontend, not just the first.
3. **4 vCPU vs 2.** docs/15 §3.4 argues at high confidence that **CPU, not RAM, is the binding
   constraint** on this stack, because query embedding runs inline on every RAG turn. CX33 doubles
   the cores for ~₹270 more. docs/15 §4 Option 1 originally specified **8** vCPU; 4 is a
   compromise, 2 is a bet.

**If ₹755 is genuinely not available this month: CX23 (4 GB) at ≈ ₹485 is a defensible start**,
but only with the four concessions in §5, and with the understanding that it is a *pilot box*, not
a box you put a paying client on.

⚠️ **The cheapest honest answer to "is ₹500 perfect?" is: ₹500 is enough to start and not enough
to be comfortable.** Hetzner supports in-place vertical resize (§2.6), so the decision is
reversible for the price of a reboot — which is the real reason it is safe to start small.

### 2.4.1 ⚠️ DECIDED 2026-10-02 — CX23, and why the budget version is defensible

**The owner set a hard ceiling of ₹500/month. Decision: CX23 (2 vCPU / 4 GB / 40 GB, ≈ ₹485).**

This overrides the CX33 recommendation above **on budget grounds, not engineering grounds** — the
reasoning in §2.4 stands and CX33 remains the right box the moment it is affordable. What makes
CX23 defensible rather than reckless:

| Concern from §5 | Why it is survivable on CX23 |
|---|---|
| 🔶 `next build` needs 2–3 GB and will OOM | **Removed entirely** — ⚠️ `.github/workflows/release.yml` already builds **and pushes** `vicero-api` and `vicero-web` to **GHCR** on every push to `main` (verified 2026-10-02, lines 26/29/57). The box runs `docker compose pull`, never `--build`. **The build never happens on the 4 GB machine.** This is existing infrastructure, not new work |
| n8n needs ~600 MB it does not have (§4.3) | **Deferred, not lost.** Automations are not required for a first pilot. n8n returns with the CX33 upgrade |
| Worker concurrency 4 → 2 | Slower batch ingest only. docs/15 §11.1 shows concurrency 4 on few cores mostly queues anyway |
| Swap becomes load-bearing | Accepted. 4 GB swap, §6.3 |

🔶 **Expected steady state on CX23, with n8n off and web pulled rather than built:** roughly
**2.9–3.5 GB** against 4 GB — tight, with swap as the margin. Not comfortable. Workable.

**The upgrade trigger is commercial, not technical: the first paying client pays for CX33.** At
₹755 the 8 GB box is covered by a single client at almost any price point. Until then, every
concession above is a deliberate, reversible trade — §2.6's in-place resize is what makes it so.

⚠️ **Hetzner bills hourly against a monthly cap and there is no contract.** A server deleted
mid-month stops costing money. The ₹485 is not a commitment; it is a ceiling.


### 2.5 🔶 x86 over Arm — and what that deletes

docs/19 §2 spent an entire section establishing Arm (aarch64) compatibility, and docs/16 §9 adds a
Python-wheel compilation risk on top. Both exist **only because Oracle's free shape was Arm**.

Choosing the x86 CX line on Hetzner **deletes that entire risk category**:

| Risk from docs/19 §2 / docs/16 §9 | Status on x86 |
|---|---|
| "Not verified: an actual `docker compose build` on Arm" | **Gone.** Every image in the stack is published for `linux/amd64` first |
| `uv.lock` native packages needing aarch64 wheels | **Gone.** amd64 manylinux wheels are the default build target for all 28 |
| "If `docker compose build api` fails on a missing wheel… `build-essential` is the usual fix" | **Gone.** No source compilation expected |
| 🔶 ARM CPU-only embedding latency for `nomic-embed-text`, unmeasured on Ampere | **Gone as an unknown** — x86 embedding performance is the better-trodden path. Still worth measuring (§9) |

**Net: x86 is cheaper than Arm on Hetzner *and* removes four unverified assumptions.** docs/19 §2
is now historical. Do not spend time re-verifying it.

### 2.6 🔶 Vertical resize — the property that makes starting small safe

Hetzner supports upgrading a server's plan in place (same IP, same disk, reboot required). Disk
can grow but **never shrink**, so a resize up is one-way for storage.

🔶 **Not verified in the console by this doc.** Confirm the "Rescale" action exists on the server
detail page before relying on it as the escape route from a 4 GB start. If it does, the upgrade
path CX23 → CX33 → CX43 is a reboot, not a migration, and the §5 concessions become temporary
rather than permanent.

---

## 3. ⛔ Location: the problem docs/19 never had

**This is the one decision this move forces that the Oracle plan did not.**

### 3.1 Where Hetzner actually is

Verified against docs.hetzner.com 2026-10-02:

| Location | Code | Lines available |
|---|---|---|
| Falkenstein, Germany | `fsn1` | all (Arm, Intel/AMD, AMD, dedicated) |
| Nuremberg, Germany | `nbg1` | all |
| Helsinki, Finland | `hel1` | all |
| Ashburn, Virginia, USA | `ash` | **AMD shared + dedicated only** |
| Hillsboro, Oregon, USA | `hil` | **AMD shared + dedicated only** |
| Singapore | `sin` | **AMD shared + dedicated only** |

⚠️ **The cheap CX and CAX lines exist only in Germany and Finland.** Singapore — the nearest
location to Chennai — carries only the **CPX** (AMD shared) and **CCX** (dedicated) lines, which
are the expensive ones. There is no cheap Asian option.

### 3.2 🔶 What the latency actually costs — reasoned, not measured

**Every number in this section is an estimate from general network geography. None of it has been
measured from Chennai. Measure before treating any of it as fact.**

Round-trip from Chennai:

| Server location | 🔶 RTT to Chennai | 🔶 RTT to US LLM providers |
|---|---|---|
| Oracle Hyderabad (lost) | ~20–30 ms | ~220–250 ms |
| Hetzner Germany (`fsn1`/`nbg1`) | ~130–150 ms | ~90–110 ms |
| Hetzner Singapore (`sin`) | ~60–80 ms | ~180–200 ms |

🔶 **The counter-intuitive part, and the reason this is not simply "Germany is worse":** a chat
turn is `user → server → LLM → server → user`. Vicero's chat models are **cloud providers only**
(Groq, Gemini, OpenRouter — docs/19 §12), all US-hosted. So moving the server away from the user
moves it *toward* the model, and the two legs partially cancel:

- Hyderabad: ~25 ms user leg + ~230 ms model leg ≈ **255 ms** of network before any model time
- Germany: ~140 ms user leg + ~100 ms model leg ≈ **240 ms**
- Singapore: ~70 ms user leg + ~190 ms model leg ≈ **260 ms**

🔶 **For the streamed chat path these are within noise of each other.** NFR-1's 417 ms p50
first-token budget is dominated by the model hop wherever the box sits.

⚠️ **But that cancellation does not apply to anything else, and "anything else" is most of the
product:**

| Path | Network cost of Germany vs Hyderabad |
|---|---|
| Dashboard page loads, agent builder, settings, inbox | **+110–120 ms on every request.** Pure loss, no cancellation — these never touch an LLM |
| Widget first paint on a client's site | **+110–120 ms.** Visitors are presumably Indian |
| Widget non-LLM calls (config, logo, session) | **+110–120 ms each** |
| Channel webhooks (Telegram/WhatsApp → API) | Depends on the provider's edge, not yours |
| `/healthz`, admin, API key calls | **+110–120 ms** |

**So the honest framing is: chat feels roughly the same, everything else feels slower.** For a
pilot and the first few clients that is an acceptable trade. It is a thing to revisit — with
measurements — before scaling into the Indian market seriously.

### 3.3 Recommendation

**Falkenstein, Germany (`fsn1`).** It carries the cheap CX line, it is Hetzner's primary site, and
per §3.2 the chat path — the part a visitor actually waits on — is not meaningfully worse than
Singapore would be. **Singapore costs roughly double for a latency win that only helps the
non-chat paths**, and those are not where the product lives or dies today.

🔶 Revisit if and only if measured dashboard latency turns into a complaint, or an Indian
data-residency requirement appears in a client contract. Note that the second one is a **legal**
trigger, not a performance one, and it is not satisfiable on Hetzner at all.

---

## 4. Memory limits

### 4.1 The defaults you are overriding

⚠️ Read directly from `infra/docker-compose.prod.yml` on 2026-10-02 — these are the real variable
names and real defaults, not copied from docs/19:

```
POSTGRES_MEM_LIMIT  3g     POSTGRES_MEM_RESERVATION  1g
REDIS_MEM_LIMIT     1g     REDIS_MEM_RESERVATION     256m
MIGRATE_MEM_LIMIT   1g     API_MEM_LIMIT             2g
WORKER_MEM_LIMIT    3g     BEAT_MEM_LIMIT            512m
WEB_MEM_LIMIT       1g     CADDY_MEM_LIMIT           256m
OLLAMA_MEM_LIMIT    4g     BACKUP_MEM_LIMIT          512m
```

They sum to **16.25 GB on purpose** — docs/15 §10 explains these are ceilings, not a budget, and
that sizing every service at its worst case would leave most of the machine idle. **Overriding
them is mandatory on any box smaller than 16 GB.**

⚠️ Note one drift: docs/19 §3 lists `BEAT_MEM_LIMIT=256m`. The compose **default** is `512m`.
docs/19 was proposing an override, not quoting the default. Both are fine; the number below is
deliberate.

### 4.2 For CX33 (8 GB) — recommended

Put in `infra/.env` (these are `${VAR}` interpolation, so `../.env` will **not** work — docs/16 §3):

```bash
POSTGRES_MEM_LIMIT=2g
POSTGRES_MEM_RESERVATION=1g
REDIS_MEM_LIMIT=512m
REDIS_MEM_RESERVATION=256m
MIGRATE_MEM_LIMIT=1g
API_MEM_LIMIT=1536m
WORKER_MEM_LIMIT=1536m
BEAT_MEM_LIMIT=256m
WEB_MEM_LIMIT=768m
CADDY_MEM_LIMIT=256m
OLLAMA_MEM_LIMIT=1g
BACKUP_MEM_LIMIT=512m
```

Ceilings sum to ~9.6 GB against 8 GB of RAM — intentionally over, same reasoning as the stock
file. 🔶 Expected steady state 4.5–6.5 GB. Keep 4 GB of swap (§6.3).

Also set, in the same file, per docs/15 §11.1's finding that `--concurrency=4` on a small box just
queues:

```bash
# edit the worker `command:` in docker-compose.prod.yml, record in DECISIONS.md
# celery ... --concurrency=2
```

### 4.3 ⚠️ For CX23 (4 GB) — and the n8n number that makes it hard

```bash
POSTGRES_MEM_LIMIT=1g
POSTGRES_MEM_RESERVATION=512m
REDIS_MEM_LIMIT=256m
REDIS_MEM_RESERVATION=128m
MIGRATE_MEM_LIMIT=768m
API_MEM_LIMIT=1g
WORKER_MEM_LIMIT=1g
BEAT_MEM_LIMIT=192m
WEB_MEM_LIMIT=512m
CADDY_MEM_LIMIT=128m
OLLAMA_MEM_LIMIT=768m
BACKUP_MEM_LIMIT=256m
```

⚠️ **n8n does not fit in what is left, and this is now measured rather than guessed.**

**First real n8n measurement this project has** (VICERO-DATA, E2.1.Micro, 2026-10-01, `n8nio/n8n`
latest, Postgres alongside):

| Limit set | Result |
|---|---|
| **300 MB** | **Crash loop.** `FATAL ERROR: Reached heap limit — JavaScript heap out of memory`, container restarting every ~40 s, port accepting connections then resetting. Symptom at the browser was `ERR_CONNECTION_REFUSED` / `Empty reply from server` — **it does not look like an OOM from outside** |
| **600 MB** | **Stable.** Steady state **292 MiB / 600 MiB (48%)**, no restarts |

Same run, for the record: **`pgvector/pgvector:pg16` idled at 46–86 MiB** — far below the 1–2 GB
docs/15 §3.1 estimates, because that estimate is for a loaded database with `shared_buffers` in
use, not an empty one. **Do not read 46 MiB as the production number.**

**Consequence: n8n needs a ~600 MB ceiling, and on a 4 GB box there is no room for it.** This is
the first of the §5 concessions and the sharpest one, because n8n *is* a product feature — the
Automations tab, the bound-tool runtime, and `docs/16` §6.2's whole webhook story depend on it.

### 4.4 Diagnosing a limit set too low

Unchanged from docs/15 §10, repeated because it is the one command that saves an afternoon:

```bash
docker inspect <container> --format '{{.State.OOMKilled}}'   # true, or exit 137
```

`true` means the limit is too low — raise that one variable. It does **not** mean a bug. Postgres
is the one to watch: an HNSW index build is spiky and is not the steady state.

---

## 5. ⚠️ What a 4 GB box forces you to give up

Only read this if you choose CX23 over CX33. **Each of these is a product or safety concession,
not a tuning tweak.**

1. **n8n comes off the box** (§4.3). Options, worst to best: drop automations from the pilot
   entirely; run n8n on a second tiny VPS (which erases most of the savings); or keep it on your
   PC for development only and accept that **production automations do not exist**. ⚠️ Whichever
   you pick, `N8N_BASE_URL` must point somewhere real or you reproduce PROD-2 exactly — correct in
   dev, silently broken in production, no symptom naming the cause (docs/16 §6).
2. **Worker concurrency drops to 2.** Ingestion of a multi-document upload gets slower. Acceptable;
   docs/15 §11.1 shows concurrency 4 on few cores mostly queues anyway.
3. **⚠️ Nothing is built on the box — this is decided, not optional.** `next build` wants 2–3 GB
   (docs/19 §2); with Postgres resident on 4 GB that is an OOM on *every* frontend deploy, not
   just the first. **Resolved by using CI you already have:** `.github/workflows/release.yml`
   builds and pushes `vicero-api` and `vicero-web` to **GHCR** on every push to `main` (verified
   2026-10-02). The box therefore runs:

   ```bash
   docker compose --env-file ../.env -f docker-compose.prod.yml pull
   docker compose --env-file ../.env -f docker-compose.prod.yml up -d
   ```

   ⚠️ **never `--build` on a 4 GB box.** Two consequences to accept: (a) `NEXT_PUBLIC_API_BASE_URL`
   is baked at **build** time, so it must be set as a build arg **in the GitHub Actions workflow**,
   not in the box's `.env` — setting it on the server does nothing (docs/16 §5); (b) deploying a
   frontend change now means pushing to `main` and waiting for CI, not rebuilding on the server.
4. **Swap stops being optional and starts being load-bearing** (§6.3). A box that depends on swap
   to stay up is a box that gets slow instead of crashing — better than crashing, worse than
   fitting.

**None of these apply on CX33.** That is what the extra ₹270/month buys.

---

## 6. Disk, and why 40 GB is tighter than it looks

### 6.1 What consumes it

| Consumer | 🔶 Size | Grows? |
|---|---|---|
| Ubuntu + Docker + images (api, web, postgres, redis, caddy, ollama, n8n) | 6–10 GB | on each rebuild (prune) |
| `ollamadata` — `nomic-embed-text` weights | ~0.3 GB | no |
| `pgdata` — all tenants **and** `chunks.embedding` vectors | starts small | **yes, with every document** |
| `uploads` — originals **plus** the `.docling.json` beside each | starts small | **yes, fastest of all** |
| `backups` — nightly `pg_dump` + uploads tarball × `BACKUP_RETENTION_DAYS` | **a multiple of the above** | yes |

⚠️ **The backup volume is the trap.** docs/16 §7 established that backups land on the **same disk**
as the data. With a 7-day retention that is roughly **8× your live data footprint** on the same
40 GB. Oracle's 200 GB absorbed that carelessly; 40 GB will not.

### 6.2 Consequence

- **CX33's 80 GB is the comfortable number**, consistent with §2.4 recommending it anyway.
- On 40 GB, **lower `BACKUP_RETENTION_DAYS` to 3** and move the off-box copy from "the same day you
  go live" (docs/19 §6) to **before** you go live.
- Disk can be resized **up** but never down (§2.6). Starting at 40 GB does not trap you; it just
  makes the upgrade one-way.

### 6.3 Swap

Unchanged from docs/16 §1.4 and still worth doing on either box:

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

On CX33 this is insurance against a simultaneous spike. On CX23 it is part of the design (§5.4).

---

## 7. ⚠️ What this move does NOT fix

**Every one of these survives the vendor change intact. Re-read them before deploying; none of
them are Oracle-specific and none are solved by paying for a better machine.**

| # | Issue | Where it is written up |
|---|---|---|
| 1 | **CORS blocks the widget on every client site.** Option A (list each client origin in `CORS_ORIGINS`) is still the recommended start | docs/16 §4 |
| 2 | **n8n is deliberately not in `docker-compose.prod.yml`.** Separate compose file, same network, own Caddy block | docs/16 §6, docs/19 §5.1 |
| 3 | **Embeddings cannot leave Ollama.** `EMBEDDING_PROVIDER=openai` is accepted and routed to Ollama anyway | docs/16 §8, docs/19 §12 |
| 4 | **`NEXT_PUBLIC_API_BASE_URL` is baked at build time.** Set it before `docker compose build` or every client's embed snippet is wrong | docs/16 §5 |
| 5 | **The two-file env rule.** `--env-file ../.env` is not optional; `${VAR}` interpolation reads `infra/.env`, never `../.env` | docs/16 §3, docs/15 §10.3 |
| 6 | **Eight things that break silently in production** — console email backend, `OAUTH_REDIRECT_BASE` at localhost, `SECRET_KEY` rotation making stored provider keys undecryptable, localhost `API_BASE_URL`/`WEB_BASE_URL`, dead n8n link, stored `webhook_url` rows | **docs/19 §11 — the single highest-value table in the docs. Read it in full.** |
| 7 | **Backups land on the same disk.** Off-box copy + one restore drill before a paying client | docs/16 §7, docs/19 §6 |
| 8 | **Groq free tier is exhaustible** and one shared platform key serves every client and both guard models | docs/16 §15 |
| 9 | **R2 (KB PII audit), R3 (no pen-test), R5–R8** remain open | `docs/RISK-REGISTER.md` |

---

## 8. Section-by-section delta against docs/19

So the two documents do not quietly contradict each other:

| docs/19 section | Status under this plan |
|---|---|
| §1 box size (2 OCPU / 12 GB free) | **Dead.** Replaced by §2 here |
| §1 "do not terminate a grandfathered 4/24 instance" | **Moot.** No Oracle instance exists; both E2 micros terminated 2026-10-02 |
| §2 Arm compatibility | **Dead on x86.** Historical only — see §2.5 |
| §3 memory limits for 12 GB | **Dead.** Replaced by §4 here |
| §4 self-hosted Postgres in the compose stack | **Unchanged and still correct.** This is ADR-096's actual decision and ADR-104 preserves it |
| §5 missing from prod compose (n8n, CORS, build-time URL, base URLs) | **Unchanged.** See §7 |
| §6 backups | **Unchanged, but tighter** — see §6.2 on retention and 40 GB |
| §7 idle-reclaim and account risk | **Dead, and good riddance.** A paid VPS is not reclaimed for being quiet. **This removes a whole category of "wake up to no server"** |
| §8 go-live checklist | **Superseded by §9 here** (no Security List, no iptables trap, different key flow) |
| §10 risks | **Superseded by §10 here** |
| §11 "what breaks silently" | **Unchanged and still the most important table in the docs** |
| §12 models and embeddings | **Unchanged.** Cloud chat providers, Ollama embeddings, never change `EMBEDDING_MODEL` after data exists |
| §13 Azure comparison | **Still valid as written** — Azure free VMs are too small, and the conclusion now reads as "neither free tier works", which is how this doc came to exist |

**docs/16 is unaffected except its §1 (Oracle provisioning), which §9 below replaces.**

---

## 9. Go-live checklist

Ordered. Each line is a thing that, skipped, fails later rather than immediately.

**Provision**
- [ ] Sign up at hetzner.com/cloud; complete identity/payment verification (🔶 new accounts are
      sometimes held for manual review — budget a day, do not plan a launch around same-day access)
- [ ] **Read the real price at checkout** and reconcile against §2.2's disagreeing sources
- [ ] Create server: **CX33** (or CX23 per §2.4), **Falkenstein `fsn1`** (§3.3), **Ubuntu 22.04**,
      **x86**
- [ ] Add your SSH **public** key during creation — the same `ssh-key-2026-10-01.key.pub` already
      in use is fine; reusing a public key across providers is safe
- [ ] Note the IPv4. Decide now whether to pay the €0.50 for it (you need it — DuckDNS/A records
      and Let's Encrypt HTTP-01 both want IPv4)

**Harden** — ⚠️ *Oracle's two-firewall trap (docs/16 §1.2) does NOT apply here.* Hetzner Ubuntu
images do not ship restrictive iptables, and the Cloud Firewall is opt-in. One layer, not two.
- [ ] Hetzner Cloud Firewall (console): allow **22, 80, 443** inbound. Nothing else
- [ ] ⚠️ **Never expose 5432, 6379, 11434 or 5678.** Postgres/Redis publish no host port by design;
      Ollama has no authentication at all; n8n goes behind Caddy
- [ ] `sudo apt update && sudo apt install -y docker.io docker-compose-v2 git`
- [ ] `sudo usermod -aG docker $USER && newgrp docker`
- [ ] 4 GB swap (§6.3)
- [ ] Set `/etc/needrestart/conf.d/50-autorestart.conf` to `$nrconf{restart} = "a";` — otherwise
      every `apt` run opens an interactive dialog over SSH (learned the hard way 2026-10-01)

**DNS — before the first `up`**
- [ ] A records for `app.`, `api.` (and `n8n.` if used) → the server IPv4
- [ ] Free option while no domain is owned: a DuckDNS subdomain works and still gets a real
      Let's Encrypt certificate. 🔶 Verify Let's Encrypt is not rate-limited on the duckdns.org
      parent before depending on it for a client demo
- [ ] ⚠️ **Point DNS before `docker compose up`.** Caddy requests certificates on boot via HTTP-01;
      unresolved DNS means a backoff you will misread as a Caddy bug

**Configure**
- [ ] `git clone` to `/opt/vicero`
- [ ] Root `.env`: `ENV=prod`, fresh `SECRET_KEY` (`openssl rand -hex 32`), strong
      `POSTGRES_PASSWORD` (**not** `vicero`), `API_BASE_URL`, `WEB_BASE_URL`, `CORS_ORIGINS`,
      `N8N_*`, LLM keys
- [ ] `infra/.env`: `DOMAIN`, `API_DOMAIN`, `ACME_EMAIL`, `NEXT_PUBLIC_API_BASE_URL`, and the
      memory limits from §4.2 or §4.3
- [ ] ⚠️ Put `SECRET_KEY` in a password manager **now**. It signs JWTs *and* encrypts stored
      provider keys; rotating it later makes every saved credential undecryptable (docs/19 §11.3)

**Deploy**
- [ ] `cd infra && docker compose --env-file ../.env -f docker-compose.prod.yml config` — fails
      loudly on any missing variable, before anything starts
- [ ] ⚠️ **On CX23 (decided box): `pull`, never `--build`** (§5.3) —
      `docker compose --env-file ../.env -f docker-compose.prod.yml pull && … up -d`.
      Set `NEXT_PUBLIC_API_BASE_URL` as a build arg in `release.yml` **before** that CI build runs
- [ ] (CX33 only) `docker compose --env-file ../.env -f docker-compose.prod.yml up -d --build`
- [ ] First boot is slow and one service looks broken while it is not: `ollama` pulls
      `nomic-embed-text` with a 300 s healthcheck `start_period`. `worker` depends on it with
      `service_started`, not `service_healthy`, deliberately. A queued ingest waiting is a delay,
      not an outage
- [ ] ⚠️ **Check the first minute of API logs for `embedding_provider_unreachable` /
      `embedding_model_missing`.** Before these lines existed this failure looked like nothing
      at all (docs/09 §3a)

**Verify, in dependency order** — each proves the previous
- [ ] `docker compose ps` — all up; `migrate` showing `Exited(0)` is correct
- [ ] `curl https://api.<domain>/healthz` → 200
- [ ] `curl -I https://app.<domain>/widget.js` → 200, JS content type
- [ ] Sign up / log in on the dashboard (proves TLS + the `secure` cookie — ⚠️ this **cannot** work
      over plain HTTP; testing on the raw IP gives a login that succeeds then bounces, which is
      the cookie being refused, not the AuthGate bug)
- [ ] Invite a user and click the link in the **real email** (proves `WEB_BASE_URL` + SMTP)
- [ ] Create an agent, upload a PDF, watch it reach `ready` (proves the shared uploads volume +
      Ollama + worker — this is the path PROD-1 broke)
- [ ] Ask a question that needs the PDF; confirm citations (proves the whole RAG path)
- [ ] Embed the widget on a **different** origin (proves §7.1 — **expect this to fail first**)
- [ ] n8n up, tools re-bound, workflows tagged (docs/16 §6.2) — or consciously skipped per §5.1
- [ ] `docker compose exec backup sh /scripts/backup.sh`, confirm a file lands
- [ ] Off-box backup copy configured, and **one restore drill** before any paying client
- [ ] Free uptime monitor on `/healthz`

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **4 GB is genuinely too small** (if CX23 chosen) | OOM kills, crash loops, no n8n | §4.3 limits, §5 concessions, swap; resize to CX33 (§2.6) |
| 🔶 **Latency from India** | Dashboard and widget feel slower; chat roughly unchanged | Measure before assuming. Singapore exists at ~2× cost (§3) |
| **Recurring cost on a student budget** | Service stops if a payment fails | Calendar reminder; keep the Oracle retry script alive as a free fallback (§11) |
| **Single box, no failover** | Outage = everything down | Uptime monitor, off-box backups, documented rebuild. Unchanged from docs/19 |
| **Backups on the same disk** | Disk loss takes data *and* backups | Off-box copy **before** go-live on 40 GB (§6.2) |
| 🔶 **Price sources disagree by ~30%** (§2.2) | Budget off by ₹160–220/month | Read the console at checkout |
| **Hetzner account verification delay** | Cannot deploy same-day | Sign up before you need it |
| **Default passwords (`vicero`)** | Takeover | Change Postgres and n8n passwords before the first `up` |
| **EUR/INR movement** | A ₹755 box becomes ₹800+ | Treat §2.3 as a snapshot, not a contract |

---

## 11. Keep the Oracle path alive — it costs nothing

Do **not** delete the OCI account, the VCN, or `~/create_vicero.sh`.

- The retry script can keep looping in Cloud Shell. If 2 OCPU / 12 GB ever lands, that is a **free
  12 GB in-country box** — better than CX33 on every axis except reliability of acquisition.
- Best use if it arrives: **not** a migration back on day one, but a **staging box**, or the
  off-box backup target (Oracle Object Storage Always Free includes 20 GB — docs/19 §6).
- Moving back later is the same operation as moving out: `pg_dump` + the `uploads` tarball + a DNS
  change (docs/16 §12). ⚠️ With the same `SECRET_KEY` carried across, or every stored provider
  credential becomes undecryptable (docs/19 §11.3).
- ⚠️ **Nothing should block on it.** That is the mistake this doc exists to stop repeating.

---

## 12. Confidence

**High — that the architecture survives the move.** `docker-compose.prod.yml` is vendor-neutral and
was read directly; §4.1's variable names and defaults are quoted from the file, not from docs/19.
Nothing in the stack depends on Oracle.

**High — that x86 removes the Arm risk category** (§2.5). Structural, not empirical.

**High — the n8n measurement** (§4.3). Observed directly on a real box: crash-looped at a 300 MB
ceiling with an explicit V8 heap-OOM in the logs, stable at 600 MB using 292 MiB. This is the
first real number this project has for n8n and it contradicts nothing — docs/15 simply never
costed it.

**Medium — the sizing recommendation** (§2.4). It rests on docs/15 §3.1, which docs/15 §7 itself
rates medium confidence and which has never been measured under load. The *direction* (8 GB over
4 GB) is safe; the exact ceilings in §4.2 are starting points to tune with
`docker inspect … OOMKilled`.

**Medium — the prices** (§2.2, §2.3). Two independent trackers, agreeing on specs and on the IPv4
surcharge, disagreeing ~30% on price. The INR figures additionally ride a spot exchange rate.

**Low — every latency figure in §3.2.** Reasoned from network geography, measured from nowhere.
Directionally reliable (Germany is further from Chennai than Hyderabad; the US model hop partly
cancels it). Numerically, do not plan against them. **Measure a real first-token time from Chennai
on day one** — it is one `curl -w` away and it turns the largest unknown in this doc into a fact.

**Not assessed:** actual client document volume, concurrent tenant count, upload frequency. Every
sizing number is sensitive to these and none exist yet. Unchanged from docs/15 §7 — it remains
information only the owner can supply, and the first real client supplies it.
