# Vicero — Build Specification (Source of Truth)

This `docs/` folder is the complete specification for **Vicero**, an original,
enterprise-grade, Botpress-inspired AI chatbot & automation platform. It is written to be
handed to **Claude Code**, which will build the entire product **phase by phase, task by
task, without pausing for approval** (see `../CLAUDE.md §1`).

## What we are building (in one paragraph)

A multi-tenant SaaS where a user signs up, creates an **organization**, builds one or more
**AI agents** (chatbots), configures each agent's **persona / system prompt / temperature /
model provider**, uploads documents into a **knowledge base** that is chunked, embedded and
retrieved via **RAG**, then deploys the agent as an **embeddable web widget** and/or connects
it to **channels** (WhatsApp, Telegram, Slack, Discord, web). Agents can **call tools**,
trigger **n8n automations** running locally in Docker, hand off to a **human inbox**, and the
whole thing is observable via **analytics**, governed by **RBAC + API keys + audit logs**, and
shipped with **Docker**. LLM providers are **free-first (Groq, Gemini free, Ollama, OpenRouter)**
with **OpenAI and Claude** as paid options, all swappable per-agent including **custom
OpenAI-compatible endpoints and user-supplied API keys**.

## Document map

| File | Contents |
|---|---|
| `01-PRD.md` | Vision, personas, functional + non-functional requirements, feature list, acceptance criteria |
| `02-ARCHITECTURE.md` | System architecture, service boundaries, request flows, security, multi-tenancy |
| `03-DATABASE-SCHEMA.md` | Full PostgreSQL schema (tables, columns, relationships, indexes, pgvector) |
| `04-API-SPEC.md` | REST API: every endpoint, auth, request/response shapes, error model, websockets |
| `05-FRONTEND.md` | Next.js app: routes, pages, design system, component inventory, state, widget SDK UX |
| `06-AI-ENGINE.md` | LLM provider abstraction (Groq-first), RAG pipeline, agent runtime, tool calling, memory |
| `07-INTEGRATIONS.md` | n8n, channels (WhatsApp/Telegram/Slack/Discord/web), connectors, webhooks, widget embed |
| `08-PHASES.md` | **The build plan** — every phase broken into ordered, self-contained tasks |
| `09-DEPLOYMENT.md` | Docker Compose, prod deploy, reverse proxy, env, backups, monitoring, CI/CD |
| `10-TESTING.md` | Testing strategy: unit, integration, Playwright E2E, acceptance gates per phase |

Supporting files Claude Code creates and maintains as it builds:
`docs/DECISIONS.md` (running ADR log), `docs/PROGRESS.md` (what shipped per phase),
`docs/ENV.md` (every env var explained).

## How Claude Code should use this

1. Read `../CLAUDE.md` and this file.
2. Read `01`–`07`, `09`, `10` to load full context.
3. Open `08-PHASES.md` and execute Phase 0 → final phase, in order, task by task.
4. Do not stop for approval. Only stop for human-only secrets (see `../CLAUDE.md §7`).
5. Keep `DECISIONS.md`, `PROGRESS.md`, `ENV.md` up to date as you go.

## Scope honesty

This is "Botpress-inspired, ~80% of the important feature surface," not a byte-for-byte
Botpress clone. It is an **original** codebase and design. The goal is a genuinely
production-quality platform delivered incrementally, not a one-shot miracle.
