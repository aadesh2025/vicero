# Repository map, domain map, dependency map

> Phase A audit output (read-only). Generated 2026-09-24 from the code at `master` (c3b6ec9 + uncommitted
> edits to `chat/pii.py`, `db/templates.py`, `llm/types.py`). Numbers come from AST/import analysis, not from
> docs. Where this file disagrees with `docs/02-ARCHITECTURE.md`, this file describes the code.

## 1. Top level

| Path | Kind | Purpose |
|---|---|---|
| `apps/api` | domain + infra | FastAPI modular monolith (217 py files in `app/`, 81 in `tests/`) |
| `apps/web` | frontend | Next.js 16 dashboard (128 tsx, 58 test/e2e files, 0 `any`) |
| `packages/widget` | public product surface | Single-file vanilla JS embeddable widget (898 lines, no imports); build copies to `apps/web/public/widget.js` |
| `infra/` | deployment | compose (dev/prod), caddy, k8s, n8n, perf, scripts |
| `docs/` | docs | numbered spec docs 00–17, `DECISIONS.md`, `SECURITY.md`, `RISK-REGISTER.md`, runbooks |
| `scripts/`, `Makefile` | tooling | eval + provisioning scripts, dev targets |
| `.github/workflows` | CI | `ci.yml` (api, audit, web, e2e), `release.yml` |

Untracked noise at root (not part of the audit): `admin-staff.png`, `designreference/`, a `.docx`, `start_vicero.bat`.
Generated/ignored and correctly gitignored: `var/`, `celerybeat-schedule*`, `test-results/`. No `__pycache__` or build output is tracked.

## 2. Backend layout (`apps/api/app`)

| Package | Files / lines | Role | Layer |
|---|---|---|---|
| `api/routers` | 3 / 58 | health only | transport |
| `modules/*` (19 domains) | 79 / 10 933 | `router.py` + `service.py` + `schemas.py` (+ `deps.py`) per domain | transport + application |
| `channels` | 13 / 1 251 | `BaseChannel` + telegram/whatsapp/instagram/facebook/slack/discord, router, service | domain + adapters |
| `tools` | 12 / 1 646 | tool execution, HTTP/n8n/MCP/web-search tools, router, service | domain + adapters |
| `workflows` | 6 / 2 084 | visual-workflow graph engine, service, router | domain |
| `webhooks` | 5 / 409 | outbound webhook CRUD + signed dispatch | domain |
| `contacts`, `crm` | 2 / 104, 3 / 280 | inbound contact upsert; chat→CRM capture | domain (helpers, no router) |
| `chat` | 16 / 2 839 | turn runtime, assembly, guardrails, memory, budget, handoff, PII | domain core |
| `llm` | 12 / 1 843 | `ChatProvider`/`EmbeddingProvider` protocols + providers, registry, fallback, pricing | provider abstraction |
| `rag` | 14 / 2 412 | ingest, chunking, converters, retrieval, rerank, evaluate | domain infra |
| `integrations` | 2 / 153 | `n8n_client` | external client |
| `realtime` | 2 / 139 | websocket hub | infra |
| `worker` | 4 / 397 | Celery app, tasks, rollup | background |
| `core` | 14 / 1 321 | config, security, errors, logging, metrics, ratelimit, rbac, crypto, email, audit, middleware | cross-cutting |
| `db` | 7 / 653 | base, session, `BaseRepository`, seed, **templates** (agent template content) | persistence |
| `models` | 21 / 1 479 | 47 tables (34 tenant-owned by `organization_id`) | persistence |

Size posture: largest file `modules/agents/service.py` 878 lines; nothing over 1 000. Longest functions:
`chat/runtime.run_turn` 253, `chat/inbound` nested `events` 217, `admin.list_orgs` 125, `db/seed.seed` 121.
No `utils/helpers/common/misc` files exist. 19 `httpx` clients, all with explicit timeouts.

## 3. Frontend layout (`apps/web/src`)

`app/` (routes) · `components/<feature>/` (admin, agents, analytics, auth, builder, contacts, dashboard, inbox,
invitations, knowledge, settings, …) · `components/ui` + `shared` · `lib/api/*` (typed client, one file per domain) ·
`lib/store` (zustand) · `lib/mock` (mock layer). Feature ownership already exists as `components/<feature>`; a
`features/` rename would not add discoverability. Only two raw `fetch` calls sit outside `lib/api`
(`analytics/page.tsx` CSV export, `app/api/auth/_bff.ts`).

## 4. Domain map

| Domain | Router | Service | Models | Tests (backend) |
|---|---|---|---|---|
| auth | `modules/auth` | `modules/auth` | identity | test_auth |
| orgs / RBAC / invitations | `modules/orgs`, `core/rbac` | `modules/orgs` | identity | test_orgs, test_publish_permission, test_org_creation_gate |
| agents (+versions, playground) | `modules/agents` | `modules/agents` | agents | test_agents |
| agent tests / workflow tests | `modules/agent_tests`, `modules/workflow_tests` | same | agent_tests, workflow_tests | test_agent_tests |
| knowledge + RAG | `modules/knowledge`, `modules/help_articles` | + `rag/` | knowledge, help_articles | test_knowledge, test_rag*, test_ingest_*, test_retrieval_eval |
| conversations / inbox | `modules/conversations`, `modules/inbox` | same + `chat/` | conversations, inbox | test_conversations, test_chat |
| chat runtime | (via conversations, public, channels) | `chat/` | – | test_chat*, test_guardrails*, test_redteam_corpus, test_pii* |
| tools + MCP | `tools/router.py`, `tools/mcp_router.py` | `tools/service.py` | tools, mcp_servers | test_tools, test_mcp |
| workflows | `workflows/router.py` | `workflows/service.py`, `graph.py` | workflows | test_workflows, test_workflow_graph |
| channels | `channels/router.py` | `channels/service.py` | channels | test_channels |
| webhooks | `webhooks/router.py` | `webhooks/service.py`, `dispatch.py` | platform | test_webhooks |
| public widget API | `modules/public` | `modules/public` | widget_configs | test_public*, test_widget* |
| contacts / CRM | `modules/contacts` | + `contacts/`, `crm/` | contacts, crm | test_contacts* |
| analytics, audit, apikeys, credentials, campaigns, macros, canned, admin | `modules/*` | `modules/*` | various | one test file each |

## 5. Dependency map (measured)

Top-level imports only, grouped by package (function-level imports counted separately in §6).

Healthy, and worth locking in with a test:
* **No module-level import cycle exists at file granularity** (Tarjan SCC over 217 files = empty).
* `models` → only `db.base`. `llm` → `core`, `models`. `core` imports no feature package except `core/audit → models` and
  `core/metrics → app.__version__`.
* Routers are thin: only `audit/router.py` builds a query; `conversations/router.py` and `public/router.py` commit inside
  SSE streams.
* The `Base*` abstractions are singular: one `BaseChannel`, one `ChatProvider`, one `EmbeddingProvider`, one `ToolResult`.

Package-level cycles (real):
1. `chat ↔ rag`: `chat.inbound → rag.retrieval/agent_retrieval`, `rag.context → chat.guardrails`, `rag.ingest → chat.pii`.
2. `chat/rag → webhooks → rag`: `webhooks.dispatch → rag.loaders`, while `chat.attention/handoff` and `rag.ingest → webhooks.dispatch`.
3. `channels ↔ contacts`: `channels.service → contacts`, `contacts.service → channels.base`.
4. `core → models → db → core` (via `core.audit → models`, `db.session → core.config`).

Feature-to-feature coupling:
* `modules.orgs.deps` (`OrgContext`, `current_org`) is the de-facto shared tenant/auth context, imported by ~40 files across
  `tools`, `webhooks`, `workflows`, `channels`, and every module. It lives in a feature module but works as core.
* `modules.agents ↔ modules.agent_tests` (lazy imports both ways), `modules.agents → modules.public.schemas` (lazy),
  `modules.orgs → modules.apikeys` (lazy), `modules.help_articles → knowledge`, `modules.macros → inbox`,
  `modules.public → campaigns/conversations/help_articles`.
* Misplaced shared primitives: the SSRF guard used to live in `rag/loaders.py` as a private name imported by `tools` and
  `webhooks`; it is now `core/ssrf.py` (eb73d0f). `chat/pii.py` and `chat/guardrails.py` are consumed by `rag`,
  `orgs.schemas`, and `tools.web_search`.
* 23 cross-package imports of `_private` names remain (mostly the turn pipeline in `conversations.service`, reused by
  `chat.inbound` and `worker.tasks`); tracked as a ratchet in `tests/test_architecture.py`.

## 6. Function-level (lazy) imports

39 across 18 files. Concentrated in `worker/tasks.py` (7), `agent_tests/service.py` (4), `tools/builtins.py` (4),
`chat/inbound.py` (3), `chat/pii.py` (3), `agents/service.py` (3), `workflows/service.py` (3). Most break the cycles in §5.

## 7. Tenant isolation, as implemented

* Every tenant-owned query in the services is filtered by `organization_id`, or by a parent row that was first fetched through
  an org-scoped `_get_*` helper (conversation → messages, KB → documents → chunks, workflow → versions → runs). A heuristic
  scan flagged 47 statements without a literal org filter; the ones reviewed by hand were all parent-scoped or public-by-design
  (`public._resolve_agent`, `help_articles.public_*`, invitation token lookup, API-key hash lookup, platform-admin aggregates).
  **Not every one of the 47 was reviewed** (see REFACTOR-PLAN R-01). Postgres RLS is not implemented either.
* `app/db/repository.py::BaseRepository` was described by CLAUDE.md §8 and `docs/02-ARCHITECTURE.md` §Security as *the*
  enforcement mechanism. **It is used only by `tests/test_db.py`.** Isolation is convention-based, not structural; the docs
  now say so (ADR-084).
