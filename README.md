<div align="center">

# Vicero

**An original, enterprise-grade AI chatbot & automation platform.**
Build AI agents, ground them in your own knowledge base (RAG), embed a chat widget,
connect messaging channels, wire automations to n8n, and operate it all from one dashboard.

</div>

---

Vicero is a multi-tenant SaaS: each organization creates **agents** (chatbots) with a
persona, model, temperature and tools; attaches a **knowledge base** for retrieval-augmented
answers with citations; embeds a **chat widget**; connects **channels** (Telegram, WhatsApp,
Slack, Discord); binds **n8n workflows** as tools; hands off to **human operators**; and sees
**analytics**. Inspired by Botpress' feature set — original architecture, UI, API and data model.

## Feature highlights

- **Agents & versions** — draft/publish workflow, per-agent persona/model/temperature, playground.
- **Free-first LLMs** — Groq, Google Gemini, OpenRouter, Ollama (local), plus OpenAI/Anthropic; per-agent/org/env key resolution with a fallback chain.
- **Knowledge & RAG** — upload PDF/DOCX/CSV/TXT/MD/URL → chunk → embed (pgvector) → hybrid retrieval → grounded answers **with citations**.
- **Tools & automations** — built-in tools, user-defined HTTP tools (SSRF-guarded), and **n8n** workflows bound as tools (sync + async callback).
- **Channels & inbox** — signed inbound webhooks, a shared runtime, and a realtime operator inbox with human handoff.
- **Embeddable widget** — one dependency-free `widget.js`, Shadow-DOM isolated, streaming, theming, `window.Vicero` SDK.
- **Guardrails** — prompt-injection neutralization (retrieved content treated as data), blocked-topic pre-LLM refusal, output secret redaction.
- **Team & tenancy** — orgs, roles (owner/admin/editor/viewer/operator), invitations, API keys with scopes, outbound webhooks, audit log.
- **Platform admin** — staff-only console over all orgs, usage, health, and feature flags.

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 (App Router, TS), Tailwind, shadcn/ui, TanStack Query, Zustand |
| Backend | Python 3.11 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic |
| Data | PostgreSQL 16 + pgvector · Redis 7 · Celery |
| LLM | Groq-first → Gemini/OpenRouter/Ollama → OpenAI/Anthropic |
| Automation | n8n (Docker, local) over REST + signed webhooks |
| Tests | pytest · Vitest · Playwright (E2E) · GitHub Actions CI |

## Quickstart (Docker)

```bash
cp .env.example .env          # fill in at least SECRET_KEY; LLM keys optional (stubs warn loudly)
cd infra && docker compose up # postgres, redis, api, worker, web, n8n, ollama
# API → http://localhost:8000  ·  Web → http://localhost:3000  ·  n8n → http://localhost:5678
```

Then open the web app, sign up, create an organization, and build your first agent. For a full
walkthrough and the host-vs-container gotchas, see **[docs/RUNBOOK-docker.md](docs/RUNBOOK-docker.md)**.

### Local dev (no containers for app code)

```bash
make up                       # just postgres + redis + n8n + ollama
make migrate && make seed     # schema + a demo org/agent/KB
make dev-api                  # FastAPI on :8000  (+ a Celery worker for ingestion)
make dev-web                  # Next.js on :3000
```

## Documentation

- **Spec (source of truth):** [`docs/`](docs/) — PRD, architecture, DB schema, API spec, AI engine, phases.
- **Operate:** [self-host guide](docs/guides/SELF-HOST.md) · [Docker runbook](docs/RUNBOOK-docker.md) · [deployment](docs/09-DEPLOYMENT.md)
- **Integrate:** [API usage](docs/guides/API-USAGE.md) · [widget install](docs/guides/WIDGET-INSTALL.md) · [n8n setup](docs/guides/N8N-SETUP.md)
- **Quality:** [testing](docs/10-TESTING.md) · [security](docs/SECURITY.md) · [progress](docs/PROGRESS.md) · [decisions](docs/DECISIONS.md)

## Testing

```bash
cd apps/api && uv run pytest -q          # backend test suite
cd apps/web && npm run lint && npx tsc --noEmit && npm run build
cd apps/web && npx playwright test       # E2E, PRD criteria 1–7 (needs a booted stack)
```

CI (`.github/workflows/ci.yml`) runs API lint/typecheck/tests, web lint/typecheck/build, a
dependency audit, and the Playwright E2E suite against a service-container stack.

## Configuration

Every setting is an environment variable — see [`.env.example`](.env.example) and
[`docs/ENV.md`](docs/ENV.md). Provider/OAuth/channel keys are optional: if absent, that provider
is stubbed with a loud warning and the rest of the platform keeps working.

## License

See [LICENSE](LICENSE).
