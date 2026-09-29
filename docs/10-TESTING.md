# 10 — Testing Strategy & Quality Gates

## 1. Principles
- Every task ships with tests (Definition of Done, `CLAUDE.md §2`). No task is "done" with a
  red suite. Deterministic tests only — use fakes/mocks for LLMs, embeddings, external APIs.
- Test pyramid: many unit tests, fewer integration tests, a focused set of Playwright E2E for
  the critical user journeys.

## 2. Backend (`apps/api`)
- **Framework**: `pytest` + `pytest-asyncio` + `httpx.AsyncClient` against the ASGI app.
- **DB**: ephemeral Postgres (compose service or testcontainers) with pgvector; migrations
  applied per test session; transaction rollback per test for isolation.
- **Fakes**: `FakeProvider`/`FakeEmbeddingProvider` (`06 §6`); fake email backend; mocked
  HTTP for channel/n8n/provider calls (respx/httpx mock).
- **Coverage targets**: >70% overall; ~90% on `llm/`, `rag/`, `chat/`, RBAC, tenant scoping.
- **Must-cover cases**:
  - Tenant isolation: no query crosses `organization_id`.
  - RBAC matrix: each role allowed/denied correctly.
  - Auth: signup/login/refresh-rotation/logout/reset/magic/oauth-link.
  - LLM fallback chain + cost/token accounting.
  - RAG: chunking, embedding dims, retrieval ranking, citations, token budgeting.
  - Tool loop: execution, iteration cap, tool_runs logging, SSRF guard rejects internal IPs.
  - n8n: signed trigger + callback verification.
  - Channels: signature verification + inbound parse + send (mocked).
  - Webhooks: signed delivery + retry/backoff.
  - Rate limiting returns 429 with Retry-After.

## 3. Frontend (`apps/web`)
- **Unit**: Vitest + Testing Library for components/hooks; mock the API client.
- Cover: builder forms (persona/model validation), streaming chat rendering, RoleGuard,
  status badges, error/empty/loading states, org switcher.

## 4. E2E (Playwright) — the acceptance gates
Run against the full `docker compose` stack (real api/db/redis; LLM via Ollama-local or a
recorded/mock provider so CI needs no paid keys). Journeys (mirror `01 §6`):
1. **Signup → org → agent (Groq/local) → chat** in playground.
2. **Upload PDF → becomes ready → agent answers with citations.**
3. **Embed widget on a static page → chat through it.**
4. **Connect Telegram (mock) → message round-trips.**
5. **Bind an n8n workflow (local n8n) → agent triggers it → result used.**
6. **Invite teammate → role restricts what they see.**
7. **Analytics shows conversation + token usage.**

Capture screenshots/video on failure. These specs must pass in CI before a phase that claims
them is tagged.

### 4.1 Implemented suite (Phase 19.1)

Specs live in `apps/web/e2e/*.spec.ts` (config `apps/web/playwright.config.ts`, helpers
`e2e/helpers.ts`, PDF fixture `e2e/fixtures/vicero-facts.pdf`). One spec per PRD criterion:

| Spec | Criterion | Coverage |
|---|---|---|
| `01-onboarding-chat` | 1 | Full UI: signup → create org → create agent (Groq default) → playground chat. |
| `02-knowledge-citations` | 2 | UI upload path + poll to **ready**; grounded chat returns non-empty **citations**. |
| `03-widget` | 3 | Injects the real `/widget.js` on the web origin with a live public key; sends + asserts a reply. |
| `04-telegram-channel` | 4 | Connect + enable a Telegram channel; **signed** inbound → persisted agent reply; bad secret → 401. |
| `05-n8n-tool` | 5 | Bind an n8n workflow (by webhook URL) as an agent tool; assert it's attached + enabled.¹ |
| `06-teammate-isolation` | 6 | Invite a viewer → accept → sees only org A's agents, never org B's; viewer create → 403. |
| `07-analytics` | 7 | Generate turns; overview reports conversations + non-zero token usage; page renders. |

**Keyless determinism.** The API under test runs with `LLM_FORCE_FAKE=true` (every chat +
embedding call routes to the deterministic Fake provider — no paid keys, no model pulls) and a
lifted `AUTH_RATE_LIMIT` (the suite mints many tenants). A **Celery worker** runs alongside the
API so real ingestion (fake embeddings) drives criterion 2. `ENV=dev` exposes the invitation
`accept_token` in the API response so criterion 6 can accept without SMTP.

**Self-serve is off in the E2E API** (`SELF_SERVE_ENABLED=false`, docs/18): with it on, every
signup also gets a metered trial workspace and the suite's own tenants would sit beside it. The
pytest suite does the same via an autouse fixture in `tests/conftest.py`; the `self_serve` fixture
opts a test into production behaviour (`test_self_serve_*.py`, `test_message_metering.py`,
`test_trial_emails.py`). `e2e/24-signup-and-workspace.spec.ts` and `e2e/30-trial-ui.spec.ts` cover
the sign-in surface and how a trial workspace is drawn (plan endpoint stubbed).

¹ The live n8n webhook **trigger** roundtrip (model decides to call → n8n runs → result fed back)
requires a running n8n and the model emitting a tool call; it is covered by the backend suite
(`tests/test_n8n.py`, verified live in Phase 10) rather than re-run in keyless CI. The E2E covers
the binding + attachment surface.

Run locally against a booted stack:
```
# API (fake provider) on :8000, a worker, and web on :3001, then:
cd apps/web && E2E_API_URL=http://localhost:8000 E2E_WEB_URL=http://localhost:3001 npx playwright test
```
In CI the `e2e` job (`.github/workflows/ci.yml`) boots postgres+redis (service containers),
migrates, starts the API + worker + `next start`, installs the chromium browser, and runs the
suite; the HTML report + service logs upload as an artifact on every run.

## 5. Per-phase gates
Each phase's "Gate" in `08-PHASES.md` must be green before tagging `phase-NN-complete`:
run `make test` (unit+integration) + the relevant Playwright specs. If a gate can't be met
because of a missing human secret, stub it, keep the rest green, and record the gap in
`docs/PROGRESS.md`.

## 6. Non-functional checks
- **Perf** (Phase 16): k6/locust script measuring first-token p50 on Groq (NFR-1) and API
  p95; record results in `docs/PROGRESS.md`.
- **Security**: `docs/SECURITY.md` checklist (SSRF, rate limits, headers/CSP, encrypted keys,
  input/file validation, prompt-injection handling) verified with tests where feasible;
  dependency audit (`pip-audit`, `npm audit`) in CI.
- **A11y** (Phase 19): axe/Playwright accessibility checks on key pages + the widget.

## 7. CI wiring
`ci.yml` runs: lint → typecheck → unit → build → compose-up → E2E → teardown. Any failure
fails the build. Coverage reported as an artifact. See `09 §6`.

## 8. Local commands
`make test` (all), `make test-api`, `make test-web`, `make e2e`, `make lint`, `make typecheck`.
Keep them fast; parallelize where possible.

**Windows + Docker Desktop: the suite can stall (measured 2026-09-25).** With Postgres in Docker Desktop's WSL2 VM and
the tests on the Windows host, about 1 fresh connection in ~600 to the published port never gets a reply: Windows waits
exactly 21 s and raises `WinError 121` ("The semaphore timeout period has expired"), and now and then an accepted
connection goes silent. The suite opens a connection per test, so a full run meets a few, and unbounded they hang the run
while every file passes on its own. `tests/dbconn.py` bounds them (5 s connect timeout with up to 4 attempts; 120 s
command timeout) — 1 800 fresh connections then finished with 0 failures, two needing a retry. Use `127.0.0.1` in
`DATABASE_URL`/`REDIS_URL`, not `localhost` (IPv6 is tried first and the containers publish IPv4 loopback only). If a run
still stalls, suspect the port forward first: `docker exec vicero-postgres-1 psql ...` answering instantly while the host
connection hangs is the signature. Running pytest inside the Docker network avoids the forward entirely.
