# CLAUDE.md — Build Rules & Operating Contract

> This file is the **operating contract** for Claude Code building this project.
> Read this file first, then read `docs/00-README.md`, then start Phase 0 in
> `docs/08-PHASES.md`. Follow the phases **in order, task by task**.

---

## 0. Project one-liner

Build **"Vicero"** — an original, enterprise-grade, Botpress-inspired AI chatbot &
automation platform. Multi-tenant SaaS: users create AI agents (chatbots), give them a
persona/temperature/model, attach a knowledge base (RAG), embed a chat widget, connect
channels (WhatsApp/Telegram/etc.), and wire automations to a **locally-running n8n**
instance over connectors. This is an **original implementation** — inspired by Botpress'
public feature set, NOT a copy of its code, UI, or backend.

## 1. Autonomous execution — DO NOT STOP FOR APPROVAL

- Work through `docs/08-PHASES.md` **from Phase 0 to the final phase without pausing for
  approval between tasks or phases.** Do not ask "should I continue?" — continue.
- Only stop and ask the human when you hit a **hard blocker** that you genuinely cannot
  resolve yourself: a missing secret/credential that only the human can create (see §7),
  or a destructive action against real user data. Everything else: decide and proceed.
- After finishing each task, mark it done in your own TODO tracking, commit, and move to
  the next task automatically.
- If something is ambiguous, pick the **most standard, production-grade** option, write
  down the decision in `docs/DECISIONS.md`, and keep going.
- Never leave the build in a broken state at the end of a task. Every task ends with code
  that compiles/typechecks and with its tests passing.

## 2. Definition of Done (applies to EVERY task)

A task is done only when ALL are true:
1. Code compiles / typechecks with **zero errors** (`tsc --noEmit`, `ruff`, `mypy` clean).
2. Lint passes (`eslint`, `ruff`) with zero errors.
3. New logic has tests, and **the full test suite passes** (`pytest`, `vitest`).
4. If it touches UI, a Playwright check exists and passes (see `docs/10-TESTING.md`).
5. It is committed to git with a Conventional Commit message.
6. Any new env var is added to `.env.example` with a comment and to `docs/ENV.md`.

## 3. Git workflow

- Initialize git at Phase 0. Commit after **every task**, not every phase.
- Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`.
- One branch per phase is fine (`phase/03-ai-engine`) but committing to `main` directly
  is acceptable for a solo build. Never force-push.
- Tag the end of each phase: `git tag phase-03-complete`.

## 4. Tech stack (FIXED — do not substitute)

| Layer | Choice |
|---|---|
| Frontend | **Next.js 14** (App Router, TypeScript), Tailwind CSS, shadcn/ui, Framer Motion, TanStack Query, Zustand |
| Backend | **Python 3.11 + FastAPI**, Pydantic v2, SQLAlchemy 2.0 (async), Alembic |
| DB | **PostgreSQL 16** + **pgvector** extension |
| Cache/Queue | **Redis 7** (cache, rate limits, Celery broker), **Celery** for background jobs |
| Vector store | pgvector (default). Abstract behind an interface so Qdrant can be swapped in later |
| Auth | JWT access + refresh, OAuth (Google/GitHub), password (argon2), magic links |
| LLM | **Groq first**, then other free (OpenRouter free tier, Google Gemini free, Ollama local), then **OpenAI** and **Anthropic** paid |
| Embeddings | Free-first: `nomic-embed-text` via Ollama or Groq/OpenAI-compatible; fallback OpenAI `text-embedding-3-small` |
| Automation | **n8n** running in Docker locally (see §6), connected via REST + webhooks |
| Realtime | WebSockets (FastAPI) + SSE for token streaming |
| Deploy | Docker Compose (dev + prod), Nginx/Caddy reverse proxy, Kubernetes manifests as stretch |
| Tests | pytest + httpx (backend), Vitest + Testing Library (frontend unit), **Playwright** (E2E) |
| CI | GitHub Actions |

Do not introduce a different framework without recording the reason in `docs/DECISIONS.md`.

## 6. n8n integration rule

- n8n runs in Docker on the local machine (assume `http://localhost:5678`, configurable via
  `N8N_BASE_URL`). Add an `n8n` service to the dev compose file so the whole stack comes up
  together, but also support pointing at an already-running n8n instance.
- Integrate two ways: (a) Vicero **calls** n8n workflows via webhook/REST to run
  automations; (b) n8n **calls** Vicero via signed webhooks/REST API + API keys.
- Never hardcode the n8n URL or key — read from env. See `docs/07-INTEGRATIONS.md`.

## 7. Secrets Claude cannot invent (STOP and ask the human ONLY for these)

Put every one of these in `.env.example` with a placeholder and instructions. If a phase
needs one that isn't set, implement the code + a clear runtime error/log telling the human
what to add, then **continue** with a mock/stub so the build isn't blocked:

- LLM keys: `GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`
- OAuth: `GOOGLE_CLIENT_ID/SECRET`, `GITHUB_CLIENT_ID/SECRET`
- Channels: WhatsApp/Meta `META_APP_SECRET` + tokens, `TELEGRAM_BOT_TOKEN`, Twilio, Slack, Discord
- Billing (stretch): `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- Infra: `SECRET_KEY` (JWT), `N8N_API_KEY`

**Rule:** never block the whole build waiting on a key. Stub the provider, log a loud
warning, keep building. Real keys get dropped in later by the human.

## 8. Coding standards

- **Backend:** async everywhere, typed, Pydantic schemas for every request/response,
  service layer separated from routers, DB access in the service layer through org-scoped
  `_get_*` helpers (no repository layer, ADR-084), no business logic in routers. Alembic migration for every schema change. Structured logging (JSON).
- **Frontend:** server components by default, client components only when needed, all API
  calls through a typed client generated from the OpenAPI spec, no `any`, colocate tests.
- **Security:** validate all input, parametrized queries only, tenant isolation enforced at
  the query layer (every query filtered by `organization_id`), rate-limit public endpoints,
  encrypt provider API keys at rest, never log secrets. Follow `docs/02-ARCHITECTURE.md §Security`.
- **Errors:** typed error responses `{error: {code, message, details}}`, never leak stack
  traces to clients in prod.

## 9. When you finish a phase

Run the full suite (`make test`), run the Playwright smoke, update `docs/PROGRESS.md`
with what shipped, tag git, and **immediately start the next phase**. Do not wait.

## 10. Reading order

1. `CLAUDE.md` (this file)
2. `docs/00-README.md`
3. `docs/01-PRD.md`
4. `docs/02-ARCHITECTURE.md`
5. `docs/03-DATABASE-SCHEMA.md`
6. `docs/04-API-SPEC.md`
7. `docs/05-FRONTEND.md`
8. `docs/06-AI-ENGINE.md`
9. `docs/07-INTEGRATIONS.md`
10. `docs/09-DEPLOYMENT.md`
11. `docs/10-TESTING.md`
12. `docs/11-SAFETY-GUARDRAILS.md` ← **read for context, do NOT execute unprompted.** See below.
13. `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` ← **read for context, do NOT execute unprompted.** See §10b.
14. `docs/08-PHASES.md` ← then execute this, task by task, no stopping.

### 10a. The safety track (`docs/11-*`) is read-always, execute-on-request

`docs/11-SAFETY-GUARDRAILS.md` is the security specification: the threat model, the seven-layer
guardrail architecture, and the root-cause analysis behind six real failures found by red-teaming
a live agent. **Read it before touching anything under `app/chat/`, `app/rag/`, prompt assembly,
or any system-prompt text** — it explains why several counter-intuitive design choices exist, and
changing them without that context has already caused one measured fabrication regression.

It is **deliberately outside the §1 autonomous contract.** Unlike `docs/08-PHASES.md`, do not
start a safety phase on your own initiative — these phases add latency, cost, and per-turn
external calls, so each one is a decision the operator makes explicitly. Execute a phase only
when asked for it by name.

The three files and what each is for:

| File | Role |
|---|---|
| `docs/11-SAFETY-GUARDRAILS.md` | The spec. Always read; never paste as a prompt. |
| `docs/11-SAFETY-IMPLEMENTATION-PROMPT.md` | Master prompt, phases A–G. **Phase A is shipped**; its Phase B block is **superseded** by the file below. Use it for C–G. |
| `docs/11-PHASE-A1-AND-B-PROMPTS.md` | Phase A.1 and the revised Phase B, plus a **blocking prerequisite for Phase C** (guard models must resolve on the platform key, not the org's — see ADR-047/048). |

Order from here: **A.1 → B → C → D → E → F → G.** Do not start E before D is green; without the
red-team corpus there is no way to tell a real improvement from luck.

### 10b. The agentic runtime / builder track (`docs/17-*`) is read-always, execute-on-request

`docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` is the spec for Vicero's three biggest identified
product/backend gaps — a multi-step agentic tool loop, an in-app visual workflow builder, and
a pluggable integration contract — each modeled on a specific open-source reference repo
(`firecrawl/open-agent-builder`, `FoundationAgents/OpenManus`, `botpress/botpress`) but built
natively on Vicero's existing stack, not by adopting theirs. **Read it before touching
`app/chat/runtime.py`, adding any tool-calling capability, or building anything under a
`workflows`/`agent_steps` table** — it explains why the security rules in its §2 and §6 exist
and references the exact prior incidents (ADR-055, ADR-044) that justify them.

Like the safety track, it is **deliberately outside the §1 autonomous contract.** This adds a
new attack surface (multi-step tool use), new cost exposure (autonomous loops), and touches
`app/chat/` — the file docs/11 §10a already flags as needing this exact caution. Do not start
Phase 1 on your own initiative. Execute a phase only when asked for it by name.

The two files and what each is for:

| File | Role |
|---|---|
| `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` | The spec: reference-repo study notes, data model, security rules, phased scope, open questions for the human. |
| `docs/17-IMPLEMENTATION-PROMPT.md` | Master prompt, Phases 1–5. **Step 0 requires reading the three reference repos' actual source (not just READMEs) and asking the human 5 specific questions before Phase 1 starts** — do not skip this step. |

Order: **Phase 1 (Agentic Runtime) → Phase 2 (Visual Builder) → Phase 3 (Agent Testing) →
Phase 4 (Version/Approval) → Phase 5 (Integration SDK).** Do not start Phase 2 before Phase
1's red-team fixtures (new tool-result-injection cases) are green, for the same reason docs/11
gates Phase E on Phase D.

Two standing rules from that spec, repeated here because they are easy to violate by accident:

- **A prompt line is not enforcement.** Anything that must not happen needs a code-level check.
  Measured on this stack: prompt-only grounding fabricates 12/15 on `llama-3.1-8b-instant`.
- **Re-run the no-context A/B** (see the `session-log` skill's 2026-08-02 and 2026-08-03 entries) after *any* edit to
  `DEFAULT_SYSTEM_PROMPT`, `db/templates.py`, or the identity lock in `chat/assembly.py`.
  A unit test cannot see this regression.

---

## 11. Session log — now a lazy-loaded skill

> Session-by-session build history (incident postmortems, ADRs referenced, hard-won gotchas)
> used to be inlined here — 870 lines, ~85% of this file's weight, reloaded every session. It
> now lives in the `session-log` skill (`.claude/skills/session-log/SKILL.md`), loaded on
> demand instead. This section's own original policy already said full detail lives in
> `docs/PROGRESS.md` + `docs/DECISIONS.md` — this just makes the CLAUDE.md copy lazy too.

## 12. Dev-stack reality (Windows, this machine)

Each new session usually starts with Docker Desktop + services **stopped** — bring them up first:
1. **Docker Desktop** must be running, then `cd infra && docker compose up -d postgres redis` (wait healthy).
2. **API** (from `apps/api`): `./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000`
   (+ a Celery worker `--pool=solo` for ingestion). For keyless E2E, add `LLM_FORCE_FAKE=true` and
   run on port **8010** (see below).
3. **Web** (from `apps/web`): `npm run dev -- -p 3001`. Open **http://localhost:3001**.

**Port gotchas:** canonical is web **3000** / API **8000**, but on this machine **3000 is taken by
an unrelated app**, so Vicero web runs on **3001**. The keyless-E2E API runs on **8010**
(`LLM_FORCE_FAKE=true`, `AUTH_RATE_LIMIT` lifted) so it doesn't clash with a real :8000 API.
**n8n** is expected at **5678** (`N8N_BASE_URL`) — note the running `:5678` belongs to the separate
AUROZEN AI compose, not Vicero's own `n8n` service. Always run API/web commands from `apps/api` /
`apps/web` (not the repo root) to avoid `ModuleNotFoundError: app` / npm ENOENT.
