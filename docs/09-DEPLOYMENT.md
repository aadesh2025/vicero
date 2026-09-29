# 09 — Deployment, Infra & Ops

## 1. Dev environment (`infra/docker-compose.yml`)
Services (single command `docker compose up`):
- **postgres** — `pgvector/pgvector:pg16` image; volume `pgdata`; healthcheck `pg_isready`.
- **redis** — `redis:7`; volume; healthcheck `redis-cli ping`.
- **api** — build `apps/api`; depends_on postgres+redis healthy; runs migrations on start
  (entrypoint: `alembic upgrade head` then uvicorn); mounts code for hot reload in dev.
- **worker** — same image as api; command runs Celery worker (+ `beat` for scheduled rollups).
- **web** — build `apps/web`; `next dev`; proxies to api.
- **n8n** — `n8nio/n8n`; port 5678; volume `n8ndata`; env for basic auth + `N8N_API_KEY`.
- **ollama** — `ollama/ollama`; port 11434; volume; pull `nomic-embed-text` (+ a small chat
  model) via an init step for fully-local free operation.

All secrets/config come from `.env` (never committed). `.env.example` documents each.
Also support pointing at an **already-running** n8n/ollama via `N8N_BASE_URL`/`OLLAMA_BASE_URL`
instead of the bundled services.

## 2. Environment variables (source of truth: `docs/ENV.md`)
Groups: core (`SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `ENV`), LLM (`GROQ_API_KEY`,
`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`OLLAMA_BASE_URL`), OAuth (`GOOGLE_CLIENT_ID/SECRET`, `GITHUB_CLIENT_ID/SECRET`), channels
(`TELEGRAM_BOT_TOKEN`, `META_APP_SECRET`/WhatsApp tokens, Slack/Discord secrets), n8n
(`N8N_BASE_URL`, `N8N_API_KEY`), email (`SMTP_*` or dev console), billing (`STRIPE_*`),
observability (`SENTRY_DSN`). Every var: name, purpose, required?, default, "needs human?".

## 3. Production (`infra/docker-compose.prod.yml`)  *(implemented)*

```bash
cp .env.example .env   # set SECRET_KEY, POSTGRES_PASSWORD, DOMAIN, API_DOMAIN, ACME_EMAIL,
                       # NEXT_PUBLIC_API_BASE_URL=https://$API_DOMAIN, CORS_ORIGINS=https://$DOMAIN
cd infra && docker compose --env-file ../.env -f docker-compose.prod.yml up -d --build
```

⚠️ **`--env-file ../.env` is required, and the instructions here previously omitted it.** Two
mechanisms read env from two different files: `env_file: ../.env` supplies the *containers* their
application settings, but `${VAR}` **interpolation inside the compose file** is resolved by Compose
itself, which reads the shell and `infra/.env` — **never `../.env`**. Verified: with
`POSTGRES_PASSWORD` present in `../.env` and absent from the shell, `docker compose config` still
fails with *"required variable POSTGRES_PASSWORD is missing a value"*. It fails loudly before
anything starts, which is the one good thing about it. Affects `POSTGRES_PASSWORD`, `DOMAIN`,
`API_DOMAIN`, `ACME_EMAIL`, `NEXT_PUBLIC_API_BASE_URL`, `OLLAMA_BASE_URL` and the `*_MEM_LIMIT`
variables — pass `--env-file`, or put them in `infra/.env`.

- **Reverse proxy: Caddy** (`infra/caddy/Caddyfile`) — automatic HTTPS (Let's Encrypt) for two
  hostnames: `$DOMAIN`→web:3000 and `$API_DOMAIN`→api:8000; HSTS + security headers on the web host
  (the API sets its own strict CSP). Ports 80/443 only.
- **Non-root images:** api runs as uid 10001 (`apps/api/Dockerfile`); web is a multi-stage Next
  **standalone** build running as the `node` user (`apps/web/Dockerfile`).
- **One-shot migrations:** a `migrate` service runs `alembic upgrade head` once; api/worker start via
  `depends_on: migrate: service_completed_successfully`, so scaling replicas never re-runs migrations.
- **Healthchecks** on postgres, redis, api (`/healthz`), and web (`/login`); `restart: unless-stopped`.
- **Persistent volumes** for postgres/redis/caddy; a **backup** service runs the nightly `pg_dump`.
- **A shared `uploads` volume mounted into api *and* worker** at `UPLOAD_DIR=/app/var/uploads`.
  Not optional and not a tidiness detail: the api writes an uploaded document and the Celery
  worker reads it back to ingest it, so without one shared mount every file upload fails with
  "Stored file is missing" (docs/15 PROD-1). The persisted `DoclingDocument` is written beside
  each file, so one volume covers both.
- **An `ollama` service for embeddings**, internal-only. See §3a — this needs reading before a
  first deploy, because the failure it prevents is silent.
- **A memory limit on every service** (docs/15 PROD-3). Before this, any container could take all
  RAM and the kernel OOM-killer chose a victim host-wide — usually Postgres, since it is the
  largest resident process, and `restart: unless-stopped` then restarted into the same condition.
  With limits, an overrunning container is killed **alone** (`OOMKilled=true`, exit 137). Postgres
  and Redis additionally get memory **reservations**, which is what actually protects them: under
  host pressure the kernel reclaims from containers *above* their reservation first, whereas a
  limit only stops a service growing.
  - Defaults target the 16 GB VPS of docs/15 §4 Option 1. Every one is overridable —
    `POSTGRES_MEM_LIMIT` (3g), `WORKER_MEM_LIMIT` (3g), `OLLAMA_MEM_LIMIT` (4g),
    `API_MEM_LIMIT` (2g), `REDIS_MEM_LIMIT` (1g), `WEB_MEM_LIMIT` (1g), `MIGRATE_MEM_LIMIT` (1g),
    `BEAT_MEM_LIMIT` / `BACKUP_MEM_LIMIT` (512m), `CADDY_MEM_LIMIT` (256m), plus
    `POSTGRES_MEM_RESERVATION` (1g) and `REDIS_MEM_RESERVATION` (256m).
  - **The ceilings sum to more than the box has, on purpose.** They are ceilings, not a budget:
    sizing every service at its worst case would leave most of a 16 GB machine idle. Steady state
    is ~3.5–6 GB (docs/15 §3.1) plus ollama.
  - ⚠️ **A limit set too low is a crashloop.** `docker inspect <container> --format
    '{{.State.OOMKilled}}'` tells you in one command; raise that service's variable. Postgres is
    the one to watch — an HNSW index build is spiky and is not the steady state.
  - **No CPU limits on `api` or `ollama`**, deliberately: both serve the p50 first-token path.
    `retrieval.search()` embeds the visitor's query inline on every RAG turn, so `ollama` is
    latency-critical exactly as the reranker is — a point docs/15 §3.4's table missed. CPU caps
    belong on the batch ML services (docling-serve, ASR) when they land.
- Scale stateless tiers: `docker compose -f docker-compose.prod.yml up -d --scale api=3 --scale worker=3`
  (the realtime hub is Redis-backed — ADR-028 — so multi-replica WebSocket fan-out works).
  ⚠️ **The `uploads` volume does not survive a second machine.** Scaling replicas on one host is
  fine; moving api and worker onto different hosts needs object storage (docs/15 §4 Option 2).

### 3a. Embeddings: the one provider with no fallback  *(implemented)*

Chat has a fallback chain and degrades to a written message when a provider fails. **Embeddings
have neither**, and they are needed at *both* ends: ingesting a document and embedding the
visitor's query at retrieval time. So an unreachable embedding endpoint is not a degraded
knowledge base, it is no knowledge base.

- The stack ships **`ollama` with no published port** (it has no authentication — a published port
  is an open inference endpoint) holding **`nomic-embed-text`, 768 dimensions**, which is what the
  `chunks.embedding` column is. A different-dimension model needs a migration, not just an env var.
- **`ollama/ollama` starts empty.** It serves an API with no models, and embedding against a model
  it has not pulled is an error rather than an implicit download — so the service pulls the model
  on start and its healthcheck asserts the *model* is present, not that the port answers.
- **api and worker depend on it with `service_started`, never `service_healthy`.** Embeddings are
  required by the knowledge base, not by the platform: a worker held back by a failed model
  download would take webhooks, email and campaigns down too, whereas a queued ingest task simply
  waits in Redis.
- **On startup the API resolves the endpoint and logs loudly** —
  `embedding_provider_unreachable` / `embedding_model_missing`, error level under `ENV=prod`
  (`EMBEDDING_PROBE_ENABLED`). It never fails startup. **Check for these two lines in the first
  minute of a deploy**; before they existed, this failure looked like nothing at all.
- ⚠️ **`EMBEDDING_PROVIDER=openai` and `=gemini` are accepted and then routed to Ollama anyway** —
  no adapter for either exists. Startup warns, because there is otherwise no symptom.

## 4. Backups & data  *(implemented)*
- **`infra/scripts/backup.sh`** — `pg_dump | gzip` to a timestamped file with N-day rotation,
  **plus a `tar.gz` of the `uploads` volume** when `UPLOADS_DIR` is set (ADR-082) — the prod
  `backup` service mounts the same `uploads` volume `api`/`worker` use, read-only, at exactly
  that path, so one nightly run produces `vicero_<db>_<STAMP>.sql.gz` and
  `vicero_uploads_<STAMP>.tar.gz` together, same retention window, same volume. Run it nightly
  via cron (example in the script header) or as a one-shot container that can reach Postgres.
  `UPLOADS_DIR` unset (e.g. a bare Postgres-only environment) falls back to the DB dump alone,
  with a loud warning rather than a silent gap.
- **`infra/scripts/restore.sh <file.sql.gz> [uploads.tar.gz]`** — documented, confirmation-gated
  restore (with an optional drop+recreate for the DB). The uploads archive is optional and, when
  given, extracted to `UPLOADS_TARGET_DIR` (no default — must be set explicitly, matching the
  real `UPLOAD_DIR` the api/worker containers read) behind its own confirmation prompt. Verify
  the DB side with `SELECT count(*) FROM organizations;`.
- Uploaded files: the shared `uploads` volume (`UPLOAD_DIR`), holding each original document **and**
  the persisted `DoclingDocument` beside it. Retention + delete-per-org honored (NFR-8).
- **✅ Closed (ADR-082, 2026-09-23), verified against real Postgres and real uploaded files, not
  fixtures**: a full backup→restore round trip (48 tables, 2993 real files) matched the source
  exactly on both the DB row count and a `diff -rq` of the restored directory. Was: "`backup.sh`
  does not cover `uploads`… restoring from a backup alone leaves every `documents` row pointing
  at a `storage_path` and `docling_json_path` that are gone" (docs/15 §8.3). Moving this to
  object storage (S3-compatible) remains the eventual right move for multi-replica/k8s (§9.5,
  R11) — this fix does not block that migration, it just closes the single-box gap now.

## 5. Observability  *(implemented)*
- `/healthz` (liveness), `/readyz` (DB+Redis), **`/metrics` (Prometheus exposition** — request
  counts by method/status, a latency histogram, build info + uptime; scrape it from Prometheus).
- **Structured JSON logs** to stdout with request/org/user ids (`configure_logging`) — 12-factor,
  so any aggregator (Loki/Promtail, CloudWatch, ELK, Datadog) collects them from the container's
  stdout with no app change. Set `LOG_LEVEL` per environment.
- **Sentry** error tracking auto-initialized when `SENTRY_DSN` is set (`_init_sentry`, 10% traces,
  no PII); a no-op otherwise.
- LLM call tracing (provider/model/tokens/latency/cost/fallback) recorded on every message.

## 6. CI/CD (`.github/workflows/`)  *(implemented)*
- **ci.yml**: on push/PR — API lint (`ruff`) + typecheck (`mypy`) + `pytest` (pg+redis service
  containers), web lint (`eslint`) + typecheck (`tsc`) + `next build`, a dependency audit, and the
  **e2e** job (boots api+worker+web, runs the Playwright PRD suite, uploads the report + logs).
- **release.yml**: on push to `main` + `v*` tags — **build & push** `vicero-api`/`vicero-web`
  images to **GHCR** (`docker/build-push-action`, gha cache); a **smoke** job builds the API image,
  runs `alembic upgrade head`, starts it against pg+redis and asserts **`/readyz` → 200**; a
  **deploy** job (tags/dispatch, `environment: production`) is an SSH template gated on a
  `DEPLOY_HOST` secret that runs the prod compose (one-shot migrate) + a post-deploy `/readyz` smoke.
- Cache deps; fail the pipeline on any lint/type/test error (matches Definition of Done).

## 7. Scaling notes
- API is stateless → scale horizontally behind the proxy. Celery workers scale independently.
- Postgres connection pooling (pgbouncer optional). Redis for cache/rate-limit/queue.
- pgvector index (HNSW) tuned for recall/latency; consider Qdrant swap-in later (interface
  already abstracts the vector store).

## 8. Kubernetes (stretch, `infra/k8s/`)
Deployments for api/worker/web, StatefulSets for postgres/redis (or managed), Services,
Ingress (TLS), ConfigMaps/Secrets, HPA on api/worker, Job for migrations. Or a Helm chart.
Deferring is acceptable — record in `PROGRESS.md`.

## 9. Runbooks (`docs/` after build)
Self-host guide, restore-from-backup, rotate secrets, add an LLM provider key, connect a
channel, import n8n starter workflows, scale workers, incident basics.
