# docs/19 — Oracle free VPS, single-box production plan (app + database together)

> **SUPERSEDED 2026-10-02 — read `docs/24-HETZNER-PAID-VPS-PLAN.md` first.** ADR-104 replaces
> ADR-096's *vendor and CPU architecture*: the Oracle Always Free A1.Flex instance could never be
> created (~35 `Out of host capacity` failures over two days), so production moves to a paid
> Hetzner **x86** VPS in Falkenstein. **ADR-096's actual architecture decision — app and
> self-hosted Postgres together on one box — is unchanged and still correct.**
>
> Dead here: **s1** (box size), **s2** (Arm compatibility — irrelevant on x86), **s3** (12 GB
> memory limits), **s8** (go-live checklist), **s10** (risks), and **s7** (idle-reclaim — a paid
> VPS is not reclaimed). Still live and still required reading: **s4** (self-hosted Postgres),
> **s5** (what is missing from the prod compose), **s6** (backups), **s11** (what breaks silently
> — the highest-value table in these docs), **s12** (models and embeddings), **s13** (Azure).
> Full section-by-section delta: docs/24 s8.

> **Status: PLAN, NOT YET EXECUTED.** Written 2026-09-25 on the owner's instruction to run the SaaS
> and Postgres on the same Oracle Always Free VM.
>
> **Recorded as ADR-096 (2026-09-25), which supersedes ADR-086** (Supabase-managed Postgres). Reason: Supabase free = 500 MB DB, and
> `chunks.embedding` vectors (768 dims + HNSW index, roughly 6-8 KB per chunk) fill it fast; the free
> project also pauses after ~7 days idle. Self-hosted Postgres on the box has a 200 GB ceiling and no
> pause. ADR-086 is marked superseded and docs/16 carries a pointer to this doc.
>
> Everything else in `docs/16-VPS-MIGRATION.md` (firewalls, DNS, two-file env rule, CORS, widget,
> n8n, backups) still applies. This doc only covers what changed: the box size, the database
> location, Arm compatibility, and memory limits.

---

## 1. The box is smaller than docs/16 assumes

| | docs/16 assumed | Oracle Always Free now (from June 2026) |
|---|---|---|
| Shape | VM.Standard.A1.Flex, Arm | same |
| OCPU / RAM | 4 / 24 GB | **2 / 12 GB** (check YOUR console; older accounts may be grandfathered) |
| Storage | 200 GB | 200 GB (min 47 GB boot) |
| Egress | - | 10 TB/month |

Sources: docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm ; infoq.com/news/2026/07/oracle-cloud-free-tier-limits/

- **If your existing instance is already 4/24: do NOT resize it, stop it carelessly, or terminate it.**
  Reports say a terminated grandfathered instance cannot be recreated above the new 2/12 limit.
- **CPU is the binding constraint, not RAM** (docs/15 s3.4). 2 OCPU is half of what docs/16 planned on.
  Fine for a pilot and the first few clients. Watch `docker stats` CPU%, not memory.
- **Escape hatch:** if CPU saturates or Oracle capacity blocks you for more than a week, the same compose
  file runs unchanged on a EUR 4-6/month x86 VPS (Hetzner). Do not lose a month over free.

## 2. Arm (aarch64) compatibility check — DONE by reading, NOT by building

| Item | Result |
|---|---|
| `pgvector/pgvector:pg16` | linux/arm64 present (Docker Hub, checked 2026-09-25) |
| `n8nio/n8n:latest` | linux/arm64 present (checked) |
| `ollama/ollama:latest` | linux/arm64 present (checked) |
| `redis:7`, `caddy:2`, `postgres:16`, `node:20-bookworm-slim`, `uv:python3.11-bookworm-slim` | official multi-arch images; NOT individually verified |
| Python deps (`apps/api/uv.lock`, 137 packages) | 108 pure-Python; 28 native, all have aarch64 wheels except `pywin32` (Windows-only) and `ruff` (dev tool, not installed with `--no-dev`); **0 sdist-only packages** (nothing compiles from source) |
| Next.js build (`apps/web`) | `npm ci` + `next build` on Arm; needs ~2-3 GB RAM while building |

**Not verified:** an actual `docker compose build` on Arm. First deploy is the real test. Build the web
image while nothing else is busy, and keep the 4 GB swapfile (docs/16 s1.4) on.

## 3. Memory limits for a 12 GB box

The compose defaults target a 16 GB box. They are ceilings (sum > host by design). Override so the
ceilings sum to about 11 GB. Put these in `infra/.env` (or pass with `--env-file`):

```bash
POSTGRES_MEM_LIMIT=2g
POSTGRES_MEM_RESERVATION=1g
REDIS_MEM_LIMIT=512m
REDIS_MEM_RESERVATION=128m
MIGRATE_MEM_LIMIT=1g
API_MEM_LIMIT=1536m
WORKER_MEM_LIMIT=2g
BEAT_MEM_LIMIT=256m
WEB_MEM_LIMIT=1g
CADDY_MEM_LIMIT=256m
OLLAMA_MEM_LIMIT=2g
BACKUP_MEM_LIMIT=512m
```

- Steady state is expected around 4-6 GB (docs/15 s3.1, medium confidence, unmeasured).
- **Ollama stays.** It only serves `nomic-embed-text` (137M params, small). Embeddings cannot leave
  Ollama today (no other adapter, and the column is `vector(768)`). It is on the query path of every
  RAG turn, so it costs CPU per chat. Do NOT pull a chat model onto this box.
- If `docker inspect <c> --format '{{.State.OOMKilled}}'` says true or exit 137: raise that one variable.
- `worker --concurrency=4` is hard-coded in the compose file. With 2 OCPU consider 2 (edit the compose
  `command`, record it in DECISIONS.md).

## 4. Database: self-hosted Postgres in the compose stack

- Use the existing `postgres` service (pgvector/pgvector:pg16). No `DATABASE_URL` change: the compose file
  already builds it as `postgresql+asyncpg://...@postgres:5432/...`.
- Migrations: the one-shot `migrate` service runs `alembic upgrade head` on every `up`.
- No host port is published. Keep it that way. Never open 5432 in the Oracle Security List or iptables.
- Set a strong `POSTGRES_PASSWORD` (not `vicero`).
- Supabase is dropped from the critical path. It may still be used as an OFF-BOX BACKUP target (section 6).

## 5. Things missing from the prod compose (from docs/16, still true)

1. **n8n is not in `docker-compose.prod.yml`** (deliberate; see docs/16 s6). Run it from a separate compose
   file on the same Docker network, with a Caddy block. Requirements, all from the dev compose:
   - `N8N_WEBHOOK_SIGNING_SECRET` = the same value as the API's (one source: root `.env`)
   - `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, `NODE_FUNCTION_ALLOW_BUILTIN=crypto`
   - `N8N_DIAGNOSTICS_ENABLED=false` (n8n exits if telemetry DNS fails)
   - `N8N_HOST`, `N8N_PROTOCOL=https`, `WEBHOOK_URL` = the public n8n URL
   - **Change `N8N_BASIC_AUTH_PASSWORD` from the default `vicero`**
   - Do not publish 5678 to the internet except via Caddy + basic auth.
2. **CORS** for client sites (docs/16 s4): add each client origin to `CORS_ORIGINS` for now.
3. **`NEXT_PUBLIC_API_BASE_URL`** is baked at build time (docs/16 s5). Set it BEFORE `docker compose build`.
4. **`API_BASE_URL`, `WEB_BASE_URL`** must be the real https URLs or webhooks and email links break silently.

## 6. Backups (single box = single point of failure)

- The `backup` service already dumps Postgres AND archives the uploads volume nightly (ADR-082). docs/16 s7
  is stale on this point. Verify with `docker compose exec backup ls /backups` after the first night.
- **But backups land on the same disk.** Add an off-box copy the same day you go live:
  - Oracle Object Storage (Always Free includes 20 GB) via `rclone`, or
  - a small Supabase Storage bucket / any S3 target via `rclone`.
- Do a **restore drill** once (`infra/scripts/restore.sh` on a scratch machine) before a paying client.
- Never run `docker volume prune` on this box.

## 7. Idle-reclaim and account risk

Oracle may reclaim an Always Free instance if, over 7 days, CPU p95 < 20% AND network < 20% AND memory
< 20% (A1 shapes). A quiet pilot can trip this.

- Upgrading the tenancy to Pay-As-You-Go is the commonly used protection; Always Free resources stay
  free after upgrade. **Confirm Oracle's current policy in your console before relying on it.**
- Keep off-box backups regardless. Free accounts can be closed.

## 8. Go-live checklist (in order)

- [ ] Confirm the shape you actually have (2/12 or 4/24) in the Oracle console
- [ ] Create VM.Standard.A1.Flex, Ubuntu 22.04 aarch64; save the SSH key (shown once)
- [ ] "Out of host capacity"? try another availability domain / off-peak / PAYG upgrade
- [ ] Security List: allow TCP 80 and 443 only (plus 22 from your IP if possible)
- [ ] On the box: iptables ACCEPT for 80 and 443, `netfilter-persistent save` (docs/16 s1.2)
- [ ] Install docker + compose-v2 + git; add 4 GB swap (docs/16 s1.3-1.4)
- [ ] Buy or point a domain: A records for `app.`, `api.` (and `n8n.`) to the public IP BEFORE first `up`
- [ ] Copy repo to `/opt/vicero`; create root `.env` from `.env.example`:
      `ENV=prod`, new `SECRET_KEY` (`openssl rand -hex 32`), strong `POSTGRES_PASSWORD`,
      `CORS_ORIGINS`, `API_BASE_URL`, `WEB_BASE_URL`, `N8N_*`, LLM keys (Groq etc.)
- [ ] `infra/.env`: `DOMAIN`, `API_DOMAIN`, `ACME_EMAIL`, `NEXT_PUBLIC_API_BASE_URL`, the memory limits above
- [ ] `cd infra && docker compose --env-file ../.env -f docker-compose.prod.yml up -d --build`
- [ ] Watch: `docker compose ps`, `docker stats`, `docker compose logs -f caddy api worker`
- [ ] `https://api.<domain>/healthz` returns 200; dashboard loads; sign up works; verify email link points to the real domain
- [ ] Create a test agent, upload a small PDF, confirm ingestion finishes (proves uploads volume + worker + Ollama)
- [ ] Embed the widget on a test page on a DIFFERENT origin (proves CORS)
- [ ] Bring up n8n, re-bind tools, tag workflows (docs/16 s6.2)
- [ ] Off-box backup job + one restore drill
- [ ] Add uptime monitoring (free UptimeRobot on `/healthz`)

## 9. Open decision for the owner

- [x] Confirmed by the owner 2026-09-25: single-box self-hosted Postgres replaces ADR-086 (Supabase).
      Recorded as **ADR-096** in `docs/DECISIONS.md` (ADR-087 was already taken); ADR-086 is marked
      superseded and `docs/16` has a pointer here.

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| 2 OCPU Arm CPU saturation | slow chat, slow ingestion | pilot only; worker concurrency 2; move to a bigger paid VPS |
| Idle reclaim / account closure | total loss of the box | PAYG upgrade, off-box backups |
| Single box | outage = everything down | uptime monitor, restore drill, documented rebuild |
| Untested Arm build | first deploy surprises | build off-peak, keep swap, read logs |
| Default passwords (`vicero`) | takeover | change Postgres and n8n passwords before `up` |

**Confidence: Medium-High.** Sizing and Arm image checks are from Oracle docs and Docker Hub. Steady-state
RAM and CPU under real load are unmeasured until the first deploy.

---

## 11. Local -> VPS: what breaks silently (code review, 2026-09-25)

Nothing crashes at startup. These fail later, in front of a user. Template for the fixes: `.env.prod.example`.

| # | Symptom on the VPS | Cause (verified in code) | Fix |
|---|---|---|---|
| 1 | Signup / reset / magic-link emails never arrive | `EMAIL_BACKEND=console` (`core/email.py`) only prints them | `EMAIL_BACKEND=smtp` + `SMTP_*` |
| 2 | Google / Facebook login fails | `OAUTH_REDIRECT_BASE` defaults to `http://localhost:8000` (`modules/auth/oauth.py:96`) | set to `https://api.<domain>`, and register the callback URLs in Google/Meta consoles. **Not covered in docs/16.** |
| 3 | Restored DB has unreadable saved provider keys | Fernet key = SHA-256(`SECRET_KEY`) (`core/crypto.py:20`); a new `SECRET_KEY` cannot decrypt old ciphertext | start with a fresh DB, or re-enter provider keys after migrating. Also invalidates all JWTs (everyone logs in again). |
| 4 | Links and webhooks point at localhost | `API_BASE_URL`, `WEB_BASE_URL`, `CORS_ORIGINS`, `N8N_BASE_URL` default to localhost (`core/config.py`) | set all four |
| 5 | Widget renders, never replies, on client sites | CORS regex is dev-only (`main.py`) | per-client origins in `CORS_ORIGINS` (docs/16 s4) |
| 6 | "Open n8n" link on Automations page is dead | `automations/page.tsx` hard-coded `http://localhost:5678` | **FIXED 2026-09-25**: now `NEXT_PUBLIC_N8N_URL` (build arg wired in `apps/web/Dockerfile`, `docker-compose.prod.yml`, `.env.example`, `docs/ENV.md`) |
| 7 | Web calls the wrong API | `NEXT_PUBLIC_API_BASE_URL` is baked at build time | set before `docker compose build` |
| 8 | Automations do nothing | no n8n in prod compose; stored `webhook_url` rows point at localhost | docs/16 s6 |

No code sandbox exists in the app (calculator uses an AST-restricted parser; workflows forbid `eval`).
Only staff-registered MCP **stdio** servers spawn subprocesses, and the API image has no Node/`npx`,
so those will not run in the container.

## 12. Models and embeddings when online

- **Chat models:** cloud providers only (Groq, Gemini, OpenRouter, OpenAI, Anthropic). Copy the keys
  into the VPS `.env`. Do not offer the `ollama` chat provider to tenants: the VPS Ollama only pulls
  `nomic-embed-text`, and 2 Arm CPUs cannot serve a chat model.
- **Embeddings:** stay on Ollama, but the Ollama that the VPS uses is the **`ollama` container in the
  prod compose** (`OLLAMA_BASE_URL=http://ollama:11434`), not the one on your PC. It pulls
  `nomic-embed-text` on first start (~5 min, ingest fails until done). Do not publish its port: it has no auth.
- **Never change `EMBEDDING_MODEL` after data exists.** Column is `vector(768)`; a different model means
  different vectors and a full re-embed of every document.
- **Query embedding runs on every RAG turn** on the same 2 CPUs: expect more latency than on your PC.
- **If embeddings become the bottleneck:** a hosted 768-dim embedding API needs a new adapter (none exists
  today) AND a full re-embed. Only do this deliberately, as its own phase.

## 13. Azure free tier vs Oracle free tier

| | Oracle Always Free | Azure free account |
|---|---|---|
| Duration | permanent (subject to idle-reclaim) | credit $200 for 30 days; VMs free for 12 months only |
| Best VM | Arm A1: 2 OCPU / 12 GB | B1s (1 vCPU / 1 GB); B2pts v2 (Arm) / B2ats v2 (AMD) burstable, 750 h/month total across VMs |
| Storage | 200 GB | small; check portal |
| Fit for Vicero (needs ~4-6 GB steady) | yes | **no** - free VMs are 1-4 GB (sizes not stated on the page fetched; verify in portal) |

Recommendation: Oracle. Azure free VMs are too small and expire after 12 months. Source:
learn.microsoft.com/en-us/azure/cost-management-billing/manage/create-free-services
