# Self-hosting Vicero

This guide takes you from a clean machine to a running Vicero stack you control. For the
day-to-day Docker workflow and Windows/host gotchas, pair it with
[`RUNBOOK-docker.md`](../RUNBOOK-docker.md). For a hardened, TLS-terminated production deploy,
see [`09-DEPLOYMENT.md`](../09-DEPLOYMENT.md) and `infra/docker-compose.prod.yml`.

## 1. Prerequisites

- Docker + Docker Compose (the simplest path — brings up the whole stack), **or**
- For local app-dev: Python 3.11 + [uv](https://docs.astral.sh/uv/), Node 20+, and Postgres 16
  (with the `pgvector` extension) + Redis 7 reachable.

## 2. Configure

```bash
cp .env.example .env
```

Then edit `.env`. The only value you **must** set for a real deployment is:

- `SECRET_KEY` — signs JWTs and encrypts stored provider keys/channel tokens. Generate one:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`. **Rotating it invalidates all
  existing sessions and every Fernet-encrypted credential** — you'd need to re-login and re-enter
  provider keys.

Everything else has a sensible default. LLM / OAuth / channel keys are **optional**: leave them
blank and that provider is stubbed with a loud startup warning while the rest of the platform
runs. See [`ENV.md`](../ENV.md) for every variable. At minimum, to actually chat you'll want one
LLM key (e.g. `GROQ_API_KEY`, free tier) — or run a local model via the bundled Ollama service.

## 3. Bring up the stack (Docker)

```bash
cd infra
docker compose up -d postgres redis      # wait until healthy
docker compose up -d                      # api, worker, web, n8n, ollama
```

Services:

| Service | URL | Notes |
|---|---|---|
| Web (dashboard) | http://localhost:3000 | Next.js app |
| API | http://localhost:8000 | FastAPI; `/readyz` should be green |
| n8n | http://localhost:5678 | automation engine (basic-auth) |
| Ollama | http://localhost:11434 | local models (pull `nomic-embed-text` for RAG) |
| Postgres | :5432 · Redis | :6379 | data + cache/broker |

Check health: `curl http://localhost:8000/readyz` → `{"status":"ready","checks":{"database":true,"redis":true}}`.

## 4. Migrate + seed

Migrations run automatically on the API container's start in production compose; for the dev
compose run them once:

```bash
cd apps/api && uv run alembic upgrade head    # or: make migrate
uv run python -m app.db.seed                   # optional demo org/agent/KB (make seed)
```

The Celery **worker** (already in compose) is required for document ingestion (RAG). Without it,
uploaded documents stay `queued`.

## 5. First run

1. Open http://localhost:3000 → **Sign up** → create your organization.
2. **Agents → New agent** → pick a provider on the Model tab (Groq if you set a key; Ollama for
   fully local) → chat in the playground.
3. **Knowledge** → create a KB → upload a PDF → wait for **ready** → enable RAG on the agent.
4. **Channels / Automations / Settings** → connect a channel, bind an n8n workflow, invite a teammate.

## 6. Local RAG with Ollama (no paid keys)

```bash
docker compose exec ollama ollama pull nomic-embed-text   # embeddings
docker compose exec ollama ollama pull llama3.1            # a chat model (optional)
```

Set the KB's embedding provider to `ollama` (default) and the agent's model provider to `ollama`.

## 7. Backups & upgrades

- **Backup:** `infra/scripts/backup.sh` runs `pg_dump` (see [`09-DEPLOYMENT.md`](../09-DEPLOYMENT.md)
  for the cron + documented restore). Uploaded document files live under `UPLOAD_DIR`.
- **Upgrade:** pull, `alembic upgrade head`, rebuild images. Migrations are additive and versioned.

## 8. Clearing ephemeral dev data

`make clean-devdata` removes throwaway `@example.com` test users/orgs and the `live_demo` flag —
it never touches seed data. To fully reset: `alembic downgrade base && alembic upgrade head && make seed`.

## Troubleshooting

- **`/readyz` not ready** — Postgres/Redis not reachable; check `docker compose ps` and the URLs in `.env`.
- **Documents stuck `queued`** — the Celery worker isn't running.
- **"No API key configured for 'groq'"** — set `GROQ_API_KEY` (or switch the agent to `ollama`).
- **Chat 401 in the browser** — the access token expired; the client auto-refreshes, or re-login.
