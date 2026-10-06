# 02 — System Architecture

## 1. High-level diagram (text)

```
                       ┌─────────────────────────────────────────────┐
   End users ───────►  │  Web Widget (packages/widget)  &  Channels   │
   (browsers,          │  WhatsApp / Telegram / Slack / Discord       │
    messaging apps)     └───────────────┬─────────────────────────────┘
                                        │  HTTPS / webhooks / WS
                       ┌────────────────▼─────────────────────────────┐
   Dashboard users ─►  │  Next.js 16 web app (apps/web)               │
                       │  - dashboard, agent builder, KB, inbox, etc. │
                       └────────────────┬─────────────────────────────┘
                                        │  REST + WS (typed client)
                       ┌────────────────▼─────────────────────────────┐
                       │  FastAPI backend (apps/api)                  │
                       │  Routers → Services → SQLAlchemy session     │
                       │  Auth · Orgs/RBAC · Agents · KB · Chat ·     │
                       │  Tools · Channels · Inbox · Analytics · Admin│
                       └───┬──────────┬──────────┬─────────┬──────────┘
                           │          │          │         │
                ┌──────────▼──┐  ┌────▼────┐ ┌───▼────┐ ┌──▼─────────┐
                │ PostgreSQL  │  │  Redis  │ │ Celery │ │  LLM layer │
                │ + pgvector  │  │ cache/  │ │ workers│ │ Groq/Gemini│
                │ (tenant DB) │  │ queue   │ │ ingest │ │ Ollama/... │
                └─────────────┘  └─────────┘ └───┬────┘ └────────────┘
                                                 │
                                        ┌────────▼─────────┐
                                        │  n8n (Docker,    │
                                        │  localhost:5678) │
                                        └──────────────────┘
```

## 2. Services & responsibilities

### apps/api (FastAPI) — modular monolith
Organized by **domain**. The CRUD-style domains live in `app/modules/<name>/` with `router.py`,
`service.py`, `schemas.py` (+ `deps.py` where needed); there is **no per-module `repository.py`** —
services query through the SQLAlchemy session directly (ADR-084). ORM models live together in
`app/models/`. The engine/adapter domains sit beside `modules/` as top-level packages
(`chat`, `channels`, `tools`, `workflows`, `webhooks`, `rag`, `llm`, `contacts`, `crm`, `realtime`,
`worker`, `integrations`). The bullets below are logical domains, not directory names. Import
boundaries are enforced by `apps/api/tests/test_architecture.py`.

- `auth` — signup/login/oauth/magic-link, JWT, sessions, password reset.
- `orgs` — organizations, memberships, invitations, RBAC enforcement.
- `agents` — agent CRUD, persona, model config, versioning, publish.
- `knowledge` — documents, upload, ingestion orchestration, chunks, retrieval.
- `chat` — conversations, messages, streaming runtime, memory, orchestration of LLM+RAG+tools.
- `tools` — built-in tools, HTTP tools, n8n tool binding, execution + logs.
- `channels` — widget config, WhatsApp/Telegram/Slack/Discord webhooks & senders.
- `inbox` — human handoff, assignment, operator replies.
- `analytics` — metrics aggregation, usage/cost metering.
- `apikeys` — programmatic API keys.
- `webhooks` — outbound event emission, inbound signature verification.
- `admin` — platform-level console.
- `billing` — Stripe hooks (optional/stretch).

Cross-cutting: `core/` (config, security, db session, dependencies, logging, errors,
rate-limit, pagination), `llm/` (provider abstraction), `rag/` (pipeline), `integrations/`
(n8n client, channel clients).

**Why modular monolith, not microservices:** one deployable, simplest to build and run on
one machine; module boundaries make later extraction possible. Record in `DECISIONS.md`.

### apps/web (Next.js) — dashboard + marketing
App Router. Server components fetch via a typed API client generated from the backend
OpenAPI schema. Client components for interactive builder/chat. Auth via httpOnly cookies
holding the access token (refresh handled by an API route/proxy). See `05-FRONTEND.md`.

### packages/widget — embeddable SDK
Framework-agnostic TypeScript, bundled to a single `widget.js` + `widget.css`. Loads config
by public agent key, opens a WS/SSE chat session against the public chat API. See `07 §Widget`.

### Background workers (Celery)
Document ingestion (parse→chunk→embed), long-running tool calls, webhook delivery with
retries, analytics rollups, scheduled jobs. Redis is the broker + result backend.

## 3. Key request flows

### 3.1 Chat message (RAG + tools + streaming)
1. Client sends message to `POST /v1/agents/{id}/chat` (or over WS) with conversation id.
2. The conversation service (`modules/conversations/service.py`, or `chat/inbound.py` for channel messages) loads agent config, conversation history, applies token budget.
3. If RAG enabled: embed the query → pgvector similarity search → assemble context + citations.
4. Build prompt (system persona + retrieved context + memory + history + user turn).
5. Call LLM via `llm` layer (provider = agent config; fallback chain on error).
6. If the model requests a tool call → `tools.service` executes (built-in/HTTP/n8n/MCP) → feed
   result back → another provider pass, up to `tool_max_iterations` and the agentic budget
   (`chat/runtime.py::run_turn`, `docs/06 §3`).
7. Stream tokens to client via SSE/WS; persist assistant message, tool logs, usage/cost.
8. Emit `message.created` webhook; update analytics counters.

### 3.2 Document ingestion
1. `POST /v1/knowledge/{kbId}/documents` (multipart) → store file, create `document` row
   (status=queued) → enqueue Celery task.
2. Worker: detect type → extract text → chunk (size/overlap from KB config) → embed each
   chunk via embedding provider → insert `chunk` rows with vectors → status=ready.
3. On failure: status=failed with error; retriable.
4. Progress surfaced via a status endpoint / WS event.

### 3.3 Channel inbound (e.g., Telegram)
1. Telegram → `POST /v1/channels/telegram/{channelId}/webhook` (verify secret).
2. Map to/create a conversation for that channel user → run chat flow (3.1) → send reply
   back through the channel client. Handoff pauses bot and routes to inbox.

### 3.4 n8n automation as a tool
1. Agent decides to call tool `create_ticket` (bound to an n8n workflow webhook).
2. `integrations/n8n_client` POSTs args to the workflow's webhook URL (signed) → awaits
   response (sync) or accepts async callback → returns result to the model. Logged in `tool_runs`.

## 4. Multi-tenancy & isolation

- **Model:** shared database, shared schema, **row-level tenant scoping** by `organization_id`
  on every tenant-owned table.
- **Enforcement (as built, ADR-084):** a FastAPI dependency (`modules/orgs/deps.py::current_org`) resolves
  the caller's `OrgContext` from the JWT/API key and rejects non-members. Every service function
  then fetches its root resource through an org-scoped `_get_*` helper (`WHERE organization_id = ctx.org.id`)
  and queries child rows (messages, chunks, runs) by that already-verified parent. This is a
  convention, not a structural guard: `db/repository.py::BaseRepository` exists but no service uses it.
  Cross-tenant behavior is covered by per-module tests. **Postgres Row-Level Security is not implemented.**
- **Public surfaces** (widget, channel webhooks) resolve org via the agent's public key /
  channel id, never trust client-supplied org ids.

## 5. Security (implement — this is NFR-3)

- **AuthN:** argon2 password hashing; JWT (short-lived access ~15m + rotating refresh ~30d);
  OAuth state/PKCE; magic-link tokens single-use + TTL.
- **AuthZ:** RBAC permission matrix (below), enforced by dependencies/decorators.
- **Secrets at rest:** provider API keys and channel tokens encrypted with Fernet/AES-GCM
  using `SECRET_KEY`-derived key; never returned in full via API (masked).
- **Transport:** HTTPS enforced in prod; secure, httpOnly, sameSite cookies.
- **Input:** Pydantic validation on everything; strict file-type/size checks on uploads;
  SSRF protection on HTTP tools and URL ingestion (block internal IP ranges/metadata).
- **Injection:** SQLAlchemy parametrized queries only; no string-built SQL.
- **XSS:** sanitize markdown rendered in widget/inbox; CSP headers.
- **Rate limiting:** Redis token-bucket on auth, chat, and public endpoints.
- **Webhooks:** HMAC signatures (outbound) + signature verification (inbound); replay
  protection via timestamp + nonce.
- **Audit:** write to `audit_logs` on sensitive mutations.
- **Prompt-injection awareness:** treat retrieved/tool content as untrusted; keep system
  instructions authoritative; guardrail layer for blocked topics.

## 6. RBAC permission matrix

| Capability | owner | admin | editor | viewer | operator |
|---|---|---|---|---|---|
| Manage org / billing | ✓ | | | | |
| Invite / manage members | ✓ | ✓ | | | |
| Create / edit / delete agents | ✓ | ✓ | ✓ | | |
| Manage knowledge base | ✓ | ✓ | ✓ | | |
| Manage tools / channels / API keys | ✓ | ✓ | ✓ | | |
| View analytics | ✓ | ✓ | ✓ | ✓ | ✓ |
| View agents / KB (read) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Handle inbox / handoff | ✓ | ✓ | ✓ | | ✓ |
| Platform admin console | (platform staff only, separate `is_staff` flag) |

## 7. Configuration & environments

- 12-factor config via env vars, loaded by Pydantic Settings. `dev`, `test`, `prod` profiles.
- Feature flags in DB/config for risky features (channels, billing).
- All env vars documented in `docs/ENV.md`; placeholders in `.env.example`.

## 8. Error model

All API errors: `{"error": {"code": "string", "message": "human readable", "details": {...}}}`
with appropriate HTTP status. Codes are stable machine-readable strings
(e.g., `auth.invalid_credentials`, `org.forbidden`, `llm.provider_unavailable`).

## 9. Observability

- Structured JSON logs with `request_id`, `org_id`, `user_id` (never secrets).
- `/healthz` (liveness), `/readyz` (DB+Redis check), `/metrics` (Prometheus format).
- Error-tracking hook (Sentry-compatible, optional via `SENTRY_DSN`).
- LLM call tracing: provider, model, tokens in/out, latency, cost, fallback used.
