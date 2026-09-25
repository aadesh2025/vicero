# PROGRESS.md — Build Progress & Roadmap

Claude Code updates this after every phase (per `CLAUDE.md §9`): what shipped, what's
stubbed/deferred, and any gaps waiting on human secrets.

## Status by phase
| Phase | Title | Status | Notes |
|---|---|---|---|
| 0 | Repo/tooling/CI/compose | ✅ | git, web + api scaffolds, docker-compose (postgres+pgvector/redis/api/worker/web/n8n/ollama), .env.example, Makefile, CI. **Verified: `docker compose up postgres redis` healthy, `/readyz` green (db+redis reachable).** Remaining: on-start alembic in api container (later), Playwright in CI (Phase 19). |
| 1 | Database foundation | ✅ | All 27 models, UUIDv7 base + mixins, org-scoped repository (cross-org isolation test), Alembic async. **Verified against real Postgres: `alembic upgrade head` created all 27 tables + pgcrypto/vector extensions + `chunks.embedding vector(768)`; seed ran (idempotent); 10 tests + ruff + mypy green.** |
| 2 | Auth & accounts | ✅ | Backend (argon2/JWT+rotating refresh/verify/reset/magic-link/OAuth/sessions/rate-limit). **2.6 web auth done: login+signup pages + cookie token store + route-guard middleware + AuthGate (/me+orgs bootstrap, create-first-org). Verified live: signup→org→dashboard, login→dashboard.** Tagged phase-02-complete. |
| 3 | Orgs/RBAC | ✅ | Backend complete (CRUD/members/invites/transfer/RBAC/audit). **3.4 gap closed in Phase 15: org members + invitations settings UI wired to `/v1/orgs/*`, and a real `lib/rbac` matrix / `useCan` gate (mirrors docs/02 §6). Verified live: a viewer-role member gets 403 on admin actions and a read-only UI.** |
| 4 | App shell & design system | ✅ | Design tokens, dark-first ember system, full app shell + dashboard. **4.3 done: typed API client (`lib/api`, hand-written) with Bearer+X-Org-Id+401-refresh + SSE reader; real `/me` renders in shell.** Tagged phase-04-complete. |
| 5 | LLM provider layer | ✅ | app/llm: ChatProvider/EmbeddingProvider protocols, Fake providers, OpenAICompatible base + Groq/Ollama/OpenRouter/OpenAI/custom (chat+stream+models), Gemini + Anthropic adapters (native translation), PRICING + cost, provider resolution (agent→org→env) w/ Fernet-decrypted keys, fallback chain. /v1/credentials CRUD (masked) + test + /providers catalog. 54 tests pass (mock HTTP transport, no live keys needed); ruff+mypy clean. Tagged phase-05-complete. |
| 6 | Agents & versions | ✅ | Backend (CRUD/duplicate/versions/publish/rollback/playground stream). **6.4 done: agents list + builder wired to real API — loads real agent+version, debounced autosave (PATCH), publish, and real SSE playground streaming. Verified live: create→configure→autosave persists→publish→playground streams (echo fallback + graceful provider-error surfacing).** 61 tests pass; ruff+mypy clean. Tagged phase-06-complete. |
| 7 | Knowledge base & RAG | ✅ | Backend: knowledge module (KB CRUD, document upload file/url/text → store + Celery enqueue), `app/rag` pipeline (loaders PDF/DOCX/CSV/TXT/MD/URL+SSRF, recursive char chunker, Ollama embeddings + resolver, vector + hybrid-RRF retrieval, context assembly w/ char/token budget), Celery worker running `rag.ingest_document`, RAG wired into playground (prompt assembly + streamed `citations` event), migration 0004 (HNSW + GIN indexes). Frontend: knowledge list/detail wired to real API (upload/url/text, live status polling, chunk viewer, reingest/delete) + builder Knowledge tab (real KBs, enable-RAG, retrieval test). **Verified live (Playwright + curl): browser upload→Celery worker→real Ollama `nomic-embed-text` embeddings→ready→chunk viewer; builder retrieval test returns the correct chunk; playground stream emits a `citations` event then a grounded answer from local `qwen3:14b`.** 79 tests pass; ruff+mypy clean. |
| 8 | Chat persistence & memory | ✅ | Backend: `app/chat` (assembly, TurnResult runtime, memory summarizer on a small configurable model), shared `app/rag/agent_retrieval`, conversations module (`POST /v1/agents/{id}/chat` SSE+JSON persisting conversations/messages w/ usage/cost/latency/citations; list/detail/messages/patch/delete; long-term memory folds aged-out turns into `memory_summary`), WebSocket `WS /v1/agents/{id}/chat/ws`. Also fixed the published-agent silent-save bug (branch-on-edit, ADR-023). Frontend: `/conversations` two-pane browser + streaming composer through the persisted endpoint; nav item. **Verified live: curl + browser — streaming `conversation`/`token`/`message` events, continuation on the same conversation, persisted history renders, composer streams a persisted turn.** 87 tests pass; ruff+mypy clean. |
| 9 | Tools & tool calling | ✅ | Backend `app/tools`: built-ins (get_datetime, calculator, knowledge_search, guarded http_request w/ SSRF, web_search stub) each JSON-schema'd; user-defined HTTP tools (templated `{{arg}}`, SSRF-guarded, timeouts); Tool CRUD + `/test` + `/runs`; `run_turn` tool-calling loop (iteration-capped, execute→feed-back→continue) wired into persisted chat + playground; every call logged to `tool_runs`. Frontend: builder Tools tab wired (enable-tools, built-in toggles, HTTP tool builder + test, runs view). **Verified live: qwen3:14b called the `calculator` tool mid-conversation ("47 × 89" → tool run `{expression:"47 * 89"}`→`4183`, used in the answer), and the Tools tab shows the built-in toggled on + the real run.** 97 tests pass; ruff+mypy clean. |
| 10 | n8n integration | ✅ | Backend `app/integrations/n8n_client` (list/get workflows via public API, HMAC-signed `trigger_webhook`, `verify_callback` w/ replay protection, webhook-URL extraction). `type="n8n"` tool slots into the Phase-9 dispatch/loop (ADR-024): **sync** (Respond to Webhook → fed to model) + **async** (callback resolves a pending `tool_run`). `/v1/tools/n8n/{workflows,bind,callback}`. Starter workflows in `infra/n8n/` + import README. Frontend: `/automations` lists real workflows + bind-as-tool dialog (agent + mode). **Verified live end-to-end: created+activated the echo workflow in the running n8n, bound it, and qwen3:14b called it — the real webhook responded `{received:{message:"n8n works"}}` and the model used it.** 106 tests pass; ruff+mypy clean. |
| 11 | Web widget | ✅ | Backend `app/modules/public`: `GET /config`, `POST /chat` (SSE + JSON, rate-limited, optional visitor identity, no dashboard auth) + `WS /ws`, keyed by `public_key`; reuses the dashboard runtime (refactored `build_tooling`/`_resolve_provider`/`_finalize_turn` to be org-context-free). `packages/widget`: single dependency-free `widget.js` (Shadow DOM, launcher, streaming, sanitized markdown, quick replies, file attach, theming, `window.BotForge` SDK) → served at `/widget.js`. Frontend: builder Channels tab (color/position/launcher/mode/branding → `persona.widget`, autosaved) with live preview + copyable embed snippet. **Verified live: embedded the snippet on a plain HTML page — the widget loaded its themed config and `BotForge.sendMessage()` streamed a reply from the public endpoint (Shadow-DOM isolated, cross-origin).** 110 tests pass; ruff+mypy clean. |
| 12 | Messaging channels | ✅ | `app/channels`: a `BaseChannel` adapter interface + registry; Telegram (setWebhook + secret-token verify), WhatsApp (GET verify challenge + `X-Hub-Signature-256`), Slack (v0 signature + `url_verification` + `chat.postMessage`), Discord (Ed25519 interaction verify + PING/PONG + inline slash-command reply). Channel CRUD with **encrypted, masked** tokens; the shared inbound turn factored into `app/chat/inbound.InboundTurn` (also honours handoff-pause). Frontend Channels tab: per-channel connect flows (token entry, enable/disable, webhook URL). **Verified live: a signed Telegram inbound webhook produced a real Groq reply ("Paris.") on a `channel="telegram"` conversation.** Real provider *delivery* is mock-tested (this env can't reach provider hosts / has no public webhook URL). 117 tests pass; ruff+mypy clean. |
| 13 | Inbox & handoff | ✅ | Handoff triggers (keyword in `app/chat/handoff` + a `request_handoff` built-in tool) pause the bot (`InboundTurn` honours `status="handoff"`); `handoffs` records; inbox endpoints (list/detail/takeover/handback/reply/assign/close/notes/tags); an in-process pub/sub (`app/realtime/hub`) drives the operator inbox WS **and** a widget listen-socket so operator replies reach the end user live. Two-pane Inbox UI wired + realtime. **Verified live end-to-end in the browser: widget user asks for a human → canned handoff message → appears in the inbox → operator takes over + replies → the reply pushes to the widget live → handback → bot resumes.** 123 tests pass; ruff+mypy clean. |
| 14 | Analytics & metering | ✅ | `app/modules/analytics`: overview (conversations/messages/users/tokens/cost/handoff+resolution rate), usage grouped by day/provider/model, latency (p50/p95/avg via `percentile_cont`), top-questions, unanswered (escalation heuristic), CSV export — all aggregated **live from `messages`/`conversations`/`handoffs`** (org-scoped). `app/worker/rollup`: Celery task upserting `usage_records` + refreshing `quotas` with a threshold event. Frontend: `/analytics` + dashboard stat row/chart wired to real aggregates. **Verified live against real Groq usage: the UI/overview (51 msgs, 3789+956 tokens) matches a direct `messages` aggregate exactly, and the rollup's `usage_records` matches the message sums.** 129 tests pass; ruff+mypy clean. |
| 15 | API keys/webhooks/audit | ✅ | `app/modules/apikeys`: `bf_`-prefixed keys (hashed + prefix lookup, scopes, last-used), and `current_org` now accepts an API key (`X-API-Key` / `Bearer bf_…`) resolving its org + acting as the creator. `app/webhooks`: endpoint CRUD (encrypted/masked secret), HMAC-signed delivery with retry/backoff + delivery log, and the full event catalog emitted across the app (message.created, conversation.created/closed, handoff.requested/resolved, document.ready/failed, tool.run, usage.threshold). `app/modules/audit`: read API; `app/core/audit.write_audit` records sensitive mutations. Frontend: wired settings pages (API keys, webhooks + deliveries, audit) + a `lib/rbac` matrix / `useCan`. **Verified live: API key authenticates requests; a real chat emits a `message.created` delivery; `/test` signed-delivered to example.com (405); audit recorded key/webhook creation; a viewer got 403 on admin actions + a read-only UI.** 141 tests pass; ruff+mypy clean. |
| 16 | Guardrails & hardening | ✅ | **16.1** `app/chat/guardrails`: retrieved RAG chunks + tool output are neutralized (`neutralize_injections`) and wrapped as **data, not instructions** before the model sees them; blocked-topics (`persona.blockedTopics`) refuse pre-LLM via a `RefusalProvider`; output redaction strips secret-looking strings from stored/returned text. **16.2** API-key **scope enforcement** (effective role = scope tier capped by creator's role, least privilege; `admin`≠`owner`), rate limits on channel webhooks + n8n callback, `SecurityHeadersMiddleware` (CSP/XFO/nosniff/Referrer/COOP/CORP/Permissions + prod HSTS), advisory `pip-audit`+`npm audit` CI job, and a verified `docs/SECURITY.md` checklist. **16.3** perf harness (`infra/perf/{locustfile,measure}.py`). **Verified live: blocked topic → fallback (not the model), secret → `[redacted]`, a read-scoped key → 403 on write / 200 on read. Measured NFR-1 (see below).** 151 tests pass; ruff+mypy clean. |
| 17 | Admin console | ✅ | **17.1** platform-staff API (`app/modules/admin`, `require_staff`/`is_staff`, org-agnostic — no `X-Org-Id`): `GET /v1/admin/{orgs,users,usage,health,feature-flags}` + `PUT /v1/admin/feature-flags/{key}` (Postgres upsert). New `FeatureFlag` model + migration `0005`. **17.2** `/admin` console UI (platform usage cards, live system-health pills, top-orgs, feature-flag toggles, orgs + users tables), a `is_staff`-gated "Platform › Admin" sidebar item, and a client route guard + middleware. **Verified live (curl + Playwright): staff → 200 + full console; non-staff → 403 on every endpoint **and** redirected off `/admin` to `/dashboard` with no Admin nav; unauth → 401.** 157 tests pass; ruff+mypy clean. Tagged phase-17-complete. |
| 18 | Billing (optional) | ⏸️ | **DEFERRED** — optional/stretch in the spec (PRD §7, Phase 18 header), no `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` supplied, and the platform is not monetising yet. Metering plumbing (`usage_records`, `quotas`, `subscriptions` table, `Organization.plan`) already exists, so billing can be layered on later without rework. Not built. |
| 19 | E2E/docs/polish | ✅ | **19.1** Playwright E2E — one spec per PRD §6 criterion 1–7 (`apps/web/e2e`), driven against the real stack with the Fake provider (`LLM_FORCE_FAKE`), **all 7 green**; new CI `e2e` job boots services + runs them (closes the Phase-0 Playwright item). Also the **Next.js 14→16 + React 18→19 + ESLint 9** upgrade (**0 npm-audit advisories**, was 4 high + 1 moderate) and a real **worker bug fix** (NullPool engine so async tasks survive across Celery loops). **19.2** README + self-host / API-usage / widget-install / n8n-setup guides; widget-demo.html rewritten. **19.3** axe a11y gate (spec 08) on key pages + widget — fixed widget accent contrast + `--faint` token. Throwaway `@example.com` dev rows + `live_demo` flag cleaned (`make clean-devdata`). 157 backend tests pass; ruff+mypy+tsc+eslint clean. Tagged phase-19-complete. |
| 20 | Production deployment | ✅ | **20.1** `docker-compose.prod.yml` (Caddy auto-TLS for `$DOMAIN`+`$API_DOMAIN`, non-root api+web images, healthchecks, **one-shot migrate job** so replicas never re-migrate, nightly backup service) + a production **web Dockerfile** (Next standalone, non-root). **Realtime hub → Redis pub/sub** (ADR-028, same interface, cross-node delivery test). **Webhook retry beat sweep** (`webhooks.sweep_pending` + beat service). **Refresh token → httpOnly cookie** via a Next BFF (`/api/auth/*`, ADR-019); access token stays JS-readable by cross-origin+WS architecture (documented). **20.2** `pg_dump` backup + restore scripts, **`/metrics`** (Prometheus), **Sentry** hook, JSON-log aggregation. **20.3** K8s manifests (`infra/k8s/`). **20.4** `release.yml`: build+push images to GHCR + **smoke-test `/readyz`** on the built image + deploy template. 162 backend tests pass; ruff+mypy+tsc+eslint clean; **10/10 Playwright E2E green**. Tagged phase-20-complete. |

Legend: ⬜ not started · 🟨 in progress · ✅ complete · ⏸️ deferred

## Measured performance (NFR-1, Phase 16.3 — `infra/perf/measure.py`, live stack 2026-07-19)
- **Non-LLM API** `GET /v1/agents`: **p50 13 ms, p95 16 ms** (n=30) — well under the 300 ms target.
- **LLM first-token (Groq `llama-3.1-8b-instant`)**: **p50 417 ms, p95 529 ms** (n=7) — meets NFR-1.
- Measured **Groq separately from Ollama** on purpose: the earlier "p95 166 s" figure was **local
  Ollama `qwen3:14b` tool-calling turns** (30–90 s/turn), not a web-provider latency — it must not
  be blended into the Groq number. Ollama is excluded from the NFR-1 first-token figure by design.

## Shipped enhancements (post-v1)
- **Client IP, throwaway-email list and dev exposure fixes (2026-09-25, ADR-091/092/093).** The BFF now forwards the
  visitor's `X-Forwarded-For`/`User-Agent` and the API believes it only from `TRUSTED_PROXIES` (read from the right;
  spoofed headers ignored), so per-IP limits are per visitor — the shared-bucket bug that blocked dev signups is fixed and
  the stuck Redis key deleted. Throwaway-email domains now come from the maintained `disposable-email-domains` list,
  refreshed weekly by beat, plus a fail-open MX signal that only records; neither catches `idwager.com`. The dev
  Postgres/Redis are published on loopback only and the Postgres password is required and was rotated.
- **Self-serve signup, 10-day free trial and plan limits — Phase S1 (2026-09-25, ADR-088, ADR-090, docs/18).**
  Anyone can now sign up (Google, Facebook, email magic link, email + password) and gets exactly one workspace
  on a 10-day trial: 500 messages (1 visitor message or 1 AI reply each), one agent, no workflows / n8n / tool
  calling / MCP, a 50-per-day dashboard test-chat cap, and publishing needs a verified email. Every limit lives in
  one table (`app/core/plans.py`); `trial_expired` is computed, never stored. **Existing orgs were backfilled to
  `legacy` (unlimited) by migration 0028** and `plan` defaults to `legacy`, so no current client is metered.
  When the trial ends or the messages run out the bot goes **silent** — no error/upgrade text on any channel, the
  visitor message is still saved, the owner can reply from the inbox — enforced in `InboundTurn` (the one path the
  widget and every messaging channel share) with an atomic `UPDATE … RETURNING` reservation (300 racing
  reservations against a cap of 500 → exactly 250). 402 `plan_limit` on the API + runtime checks (`build_tooling`,
  workflow execution). Dashboard: trial banner + usage meter, ended banner with "N visitor messages were not
  answered", locked (not hidden) features, `/billing/upgrade` placeholder, onboarding, and the auth pages
  (`/verify-email`, `/forgot-password`, `/reset-password`, `/magic`, `/oauth/callback`, `/oauth/verify`). Hourly
  Celery-beat `trial.sweep` sends the five lifecycle emails once each. Abuse: normalised-email uniqueness, per-IP
  signup cap, disposable-domain blocklist, weak-password floor, login lockout, per-email limits, per-org burst limit.
  OAuth now links only to **verified** accounts and has a real browser flow (one-time exchange code, httpOnly
  refresh cookie). **Setup only you can do:** Google/Facebook apps, SMTP + Celery beat, `alembic upgrade head` —
  `docs/18-SELF-SERVE-PLAN.md` §7. **Not built (out of scope):** payments, pricing, team invites for trial users.
  **Known:** signup still returns 409 for a registered address (SECURITY.md §10); Facebook PKCE unverified against
  a real app; tests/test_architecture.py::test_no_new_package_import_cycle already failed on HEAD (a
  `core`/`db`/`llm`/`models` cycle) — not caused by this change.
  **Follow-up:** a conversation silenced by the plan now also lands in the Inbox handoff queue with a "plan limit"
  badge (`reason=plan_limit`), so the owner sees waiting visitors; details in docs/18-SELF-SERVE-PLAN.md §8.
- **n8n bind-time signature enforcement, audit, and a single home for the signing secret (2026-09-25, R15, ADR-089).**
  Binding an n8n tool now fails unless the workflow verifies BotForge's signature (by id or by pasted URL,
  resolved via the n8n API); `GET /v1/admin/n8n-signature-audit` reports every already-bound tool that
  doesn't; `N8N_WEBHOOK_SIGNING_SECRET` lives only in the root `.env` (`make up` passes both env files).
  New `N8N_REQUIRE_SIGNATURE_CHECK`. R15 (b) — no production n8n — stays open.
- **n8n webhook signatures are now verified, and the demo workflows live in the repo (2026-09-24, RISK-REGISTER R15).**
  BotForge signed every call to an n8n webhook, but no workflow checked it: an unsigned `curl` to a
  workflow URL ran the automation, bypassing the agent, RBAC and budgets.
  - **Verification** (`infra/n8n/*.json`): `Webhook (rawBody)` -> `Verify BotForge signature` (Code) ->
    `Signature valid?` (IF) -> automation | `401`. Same scheme as `n8n_client.sign()/verify_callback()`:
    hex HMAC-SHA256 of `"<ts>." + raw body`, constant-time, 300 s window, **fails closed** (no secret,
    missing header, no raw body => 401). Applied to the four demo workflows, the provisioning template
    and both example workflows.
  - **What n8n 2.x needed:** Code nodes cannot read `$env` or `require('crypto')` by default, so the dev
    compose n8n service now sets `N8N_WEBHOOK_SIGNING_SECRET`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and
    `NODE_FUNCTION_ALLOW_BUILTIN=crypto`. Compose interpolates from `infra/.env`, not the root `.env`; the secret
    was briefly copied into both (superseded 2026-09-25: one home, the root `.env` — see the entry above). Prod compose has no n8n service; the
    requirement is written up in docs/07 and `infra/n8n/README.md`.
  - **Verified live** against all four demo workflows: an 8-case attack matrix each (no headers, wrong MAC,
    no timestamp, 10-min replay, future timestamp, tampered body, wrong secret, **empty secret**) = all 401;
    a request signed by BotForge's own `sign()` with a non-ASCII body = accepted and the automation ran;
    and a real agent turn (BotForge signing, n8n verifying) worked. Then all 7 repo JSONs were imported
    into n8n and hit signed/unsigned.
  - **Enforcement, not just docs:** `provision-client.mjs` refuses to clone a template without the verify
    node; `tests/test_n8n_workflows_signed.py` pins the structure of every JSON under `infra/n8n/`, runs
    the verifier's real JavaScript under Node against Python `sign()`, and checks no secret/state is
    exported (mutation-checked: unwiring the gate fails it).
  - **Not covered:** a workflow hand-built in the n8n UI is unverified and BotForge does not inspect nodes
    at bind time; there is no production n8n yet. R15 stays open for both.
  - **Demo workflows exported** to `infra/n8n/demo-niches/` (Harbor Table Bistro, CityCare Clinic, Luxe Glow
    Salon, Threadline Fabrics) from the live n8n with `staticData` (the test bookings) stripped; the
    fictional seed data that remains is marked `// DEMO FIXTURE`. `scripts/import-n8n-workflows.mjs`
    recreates/updates, activates and org-tags them (both paths tested, including recreate-after-delete);
    the rebuild-from-nothing steps are in `infra/n8n/README.md`. n8n's own database had been empty once
    today (no owner, stale `N8N_API_KEY`), which is why.

- **Live bug fixed: the platform default model was retired, so every new agent was silently broken (2026-09-24).**
  Groq retired `llama-3.3-70b-versatile` (the default in `agents.service.DEFAULT_MODEL_CONFIG` and
  `db.seed`) and `llama-3.1-8b-instant` (`settings.summary_model`, used by memory summaries and CRM
  extraction). Both 404 with `model_not_found`. The effect on a visitor: an agent created with no
  overrides answered every message with its fallback message. Found while testing four demo agents,
  not by CI — the fake provider never 404s. Agents pinned to another model (the two existing demo
  agents were already on `qwen/qwen3.8-27b`) were unaffected.
  - **Verified before choosing** (`GET /openai/v1/models` with the live key, plus a real tool-calling
    request to each candidate): Groq now serves `openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
    `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`, `llama-prompt-guard-2-{22m,86m}`, plus speech/TTS
    models. **New defaults: chat `openai/gpt-oss-120b`, summary `openai/gpt-oss-20b`.** Both tool-call
    correctly; both are on the same 8,000 tokens/min free-tier cap as qwen. The L2 (`prompt-guard-2-86m`) and
    L3 (`gpt-oss-safeguard-20b`) guard models were already on the live list.
  - **One source of truth:** `llm/catalog.DEFAULT_CHAT_MODEL` now feeds `agents.service` and `db.seed`
    (it was three separate literals that went stale together). The Groq catalog list was rewritten to
    the live lineup; it had also kept Llama 4, Qwen3-32B, Kimi K2, DeepSeek-R1-distill and Gemma2, none of
    which Groq serves any more. `scripts/provision-client.mjs` no longer pins a model at all — a
    provisioned client inherits the platform default.
  - **Sweep:** every other reference was updated — `.env.example`, `docs/ENV.md`, `docs/06`,
    `docs/guides/API-USAGE.md`, `infra/perf/measure.py`, the web fallback in `agent-mapping.ts`, and the
    test/e2e fixtures. **Deliberately left:** historical records (this file, `DECISIONS.md`, the docs/11
    and docs/12 measurements, the session-log skill, and the `CLAUDE.md` §10b sentence "prompt-only
    grounding fabricates 12/15 on `llama-3.1-8b-instant`") — they describe what was measured on that
    model and must not be rewritten. OpenRouter's `...llama-3.3-70b-instruct:free` and Cerebras'
    `llama-3.3-70b` entries in the catalog are other vendors' ids; not checked against those vendors.
  - **Tests:** `tests/test_default_model.py`. Offline (always on): defaults resolve to the Groq catalog,
    and a zero-override agent is created on the default. Live (opt-in, `RUN_LIVE_LLM_TESTS=1`): asks
    Groq's `/models` whether the default, summary, L2 and L3 models exist, and sends a real message
    through a fresh zero-override agent asserting it does **not** return the fallback. No model name is
    hardcoded in the test. Mutation-checked: pointing the default back at `llama-3.3-70b-versatile`
    fails both live tests and passes the offline ones (as designed — offline cannot know what a vendor
    retired). Nothing runs the live tier automatically; it belongs in a periodic job or a pre-deploy step.
  - **⚠️ Not re-measured:** every grounding/fabrication, red-team and distress number in docs/11 and
    docs/12 was taken on Llama models. `gpt-oss-120b` is a different model with reasoning; the
    2026-08-02/03 no-context A/B (see the `session-log` skill) has not been re-run on it. Do that before
    trusting those numbers for any agent on the new default.

- **docs/14 (Knowledge Pipeline v2) audit — what is done and what is left (2026-09-24).** Checked
  against the code, config and commits, not only the doc (which is dated 2026-08-17). Headline:
  **built and tested, but everything Docling is OFF** — `DOCLING_ENABLED=false`, structural
  chunking off, reranker off; `docling-serve` exists in the *dev* compose only, not `docker-compose.prod.yml`.
  - **Done and live:** P0-1 FTS literal; P0-2 eval harness + CI gates; K1-1…K1-5 (K1-5 closed
    2026-08-17, `b84d96b`, verified against a real `docling-serve`); the cold-start warm-up
    (`e0f1a22`, 124.6 s → 8.1 s); K2-1 real token counts (cl100k, enabled); K2-6 heading in the FTS
    index (ADR-067); K3-1 upload allowlist; K3-2 email gate; K5-1 per-KB `fts_config`; K5-2 PDF page cap;
    K5-3 extraction retention. Follow-up 2 (wide-spacing phone gap) measured — 0 instances in 18 real
    chunks — and deliberately filed, not fixed.
  - **Built but deliberately disabled:** K2-2/3/4 structural chunking (K2-5 measured hybrid **−0.0029**,
    so the gate held); K4-1/2/3/5 reranker (protocol, no-op default, HTTP cross-encoder, ADR-063).
  - **Not done:** **K6** (extraction confidence K6-A1…A5, structured facts K6-B1…B7) — no code, no
    migration; its K1-5 prerequisite is now met but it also needs Docling enabled and `docling-serve`
    added to the prod compose. **K3-3…K3-6** (charts, `q=media` queue, ASR, media quota) — blocked on
    Granite Vision RAM measurement and real client audio, *not* on a running docling-serve (that has
    run live since K1-5; the doc's wording is stale). **K4-4** (rerank latency delta) — needs a
    deployed rerank service. **§12 rollout steps 1–6** — none executed.
  - **New findings from this audit:** (1) the prod api/worker image does not pre-warm `tiktoken`, so
    the first ingest downloads the ranks; fine on a normally-connected VPS, and it degrades to `len/4`
    (never fails) where it cannot — bake it into the image or set `TIKTOKEN_CACHE_DIR` for a
    locked-down box. (2) The docs/14 header block and the K3-3 note need a wording refresh.
  - **Nothing here blocks launch.** It is optional quality/format work behind flags that default safe.

- **The 13 known backend test failures, diagnosed precisely (2026-09-24).** The prior note
  ("need a download from a server this machine can't reach") was a guess; it is now confirmed
  against the tracebacks. Full suite twice on the dev DB (port 5750): **1192 passed / 13 failed /
  4 skipped**, both runs, the same 13.
  **One cause: `tiktoken` cannot download its BPE ranks.** The resource is
  `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken`. From this machine
  the name resolves (57.150.192.193) but the TLS connection is **reset** (`WinError 10054`, curl
  exit 35) while github.com and huggingface.co answer 200 — a selective block on this network, not
  a general outage and not a DNS failure. `app/rag/tokenizer.py` degrades to `len/4` by design,
  which is what the token tests then trip over.
  - `tests/test_docling_chunking.py` × 9 — `DoclingChunkingUnavailable` (chunker builds a
    tiktoken-backed tokenizer): `test_the_heading_path_is_embedded`, `..._is_not_stored`,
    `test_the_enriched_string_is_never_persisted_as_content`,
    `test_inline_mode_puts_the_heading_where_the_fts_index_can_see_it`,
    `test_the_two_heading_modes_really_differ`, `test_every_chunk_carries_its_heading_path`,
    `test_caller_metadata_survives`, `test_peers_under_one_heading_merge_and_other_sections_do_not`,
    `test_no_page_key_when_the_document_has_no_page_geometry`.
  - `tests/test_docling_chunking.py` × 3 — assertions that the count is *not* `len/4`, failing
    because it is: `test_token_count_is_the_tokenizers_not_a_character_ratio` (`8 != 8`),
    `test_token_count_of_a_known_fixture` (`3 == 2`),
    `test_non_english_is_no_longer_wildly_undercounted` (`11 > 22`).
  - `tests/test_rag.py::test_estimate_tokens` × 1 — `100 == 50` for `"a" * 400`.
  **Real-world consequence on this machine:** ingest silently records `len/4` token counts, which
  undercounts Tamil/Devanagari several-fold. Fix for CI or a locked-down worker: point
  `TIKTOKEN_CACHE_DIR` at a pre-warmed path. CI with normal internet access should not see these.
  **Not a network failure — the first run overlapped a deliberate mutation check:** the *first* full
  run had 5 extra failures, `tests/test_tenant_isolation.py` probes for `GET/PATCH/DELETE
  /v1/agents/{agent}`, `/versions`, `/widget-config` (org B got 200 on org A's agent). Those are
  **exactly the 5 probes that fail when the org check in `agents.service._get_agent` is removed**:
  the session that wrote the sweep did that on purpose, in the shared working tree, to prove the
  sweep can detect a leak (5 failed, 3 passed for `-k agents`), then restored the file (`git diff`
  empty). A full run that imported `agents/service.py` inside that window saw the mutated code.
  Reproduced: the same 5 fail deterministically under that mutation. That the run overlapped the
  window is inferred from timing, not captured. `_get_agent` in `HEAD` checks `organization_id`; the
  sweep passes 69/69 in isolation and in the second full run (1192 passed). No leak in committed
  code. **Lesson: don't run a full suite in a tree another session is editing — and don't mutate
  shared source for a mutation check; do it in a worktree.**

- **R14 fixed: a dropped chat stream no longer loses the reply (2026-09-24, ADR-083).** Instruction
  was "solve the issues and existing bugs". Three attempts, and the first two failing is the
  useful part — recorded in full in ADR-083 so nobody retries them:
  1. Persist inline in a `CancelledError` handler with a bare `flush()` → silently rolled back
     (`CancelledError` is a `BaseException`; it skips `get_session()`'s rollback *and* commit).
  2. Catch it and retry (`rollback()` + finalize + `commit()`) → still 0 replies: anyio's cancel
     scope re-raises at **every** subsequent checkpoint, so each recovery `await` was cancelled
     in turn. The `queue_email` off-thread pattern failed for the same reason (no task reached
     the worker). Found only by re-running the reproduction after each draft.
  3. **Works:** a plain synchronous `Task.delay()` (no `await`, so no checkpoint to interrupt)
     handing a new `chat.finalize_turn` task the conversation fields + `TurnResult`; the worker's
     own session recreates the conversation/user message under the same client-assigned UUIDv7
     id if they never became durable, then appends the reply via the unchanged `_finalize_turn`.
  A further miss found by the next verification run: Starlette ends a dropped stream by
  `aclose()` (→ `GeneratorExit`) about twice as often as by cancellation; the first working
  draft handled only `CancelledError` and still lost 61 of 91 replies. Both are caught now.
  **Verified on the real stack** (fake provider, guards off, own Redis DB): concurrent load
  test → 91/91 conversations, 91/91 user messages, **90/91** replies (was 0/91); 100 sequential
  early-disconnects → 100/100/100. **Residuals, not hidden:** one unexplained straggler in ~190
  requests; recovered replies are whatever had streamed so far (can be truncated); a widget-path
  drop during pre-generation awaits can still lose that turn's user message. Regression tests
  (`tests/test_chat_disconnect.py`, 6) drive the generator directly (`aclose()` /
  `athrow(CancelledError)`) because httpx's `ASGITransport` buffers the body.
  **Also fixed — the long-standing "pre-existing unrelated failure"**
  `test_playground_without_handoff_feature_does_not_trigger` (a 502 reported as known-and-ignored
  in every report since Phase 1): a **stale test, not a product bug**. The default provider is
  `groq`, and since 2026-07-31 the Playground deliberately raises a typed 502 instead of
  stubbing an echo; the test still asserted the echo. It now opts into `provider: fake`.
  **Environment note:** after a reboot Windows reserved TCP 5430–5729 (`netsh interface ipv4
  show excludedportrange`), so Postgres could not bind 5433 — dev DB was brought up on host
  port 5750 via `POSTGRES_HOST_PORT` with `DATABASE_URL` overridden per command; no files
  changed, data volume intact (migrations at head).

- **RISK-REGISTER R4 measured, and a real data-loss bug (R14) found doing it (2026-09-23).**
  New `infra/perf/load_test.py` — the first concurrency test ever run against this stack; every
  prior verification anywhere in this file is sequential, one-request-at-a-time correctness.
  - **Deliberately isolates platform throughput from LLM-provider latency**: every test agent
    uses `provider: "fake"` (instant, no network) and runs against a DEDICATED API instance with
    guard L2/L3 disabled — otherwise a real concurrency test would fire live Groq calls per
    message and risk repeating the exact incident already logged 2026-08-10 (free tier exhausted
    by 61 *sequential* probes). Isolated on Redis DB 1 so its Celery traffic never touched the
    operator's normal dev worker/queue.
  - **Numbers**: chat first-token p50 49ms→2220ms (c=1→50), workflow-run p50 355ms-1581ms→
    3700-4057ms (c=1→25), **zero HTTP-level errors at any level** — the platform didn't fall
    over, it degraded. Two causes identified, not fixed (a sizing decision for whoever deploys
    to production, not a code bug): no `pool_size`/`max_overflow` on the async DB engine
    (asyncpg default caps at 15 concurrent connections/process); the documented Windows dev
    `--pool=solo` Celery worker (one process, no parallelism) caps workflow throughput near
    3.3 req/s regardless of concurrency. Full numbers in `docs/15-DEPLOYMENT-CAPACITY.md` §11.1.
  - **⚠️ R14 (new): a 100%-reproducible data-loss bug, found only because the load test's own
    client disconnects the SSE stream after the first token — the same thing a browser tab does
    on navigation, a pattern no prior test in this codebase exercised.** Every early-disconnected
    chat turn (38/38 in this run) logs an `unhandled_exception` (a `CancelledError` propagating
    through `BaseHTTPMiddleware` into a SQLAlchemy session mid-flush) — but worse, **the
    assistant's reply is never persisted**: queried directly, 53/53 user messages from the
    concurrent runs have zero matching assistant message, while a matched control (identical
    setup, stream drained to completion) persisted correctly on the first try. The client sees
    HTTP 200 throughout (SSE headers are already on the wire before the stream breaks), so
    nothing signals the loss. **Not fixed** — needs a design decision (persist partial content on
    disconnect vs. discard on purpose; middleware order; move persistence earlier in
    `app/chat/runtime.py`) that touches the chat streaming/persistence path, flagged rather than
    guessed at. Filed as `RISK-REGISTER.md` R14, P0 — arguably more urgent than R4 itself was,
    since it's a concrete bug rather than an absence of measurement.
  - `RISK-REGISTER.md` (R4 marked measured, R14 added), `docs/15-DEPLOYMENT-CAPACITY.md` (new
    §11), `CLAUDE.md` §11 all updated.

- **RISK-REGISTER R1 closed: backups now cover the `uploads` volume (2026-09-23, ADR-082).**
  The top item on `RISK-REGISTER.md`'s P0 list — `infra/scripts/backup.sh` had only ever run
  `pg_dump`, so the `uploads` volume (client-uploaded documents + each one's persisted
  `DoclingDocument` JSON) was never backed up, a gap flagged at docs/15 §8.3 and docs/09 §4 and
  never previously closed.
  - **Extended the existing `backup` service/script, not a second one.** The prod `backup`
    service gets a **read-only** mount of the SAME `uploads` volume `api`/`worker` already write
    to (never a duplicate copy), plus a new `UPLOADS_DIR` env var. `backup.sh` now `tar -czf`s
    that directory alongside the `pg_dump`, same timestamp, same `backups` volume, same
    rotation `find` (now matching both filename patterns). `restore.sh` takes the archive as an
    optional second argument, extracted to a new required `UPLOADS_TARGET_DIR` (no default — a
    wrong guess there silently "restores" documents nobody can find).
  - **Backward compatible by construction**: `UPLOADS_DIR` unset still produces the DB dump
    alone, now with a loud warning instead of a silent gap — a bare Postgres-only environment
    (no uploads volume at all) keeps working unchanged. Same for `restore.sh` with no second arg.
  - **Verified against real Postgres and real client-shaped data, not fixtures.** Ran the actual
    `backup` service's own command (a `postgres:16` container, network-joined to the real dev
    `botforge-postgres-1`, the real dev `apps/api/var/uploads` bind-mounted read-only in place of
    the volume): 48 tables dumped, 2993 real uploaded-file entries archived. Restored BOTH into a
    throwaway database and a throwaway directory — `organizations` row count matched source
    exactly (2 = 2) and `diff -rq` between the original uploads directory and the restored one
    came back identical. This is a real end-to-end round trip, not just "the script exited 0".
    Also confirmed `docker compose -f docker-compose.prod.yml config` resolves the `uploads`
    volume `read_only: true` at `/uploads` in the `backup` service — the same named volume, not
    a second one under a different name.
  - `docs/09-DEPLOYMENT.md` §4, `docs/15-DEPLOYMENT-CAPACITY.md` §8.3, and `RISK-REGISTER.md` R1
    all updated to reflect the closure. **k8s unaffected** (R11, unchanged) — no RWX volume or
    k8s backup story exists yet; this fix is the single-box (`docker-compose.prod.yml`) path
    only, and does not block the eventual move to S3-compatible object storage (docs/15 §4
    Option 2) — the archive format is exactly what an upload step to that would ship.

- **docs/17 Phases 1-4 hardening pass (2026-08-24).** An autonomous session instruction: sweep
  every already-documented deferred/unverified item across Phases 1-4 and close what's safe and
  in-scope, without inventing new product surface. Not a new feature; exclusively coverage and
  verification. Three real findings, one of them a regression this same session introduced.
  - **⚠️ Found by actually re-running the full suite, not by inspection: `test_db.py`'s
    `EXPECTED_TABLES` was never updated for the new `workflow_tests`/`workflow_test_runs` tables
    from the commit immediately before this one.** `test_all_models_registered` failed
    (`47 == 45`) the moment the full suite ran — this is precisely the test this repo relies on
    to catch a table nobody remembered to register anywhere else (the same class of bug Phase
    1's own entry names), and it caught its own family's newest instance within one commit.
    Fixed by adding both table names. **Lesson restated, not new**: running only the test files
    that seem relevant is not the same as running the suite — this file has said that before and
    proved it again.
  - **A real, previously-undocumented, zero-coverage gap: the MCP server registry endpoints
    (`POST/GET /v1/mcp/servers`, `POST /v1/mcp/servers/{id}/test-connection`) had no test
    coverage of any kind since they shipped in Phase 1 (2026-08-19)** — not even a pure-logic
    unit test. Found while checking Phase 1-4 for "service-layer function only covered by
    pure-logic tests, not real DB-session integration tests" per this session's own instruction;
    this one had *no* tests at all, which is worse. New `tests/test_mcp.py`: CRUD, org-scoping,
    RBAC (viewer read-only, editor has `TOOLS_MANAGE`), `test-connection` mocked at
    `app.tools.service.mcp_list_tools` (same reasoning `test_admin.py` already applies to n8n —
    no real stdio spawn or SSE socket in the suite) covering both success and a protocol failure
    resolving to `ok: false`, not a 5xx. Also closed the identical gap one layer down:
    `POST /v1/tools` with `type: "mcp"` (config validation — missing server_id/tool_name, unknown
    server, cross-org server) and `execute_mcp_tool()` itself (the actual DB-session dispatch: a
    real tenant-isolation check, a disabled-server check, and a protocol error surfacing as a
    `ToolResult`, not an exception) — all previously untested. 17 new tests total, all passing on
    first real run against actual Postgres.
  - **Confirmed, not assumed: no RBAC gap between `workflows_awaiting_review` and
    `agents_with_unpublished_changes`.** Both fields live only in `app/modules/admin/`, and every
    admin route (including the one that returns them, `GET /v1/admin/orgs`) is gated by the same
    `require_staff` dependency, already exercised for exactly this route by
    `test_non_staff_blocked_from_all_admin_endpoints`. There is no org-scoped path (viewer/
    editor/owner) that can reach either field — the admin console is staff-only and
    cross-tenant-by-design, which *is* its purpose, not a leak. No new test needed; the existing
    one already proves the boundary that matters.
  - **Added, not previously covered: the loop node's own `config.max_iterations` cap**, a
    loop-local ceiling separate from `AgentBudget.max_steps` (already well covered — a 10,000-
    item list capped correctly by the shared budget, `test_loop_over_a_large_list_is_hard_capped
    _by_the_shared_budget`). New `test_loop_config_max_iterations_stops_early_independent_of
    _budget` proves `max_iterations` binds first even under a deliberately generous budget.
    Everything else this item asked to check was already thoroughly covered before this pass:
    `agent`/`sub_agent` malicious-content fixtures proving `neutralize_injections()`
    (`test_agent_node_reply_is_neutralized_before_reaching_a_variable`,
    `test_sub_agent_shares_the_budget_and_sanitizes_the_result`), `sub_agent`'s call-depth cap
    under a self-referential cycle (`test_sub_agent_call_depth_cap_fails_loudly_not_silently`),
    and `delay`'s duration ceiling (`test_delay_node_rejects_a_duration_beyond_the_configured
    _maximum`) — all already existed from the Phase 2 gap-closure pass, matching the rigor the
    `tool` node already had. Named explicitly here rather than re-built, per this session's own
    instruction not to invent work that's already done.
  - **All migrations for docs/17 Phases 1-4 (`0022`-`0027`) were already individually
    up/down/up-verified against real Postgres** at the time each shipped — reconfirmed by
    re-checking each phase's own PROGRESS.md entry rather than re-running six migrations that
    were never in doubt.
  - **Full backend suite re-run end to end after every Phase 1-4 commit, not just the touched
    test files: 1102 passed, 4 skipped, 1 pre-existing unrelated failure**
    (`test_playground_without_handoff_feature_does_not_trigger`, the same 502 already confirmed
    via `git stash` to predate this entire track — reconfirmed again here, still true). Frontend
    re-run too: `tsc`/vitest (187/187) clean, but `eslint` surfaced one pre-existing, unrelated
    failure in `components/builder/playground.tsx` (an un-escaped `"` in
    `react/no-unescaped-entities`, predating this session per `git log` — not touched by anything
    in docs/17). Fixed as the trivial one-line correction it is, not filed as a tracked gap.

- **docs/17 Phase 5 (Integration SDK) — gate checked, deliberately held (2026-08-24).**
  `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §7 gates Phase 5 on Phases 1-4 "running against at
  least one real client workflow without a Sev-1/Sev-2 incident" — a factual claim about
  production state, not a permission a task instruction can waive by naming Phase 5. **Checked
  honestly: the gate is not met.** Every workflow that exists anywhere in this project's history
  is a test fixture or a manually-created object in this session's own dev/test database —
  `Workflow`/`WorkflowVersion` shipped THIS SESSION (Phase 2 backend 2026-08-19, canvas + gap
  closure 2026-08-24). No client org has a published workflow serving real traffic; there is no
  production deployment this session has visibility into at all, only local dev and the
  tx-rollback test Postgres. Per docs/17 §7's own instruction ("do not pull it forward"),
  **Phase 5 was not started** — no `IntegrationDefinition` contract, no `bf init`/`bf deploy`
  CLI, nothing under §8. This is the expected, documented outcome, not a shortfall: workflows are
  too new to have a real integration to validate a contract against, and building one now would
  be exactly the premature-abstraction risk the gate exists to prevent. Revisit once a real
  client is running a published workflow in production.

- **Workflow regression testing + WORKFLOWS_PUBLISH test-failure gate (2026-08-24).** Closes the
  first item flagged (not fixed) in the Phase 4 report: Phase 3's own DoD wanted the
  test-failure publish gate on both `AGENTS_PUBLISH` and `WORKFLOWS_PUBLISH`, but no "workflow
  test" entity existed for the workflow side to check against. New `workflow_tests`/
  `workflow_test_runs` tables (migration `0027`, verified up/down/up against real Postgres) —
  a new pair, not a `workflow_id` column bolted onto `agent_tests`, because that table's shape
  (`input_message`, `scripted_tool_calls`, `expected_final_answer_contains`) is a chat-turn
  "final answer" concept a workflow run's `status`+`variables` outcome doesn't have. Full
  reasoning in ADR-081. New module `app/modules/workflow_tests/` mirrors
  `app/modules/agent_tests/` in structure: cached mode (scripted `tool`/`agent`/`sub_agent` node
  outputs, keyed by tool_name/agent_id/workflow_id — graph.py's executors are never told which
  graph node_id called them) is the default and the only mode that may run automatically; live
  mode (the real DB-backed executors `run_workflow_now` itself uses) is explicit only.
  `workflows/service.py::publish_version` now calls
  `workflow_tests.service.latest_batch_has_failures` exactly the way
  `agents/service.py::publish_version` already calls the Agent equivalent — same opt-in rule
  (no tests = publishes exactly as before), same "latest batch, not latest case" semantics.
  18 new tests (CRUD, cross-org 404, RBAC, cached-mode pass/fail on status/variables/visited-
  nodes, a live-mode smoke test on a graph with no external nodes so it needs no real network
  call, all four publish-gate states). Every tool/agent/sub-workflow result — scripted or real —
  still passes through `neutralize_injections()` before touching `variables`, because that call
  lives inside `graph.py`'s own node handlers, not the injected executor, so nothing new had to
  be added here to keep that rule. ruff/mypy clean.

- **docs/17 Phase 4 — Version/Approval Workflow (2026-08-24).** Triggered by name immediately
  after Phase 3. Re-read both docs/17 spec files' Phase 4 sections first, then read
  `AgentVersion`'s actual draft/publish/rollback code (`app/modules/agents/service.py`) rather
  than assume it, per this session's own instruction — a genuinely useful check, since it found
  Agent has no real submit-review step at all (see ADR-080).

  **Lifecycle**: `submit_for_review()` (`WORKFLOWS_WRITE`, `draft -> in_review` only, 400
  `workflows.submit_review_invalid_state` otherwise) makes real a status value
  `WorkflowVersion.status` has carried since Phase 2 without anything ever setting it.
  `rollback()` (`WORKFLOWS_PUBLISH`) matches `agents/service.py::rollback` exactly: moves
  `current_version_id` onto an existing already-published version, no new row, 400
  `workflows.rollback_unpublished` if the target was never published. `publish_version` is
  deliberately NOT gated on having passed through `in_review` — Agent's own publish has never
  required a prior step.

  **Diff**: `GET /v1/workflows/{id}/versions/{a}/diff/{b}` (`WORKFLOWS_WRITE`), backed by
  `app/workflows/diff.py` — a structural diff from two node-id-keyed maps, not a JSON/text diff:
  nodes added/removed by id, nodes in both compared by type/config equality so a config change
  reads differently from an add/remove, edges as `(source, target, condition)` sets, tool/MCP
  references from `tool_name`, and a variable read/write schema extracted per node type via
  hand-maintained tables mirroring `graph.py`'s handlers (condition/template parsing imports
  `graph.py`'s own `_COND_RE`/`_VAR_PATTERN` so that part can't drift). No `eval()`.

  **Admin console**: `workflows_awaiting_review` alongside the existing
  `agents_with_unpublished_changes`, same computed-count shape, different predicate — counts
  `WorkflowVersion.status == "in_review"` directly rather than aping Agent's version-number
  proxy, since workflows now carry a real signal to read.

  **⚠️ The two spec files disagree on diff scope, and the narrower one was built.**
  `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §7's Phase 4 line says diff view covers "system
  prompt / model / tools / RAG / workflow-graph changes between versions" — an Agent-and-
  Workflow list. The authoritative, binding Definition of Done in
  `docs/17-IMPLEMENTATION-PROMPT.md` narrows this to "node/edge changes, tool/MCP-server
  changes, variable schema changes" — workflow-graph only. Built to the DoD: no Agent-level
  diff (system prompt/model/RAG) exists anywhere in this codebase as precedent, and inventing
  one wasn't asked for by this phase's instructions. If a diff over Agent versions is wanted
  later, it is a new feature, not an extension of `app/workflows/diff.py`. **Confirmed and now
  recorded directly in ADR-080 itself** (2026-08-24 follow-up), not only here — closes the
  second item flagged in the original Phase 4 report, since a future session reading the spec
  should hit this note in the ADR before rediscovering the ambiguity from zero.

  **No frontend UI beyond the admin column** — docs/17 Phase 4's own Definition of Done lists
  only backend/API items (matching Phase 3's own pattern, unlike Phase 2's explicit Playwright
  requirement), so a "Submit for review"/"Diff" UI in the workflow canvas is a spec-matching
  deferral, not a scope cut. **⚠️ Also noted, not fixed — out of this phase's scope: Phase 3's
  own Definition of Done said the test-failure publish gate should cover
  `WORKFLOWS_PUBLISH`/`AGENTS_PUBLISH` both, but only `agents/service.py::publish_version` ever
  got it** (`docs/PROGRESS.md`'s Phase 3 entry only documents the Agent wiring). There is no
  "workflow test" entity for a workflow-side gate to check against — `agent_tests` is
  agent-scoped only — so closing this gap means designing what a workflow test even is, which
  this phase's instructions did not ask for and is a genuinely separate piece of work.

  **Phase 5 (Integration SDK) remains deliberately held**, per its own gating condition in
  docs/17 §7 and `docs/17-IMPLEMENTATION-PROMPT.md`: Phases 1–4 running against at least one
  real client workflow with no Sev-1/Sev-2 incident, which is not yet true. Not an oversight.

  Suites: backend full **1067 passed, 4 skipped, 1 pre-existing unrelated failure**
  (`test_playground_without_handoff_feature_does_not_trigger`, the same one Phase 3 confirmed
  predates this track via `git stash`); ruff/mypy `app/` clean. Frontend `tsc`/`eslint` clean,
  vitest **187/187**. New coverage: 8 new `test_workflows.py` cases (submit-review, rollback,
  diff ×2, plus RBAC matrix additions) + 1 new `test_admin.py` case driving submit-review→publish
  through the real HTTP client and asserting the admin count moves 0→1→0. See ADR-080. Tagged
  `agentic-phase-4-complete`.

- **docs/17 Phase 3 — Agent Testing (2026-08-24).** Triggered by name immediately after Phase 2
  closed (`agentic-phase-2-complete`), per docs/17 §7's phase order. Re-read
  `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §7 and `docs/17-IMPLEMENTATION-PROMPT.md`'s Phase 3
  Definition of Done in full before writing any code, per this session's own Stage 2
  instruction — Step 0's reference-repo study and 5 open questions were a one-time
  track-level prerequisite already satisfied at Phase 1 kickoff, not repeated here.

  **New tables, checked against the spec rather than assumed:** `agent_tests` (an
  author-defined scenario — input, cached-mode script, expected outcome) and
  `agent_test_runs` (one scenario's one execution's actual-vs-expected result), migration
  `0026`. docs/17 §7/§10 names these two tables and a REST API explicitly
  (`POST /v1/agents/{id}/tests`, `POST /v1/agents/{id}/tests/run`,
  `GET /v1/agent-test-runs/{id}`) — unambiguously a persisted, product-facing feature, not the
  docs/11 Phase D red-team corpus's YAML-fixture-file shape, which was the other shape this
  session's instructions asked to weigh before building. See ADR-079 for the full reasoning,
  including why neither existing execution-trace table (`agent_steps`, `WorkflowRun`) was
  reused — neither has any notion of an *expected* outcome to diff against.

  **Cached mode (the default, and the only mode a batch run takes unless the request body
  explicitly asks for `"live"`) reuses `app.llm.fake.MultiRoundToolProvider` directly** — a
  test case's `scripted_tool_calls`/`scripted_final_answer` feed its exact constructor shape,
  already built and proven in Phase 1's own budget tests. The runner otherwise reuses the
  Agent Playground's own request-building helpers
  (`_build_request`/`_retrieve_context`/`_playground_tooling`/`_resolve_playground_provider`)
  unchanged, per this session's instruction to build on that existing "run the draft version,
  not counted as production traffic" pattern rather than a parallel one — imported locally to
  break a circular import with the publish-gate check calling back the other way. Unlike the
  Playground, the runner sets `guard_output=True`: a scripted final answer containing PII the
  org hasn't allowlisted, redacted in `actual_final_answer`, is exactly the kind of pipeline
  regression cached mode exists to catch, not "would the model say this."
  **Live mode is never automatic** — confirmed by test: a request with no `mode` field defaults
  to `"cached"`, matching the Definition of Done's explicit requirement that a live tier must
  never fire on every draft save (docs/17 §7 names the exact prior incident this guards
  against: the 2026-08-10 manual checklist run that exhausted the Groq free tier on 61 probes).

  **Publish gate wired into `agents/service.py::publish_version`, opt-in.** Blocks with a new
  `agents.tests_failing` 400 only when the agent's most recent test BATCH
  (`agent_test_runs.batch_id`, shared by every case triggered from the same `POST /tests/run`
  call) contains a failure — an agent with zero test cases, or cases that have never been run,
  publishes exactly as before Phase 3 existed. A later PASSING batch un-blocks publish even
  after an earlier one failed — "the latest test run" means the latest batch, not any one
  case's history read in isolation. See ADR-079 for why opt-in was chosen over "no tests =
  blocked," and for the one real gap this leaves open: the gate does not force the most recent
  batch to have run against the CURRENT draft — an author could edit the prompt after a passing
  run and publish on stale evidence, flagged rather than silently assumed solved.

  **No frontend UI ships in this phase** — docs/17 Phase 3's own Definition of Done lists only
  backend/API items (unlike Phase 2's, which explicitly required a Playwright check), so a
  "Tests" tab in the agent builder is a spec-matching deferral, not a scope cut.

  **Verification.** `ruff`/`mypy app/` clean; migration `0026` applied/downgraded/reapplied
  against real Postgres; app imports cleanly (the publish-gate/runner mutual dependency is
  real and resolved with a local import on one side, matching the established pattern already
  used for `_dispatch_run`'s Celery-task imports). 16 new tests covering CRUD, cross-org 404,
  RBAC, cached-mode pass/fail assertions (a real builtin `calculator` tool bound to the test
  agent so a scripted tool call has something network-free and deterministic to dispatch to),
  the default-cached-mode proof, the empty-batch 400, and all three publish-gate states
  (blocked / allowed / unaffected-with-no-tests / re-allowed-after-a-later-pass). Full backend
  suite **1060 passed, 4 skipped, 1 pre-existing unrelated failure**
  (`test_playground_without_handoff_feature_does_not_trigger`, confirmed via `git stash`
  earlier this track to predate it). Tagged `agentic-phase-3-complete`.

- **docs/17 Phase 2, finishing pass — items 1–6 (2026-08-24).** Triggered by name, six discrete
  commits per item as instructed (`527912d`, `c536270`, `02b7935`, `0d41083`, `3f0b004`,
  `3e6061c`), each independently green. Landed with three genuine deferrals (delay node real
  wait semantics, run-history overlay, tool-node argument editing) named explicitly rather than
  implied — see each item's own note below — so **not tagged `agentic-phase-2-complete` at the
  time**, same discipline `agentic-phase-2-backend-complete` set on 2026-08-19.
  **✅ All three closed the same day**, in a follow-up Stage-1 pass, each its own commit:
  delay (`edfdba7`, ADR-076), run-history overlay (`fb68cc3`, ADR-077), tool arguments
  (`acaebab`, ADR-078). Full re-verification after all three: backend **1044 passed, 4 skipped,
  1 pre-existing unrelated failure**; frontend `tsc`/`eslint`/`next build` clean, vitest
  **187/187**, and every workflow-canvas Playwright spec (`30`, `31`, `32`) plus two adjacent
  specs touching the same agent-builder page green. **Tagged `agentic-phase-2-complete`.**
  1. **Six new node types** — switch (N-way, literal→branch map, no `eval()`), loop (iterates
     via a graph CYCLE so the existing node-at-a-time walker needed no new execution model, and
     `AgentBudget.max_steps` became a hard iteration cap for free), transform (whitelisted
     string/number ops, no `eval()`), agent (invokes a published `Agent` through the real
     `run_turn`, sharing the SAME budget per docs/17 §2 rule 3), sub_agent (invokes another
     `Workflow`, call depth tracked on the budget itself via new `WORKFLOW_MAX_CALL_DEPTH`, so a
     self-referential or indirect cycle fails loudly instead of hanging), delay (shape-only at
     first — accepted for validation/canvas authoring, failed loudly if executed).
     **✅ CLOSED 2026-08-24 (Stage 1 of the same-day gap-closure follow-up):** delay now pauses
     for real (`status: "paused_delay"`) and schedules a Celery task at the target `eta`
     (`resume_delayed_workflow_task`, `app.workflows.service._schedule_delay_resume`) rather
     than blocking a worker — same first-visit/resume split `approval` already uses. Still
     refuses loudly, on purpose, under `celery_task_always_eager` (no worker exists to wake an
     eager run up later) and above the new `WORKFLOW_MAX_DELAY_SECONDS` cap (default 24h).
     Verified against a REAL Celery worker, not mocks: a 5-second delay node paused, stayed
     paused for the interval, and resumed automatically ~5.1s later with no human action.
     **`max_runtime_s` deliberately does not span the real wait** (resuming reconstructs the
     budget fresh, same as an approval pause already does) — checked, not assumed; see
     ADR-076 for why the alternative (a wall-clock `started_at`) was considered and rejected.
     `max_steps`/`max_tool_calls`/`max_cost_usd` all still accumulate correctly across the
     pause. A new guard in `execute_queued_run` stops a stale scheduled wake-up from reviving a
     run an operator cancelled while it was waiting (Celery cannot un-schedule an already-
     queued `eta` task). 8 new graph-engine tests + 3 new service-layer tests, plus a live
     smoke test against a real worker (not part of the automated suite). See ADR-076.
  2. **Celery async execution.** `run`/`resume` now dispatch (enqueue in production, run
     in-process only under `celery_task_always_eager`) instead of walking the graph inline —
     deliberately NOT via Celery's own eager machinery, which would hit the exact
     `asyncio.run()`-inside-a-running-loop trap `app.core.email.queue_email` already carries a
     fix for (CLAUDE.md §12). Steps persist incrementally as a real worker produces them.
     Verified against an actual `--pool=solo` worker and real Redis, not only mocks. **⚠️
     Accepted, not fixed: the same flush-then-`.delay()` race `enqueue_document_ingestion`
     already has** (a worker could in principle start before the enqueuing request's
     transaction commits) — matching existing convention rather than a workflows-only fix that
     would also have required breaking the test harness's transaction-rollback isolation.
  3. **Draft-version test-mode execution.** `POST /v1/workflows/{id}/test-run` executes the
     LATEST version (draft or published, mirroring the Agent Playground's own semantics) with a
     new `WorkflowRun.is_test` flag (migration 0024) rather than an ephemeral, non-persisted
     run. `WORKFLOWS_WRITE`, not `WORKFLOWS_PUBLISH` — testing your own draft is authoring, not
     publishing.
  4. **Approval → Handoff inbox integration.** Reuses `Handoff` (migration 0025:
     `conversation_id` now nullable, new nullable `workflow_run_id`) rather than a parallel
     "workflow approval" table. New `GET/POST /v1/inbox/workflow-approvals[/{id}/decide]`,
     gated on `INBOX_HANDLE` — proven with the `operator` role specifically, which has
     `INBOX_HANDLE` but not `WORKFLOWS_WRITE`, so the raw resume endpoint correctly refuses them
     while the inbox path accepts them. `resume_workflow_run` split into an RBAC-checked
     wrapper and `resume_workflow_run_unchecked` so both entry points share one execution path.
  5. **Systematic RBAC matrix.** Most of this was already covered by the original gap-closure
     session (2026-08-24, below) plus items 1–4's own tests; what was missing was a single pass
     proving READ/WORKFLOWS_WRITE/WORKFLOWS_PUBLISH on every one of the 13 endpoints, not just
     the ones another test's setup happened to touch — `delete_workflow`, `cancel_workflow_run`,
     `resume_workflow_run`, `list_versions`, `get_workflow` and `list_run_steps` had no RBAC
     test at all before this.
  6. **React Flow canvas** (`@xyflow/react`, new dependency) — a "Workflows" tab in the agent
     builder: drag-and-drop palette over all 13 node types, one custom node renderer with
     branch-labeled handles for condition/switch/approval/loop, a properties panel reusing
     existing form primitives and populating agent/tool/workflow pickers from the real
     endpoints, Save (draft)/Publish, and a Test run button that polls a new
     `GET /v1/workflow-runs/{id}` (added alongside — the canvas needs the run's own terminal
     status, not just its step list) plus `.../steps`, overlaying each step's outcome onto the
     matching node live. **The Playwright check caught three real bugs before a human ever
     opened a browser**: a freshly created workflow's default graph had no `end` node and would
     have failed its own first save; Save/Test-run were only `disabled` for a read-only role,
     not hidden, unlike every other RBAC-gated control in this codebase; publishing after
     saving left the toolbar showing the stale "Saved as draft v1" label. Fixed a small
     pre-existing bug on the way: the agent page's Playground-visibility check was hardcoded to
     `tab !== "channels"` rather than deriving from `FULL_WIDTH_TABS`, which the new
     "workflows" tab would otherwise have inherited (a stray Playground stacked below an
     already-tall canvas). Native HTML5 drag-and-drop from the palette is exercised by
     Playwright but the jsdom unit suite stubs the whole canvas out and covers only the
     list/create screen around it — unchanged, still true.
     **✅ CLOSED 2026-08-24 (Stage 1 of the same-day gap-closure follow-up), both deferrals
     named above:**
     - **Run-history overlay.** New `GET /v1/workflows/{id}/runs` (newest first, joined through
       every version — an author reviewing history reasonably expects a run against an older
       draft to still show up) backs a history picker in the canvas toolbar. Reopening a PAST
       completed run reuses the exact same `runId`/`run`/`steps` polling state a live test run
       already drives — no second code path; the existing `refetchInterval` already stops
       polling on a terminal run's first fetch, so a finished historical run "just works."
       Found and fixed a real ordering bug the first time the newest-first test ran against
       real Postgres: two runs created moments apart in the same transaction get an IDENTICAL
       `started_at` (`func.now()` freezes at transaction start, not per-statement) — added
       `id DESC` as a tiebreak (`WorkflowRun` uses time-ordered UUIDv7 keys), same fix shape as
       the FTS `ts_rank`/`chunks.id` tiebreak (docs/14 K1+K2). See ADR-077.
     - **Tool node argument editing.** The properties panel now exposes `config.arguments` as
       raw JSON, matching the EXISTING agentic-runtime tool UI's own pattern (the Tools tab's
       "Test tool" dialog already asks for call arguments the same way, since a tool's argument
       shape varies per tool and isn't known statically to either UI). Invalid JSON is never
       propagated to the graph — the last-known-good value stays in effect with a visible
       inline error. See ADR-078.
     - 2 new backend tests (list-runs ordering + cross-org 404) + 2 new Playwright specs
       (`31-workflow-run-history.spec.ts`, `32-workflow-tool-arguments.spec.ts`), plus the
       original canvas spec's status-panel label assertions updated for the rename from
       "Test run: {status}" to "Run: {status}" (the panel now serves both live and historical
       runs, not only ones just started from the canvas).
  - **Verification.** `ruff`/`mypy app/` clean across every commit; migrations 0024/0025
    applied/downgraded/reapplied against real Postgres; full backend suite **1033 passed, 4
    skipped, 1 pre-existing unrelated failure** (`test_playground_without_handoff_feature_does_not_trigger`,
    confirmed via `git stash` earlier this track to predate it). Frontend: `tsc --noEmit` and
    `next build` clean, `eslint` clean (one pre-existing unrelated error in `playground.tsx`,
    untouched by this pass), vitest **187/187**, and the new Playwright spec plus three
    existing specs touching the same tab/Playground logic all green.
  - ADR-075 (docs/DECISIONS.md) records the non-obvious calls: Handoff reuse for approvals,
    call-depth-on-the-budget for sub_agent cycles, `max_steps`-as-loop-cap by default,
    `is_test` as a persisted column, and the eager-mode dispatch pattern.

- **docs/17 Phase 2 gap closure — DB-backed integration tests for the workflow CRUD/router layer
  (2026-08-24).** Closes the exact gap the 2026-08-19 entry below flagged as open: `create_workflow`,
  `update_workflow`, `delete_workflow`, `create_version`, `publish_version`, `run_workflow_now`,
  `resume_workflow_run`, `cancel_workflow_run`, and `list_run_steps` had only ever been exercised
  by the pure-Python engine tests — never over the real HTTP client, never against a real Postgres
  session, never with RBAC or cross-org ownership checked. `tests/test_workflows.py` (14 tests,
  real DB via the `client`/`db_session` fixtures) now covers CRUD + soft delete, invalid-graph
  rejection at `create_version`, the full draft→publish→run lifecycle, an approval-node
  pause/resume round trip (asserting the step log records the paused visit and the resumed visit
  as two distinct rows), `not_published`/`not_paused` 400s, idempotent cancel, viewer-read/
  editor-write-not-publish/owner-publish RBAC, and 404-not-403 on cross-org access to both a
  workflow and a run.
  **⚠️ Found a real bug the moment the run/resume/cancel paths were hit over real HTTP:**
  `run_workflow_now`/`resume_workflow_run`/`cancel_workflow_run` all set
  `run.completed_at = sa_func.now()` (a raw SQL expression) and then serialized the same
  in-memory ORM object straight into `WorkflowRunOut` with no intervening flush/refresh —
  every terminal run response (`completed`, `failed`, `budget_exceeded`, and every `cancel` call)
  would have thrown a Pydantic `ValidationError` and come back as a 500, in production, on the
  first call. The pure-Python engine tests never touched this line — `run_workflow()` itself
  doesn't set `completed_at`, the service layer does, and only serialization through the real
  Pydantic response model surfaces the type mismatch. Fixed to `dt.datetime.now(tz=dt.UTC)`,
  matching the convention every other service in the codebase already uses for this exact pattern
  (`app/modules/agents/service.py`, `app/modules/apikeys/service.py`, `app/modules/auth/service.py`,
  `app/modules/knowledge/service.py`). This is the second time in this track that the DB-backed
  integration layer caught something the unit layer structurally could not see — same lesson as
  the 2026-08-04 "Phase B" allowlist note in CLAUDE.md's session log: an untested direction is not
  a verified direction, even when the code under it looks obviously correct.
  Verification: `ruff check` and `mypy app/` clean (206 files); `tests/test_workflows.py` 14/14;
  `tests/test_workflow_graph.py` + `test_db.py` unaffected (32/32); full suite
  **991 passed, 4 skipped, 1 pre-existing unrelated failure** (the same
  `test_playground_without_handoff_feature_does_not_trigger` 502, confirmed via `git stash`
  against the clean tree to predate this track — see Phase 1's entry).
  **Still open** (unchanged from the 2026-08-19 entry, not addressed by this slice): no Celery
  wiring, no React Flow canvas, 7 of 11 node types, no test-mode execution against a draft version,
  no Approval→Handoff inbox integration.
  **DB-verified, not just test-suite-verified.** Migration `0023` re-run against the real
  `botforge-postgres-1` (port 5433): `upgrade head` (no-op, already current) → `downgrade -1`
  (`0023_workflows` → `0022_agentic_runtime`) → `upgrade head` (back to `0023_workflows`), all
  three clean. Then a live smoke test through the actual running API (keyless E2E mode, port
  8010, `ALLOW_SELF_SERVE_ORGS=true`) rather than the httpx test client: created a workflow,
  published a version with an approval node, ran it to `paused_approval`, resumed it to
  `completed`, and separately ran + cancelled a second run — `WorkflowRunOut.completed_at` came
  back as a real ISO datetime string on the wire in both terminal states, confirming the
  `sa_func.now()` fix holds outside the test transaction too, not only inside the tx-rollback
  fixture. Committed as `37d5803`.

- **docs/17 Phase 2 — Visual Workflow Builder, backend slice (2026-08-19).** Triggered by name
  per CLAUDE.md §10b, immediately after Phase 1. **This is a backend/API foundation, not a
  finished Phase 2** — see ADR-074's Consequences for the explicit list of what's deferred
  (Celery wiring, the React Flow canvas, 4 of 11 CORE+ node types, draft-version test runs, and
  Approval→Handoff inbox integration). Framed honestly rather than claimed complete.

  **What shipped.** `app/workflows/graph.py` — a pure-Python execution engine: `run_workflow()`
  walks a `{"nodes":[...], "edges":[...]}` graph one node at a time, synchronously, reusing
  `app.chat.budget.AgentBudget` as its bounding mechanism (steps/tool-calls/runtime/cost) —
  the same instance a caller can share with a future nested call, inheriting Phase 1's
  never-reset rule for free rather than re-implementing it. Seven node types: start, end,
  message, condition, set_variable, approval, tool. `evaluate_condition()` is a narrow
  hand-rolled `var OP literal` parser (five operators, no boolean chaining) — never `eval()`,
  per docs/17 §11's ban on arbitrary code execution. `render_workflow_template()` reuses Phase
  F's `escape_value()` (`app/chat/variables.py`) for the same single-pass, unknown-vars-render-
  empty discipline, applied to workflow-defined variables rather than the fixed system-prompt
  allowlist. A `tool` node's result gets the IDENTICAL treatment `run_turn` already gives chat
  tool results — `neutralize_injections()` before it can reach a variable or a later node's
  rendered text, no exception for the result being "one of ours."

  **Data model** (`app/models/workflows.py`, migration `0023`): `Workflow`/`WorkflowVersion`
  deliberately mirror `Agent`/`AgentVersion`'s draft-publish shape rather than inventing a
  second pattern (ADR-074). `WorkflowRun.budget` persists a serialized `AgentBudget` so a
  paused-on-approval run resumes with what it already spent, not a fresh ceiling —
  `test_workflow_graph.py::test_resume_shares_and_accumulates_the_same_budget` proves this
  against the real engine, mirroring Phase 1's own nested-budget test. `WorkflowStep.output`
  is sanitized-only, same rule as `agent_steps.tool_output`.

  **API** (`app/workflows/{service,router}.py`, new `WORKFLOWS_WRITE`/`WORKFLOWS_PUBLISH` RBAC
  permissions mirroring `AGENTS_WRITE`/`AGENTS_PUBLISH`'s split): 9 endpoints — workflow CRUD
  under `/v1/agents/{agent_id}/workflows` + `/v1/workflows/{id}`, versioning + publish, `POST
  /v1/workflows/{id}/run` (synchronous — see the async caveat below), and
  `/v1/workflow-runs/{id}/{resume,cancel,steps}`. Tool nodes reuse the exact `Tool`-row
  dispatch chat's `run_turn` uses (`execute_tool_call`), not a second tool-execution path.

  **⚠️ Execution is synchronous-in-request; there is no Celery wiring yet.** A long-running
  workflow blocks the HTTP call for up to `max_cost_usd`'s effective runtime cap (`AgentBudget`
  default `max_runtime_s=30`). The `WorkflowRun` row already externalizes all resumable state
  (variables/budget/current_node_id read from Postgres, not kept in process memory), so wiring
  a Celery task to drive the same `run_workflow_now`/`resume_workflow_run` functions is additive
  — but it is not done, and nothing async/queued runs today.

  **Verification — re-run against the real dev stack; the migration gap above is closed.**
  `ruff check` and `mypy app/` clean (206 source files — the initial draft needed four unused
  `type: ignore` comments removed and two `bool()` casts added on `evaluate_condition()`'s
  `==`/`!=` branches once mypy actually ran against it). Migration `0023` applied to the real
  Postgres (`botforge-postgres-1`, port 5433), then downgraded and re-upgraded — clean both
  directions, same check migration `0022` got. All 9 workflow paths confirmed in the live
  OpenAPI schema. `tests/test_workflow_graph.py`'s 27 tests (graph validation, the no-`eval()`
  condition parser incl. a direct code-injection regression case, template escaping,
  linear/branching execution, approval pause/resume with real shared-budget accumulation,
  tool-result sanitization at both the write and read point) plus `test_db.py` (needed
  `workflows`/`workflow_versions`/`workflow_runs`/`workflow_steps` added to `EXPECTED_TABLES`)
  all green. Full suite: **977 passed, 4 skipped, 1 pre-existing unrelated failure** (the same
  `test_playground_without_handoff_feature_does_not_trigger` 502 Phase 1's entry already
  documented and confirmed via `git stash` predates this track). **Still genuinely open, not
  silently fixed:** no DB-backed integration test exists yet for the CRUD service/router layer
  — `create_workflow`/`publish_version`/`run_workflow_now` have never been exercised over the
  real HTTP client with RBAC/ownership checks, only the execution engine itself has coverage.

  Tag: `agentic-phase-2-backend-complete`.

- **docs/17 Phase 1 — Agentic Runtime (2026-08-19).** The first phase of the agentic
  runtime/builder track (`docs/17-AGENTIC-RUNTIME-AND-BUILDER.md`, triggered by name per
  CLAUDE.md §10b — not autonomous). Closes the biggest gap the repo-comparison analysis
  identified: BotForge's chat path was one retrieval + one generation + at most one bound n8n
  tool call, with no multi-step think→act→observe loop and no generic MCP tool provider.

  **What shipped.** `app/chat/budget.AgentBudget` — a single mutable four-dimensional ceiling
  (`max_steps`/`max_tool_calls`/`max_runtime_s`/`max_cost_usd`) that `run_turn` decrements as
  it goes; deliberately has no `.child()`/`.fork()` constructor so a nested/sub-agent call must
  be handed the SAME instance and draw from the parent turn's remaining allowance, never a
  fresh one (docs/17 §2 rule 3, ADR-070 — the exact gap found in OpenManus's `BaseFlow`, which
  never propagates `max_steps` into a spawned sub-agent). `app/tools/mcp_client.py` +
  `mcp_tool.py` + `mcp_router.py` — a provider-agnostic MCP client (stdio + SSE), org-scoped
  server registration (`MCPServer` model, migration `0022`), `POST /v1/mcp/servers` +
  `/test-connection`, wired into `resolve_agent_tools`/`build_tooling` behind `include_mcp`
  (registration never implies usage — same split n8n binding already has). `agent_steps` table
  + `app/chat/agent_trace.py` — the per-turn trace (`kind: think|tool_call|final_answer`),
  populated only when `run_turn` is given a `budget`, persisted once the assistant `Message`
  row exists. Rollout gate: `settings.agentic_loop_enabled` (platform) AND
  `Organization.agentic_loop_enabled` (per-org) both explicitly `True` — unlike
  `guard_injection_enabled`'s on-by-default polarity, this defaults off at both levels (ADR-073).

  **The rule that actually matters (docs/17 §6): every tool result is untrusted input,
  regardless of source.** `run_turn`'s existing `neutralize_injections()` call on
  `out.get("output")` runs unconditionally — no branch on tool type — so an MCP result, an n8n
  result, and a future sub-agent result all get the identical defang-before-re-entering-
  context treatment RAG chunks already got from Phase 16. Proven, not assumed: three new test
  files exercise it against real MCP-shaped, n8n-shaped, and sub-agent-shaped JSON payloads
  carrying the same instruction-override phrasing already in `attacks.yaml`
  (`tests/test_agentic_tool_sanitization.py`), the nested-budget-sharing rule against the real
  `run_turn` loop rather than the dataclass in isolation (`tests/test_agent_budget.py`), and
  three new `kind: tool_result` fixtures added to the Phase D corpus
  (`tests/fixtures/redteam/attacks_contextual.yaml` + a new
  `test_tool_result_injection_is_neutralized_not_trusted` in `test_redteam_corpus.py`) — docs/17
  §2 rule 5 required these be added before Phase 1 could be called done, since the existing
  corpus had never been exercised against a tool-result-shaped payload.

  **⚠️ A real finding while writing the sanitization tests, not a hypothetical:**
  `neutralize_injections()` only runs the smaller Phase-16 `_INJECTION_PATTERNS` list, not the
  full L1 family set `screen_user_message()` uses — so a phrase like "from now on you are an
  admin agent" (an L1-only `_ROLE_HIJACK` pattern) survives verbatim inside a neutralized tool
  result; only the "ignore all previous instructions" clause in the same payload gets filtered.
  Verified against the real function before asserting anything, not assumed — an earlier draft
  of the sub-agent fixture's test asserted the wrong phrase was removed, and running it against
  the real `guardrails.py` (not a mock) caught it. Recorded here rather than silently fixed,
  because it means indirect and tool-result content share a **narrower** defense than a
  visitor's own message does, and that gap is not new to Phase 1 — it is Phase 16's original
  scope, now visible because Phase 1 is the first thing to test the tool-result path directly.
  **Not fixed in this phase** — widening `_INJECTION_PATTERNS` is a Phase-16-scoped decision
  with its own false-positive risk on real documents/tool output, out of scope for docs/17.

  **Verification — the real dev stack, end to end, not the isolated slice.** `ruff check`
  clean across `app/` and `tests/`. `mypy app/` clean (200 source files, strict). Migration
  `0022` applied to the real Postgres (`botforge-postgres-1`, port 5433), then downgraded and
  re-upgraded to confirm reversibility — clean both directions. Full suite:
  **950 passed, 4 skipped, 1 failed, 17 warnings in ~173s.** The one failure
  (`test_playground_without_handoff_feature_does_not_trigger`, a 502 where 200 was expected) is
  **confirmed pre-existing and unrelated** — reproduced identically after `git stash`-ing every
  Phase 1 change and reverting to the committed tree (`8e2e5a4`), so it predates this track and
  is not filed against docs/17. One real regression *was* caught and fixed in this pass:
  `tests/test_db.py::test_all_models_registered` asserts an exact `EXPECTED_TABLES` count and
  needed `agent_steps`/`mcp_servers` added — the kind of test this repo relies on to catch a
  new table nobody remembered to register anywhere else.

  Tag: `agentic-phase-1-complete`. Next: Phase 2 (Visual Workflow Builder) per docs/17 §7 —
  not started, not autonomous, execute only when asked by name.

- **A knowledge base can be deleted (2026-08-06).** Only the documents inside one could be
  removed before. `DELETE /v1/knowledge/{id}` has existed since Phase 7 with **nothing in the UI
  calling it** — the same shape of gap as the org-delete button and the invitation-accept page.
  It is now a Danger zone on the KB's own page, matching the agent-delete pattern.

  **The button is the small part.** Deleting a KB is quiet by design: `retrieve_for_version`
  filters `deleted_at`, so an agent still pointing at it does not error — it silently retrieves
  nothing. An agent with **no context block** is precisely the state that makes the model answer
  from general knowledge and invent specifics, which this project has already shipped to a
  customer once (`CLAUDE.md`, 2026-08-02). Deleting the KB a live agent depends on therefore has
  to read as a decision, not a tidy-up.

  `GET /v1/knowledge/{id}` now returns **`attached_agents`** — agents whose `rag_config`
  references the KB, flagged `is_live` when it is their *published* version — resolved by JSONB
  containment (`rag_config @> {"knowledge_base_ids": [id]}`) and grouped per agent, since a
  draft and a published version both count as a reference. The **list** endpoint deliberately
  leaves it empty: populating it there is one query per card for something no card acts on.

  The confirmation names those agents and states plainly that they keep answering with nothing
  to ground them. Their config is **not** rewritten on their behalf — the dialog points at the
  Knowledge tab instead, because silently editing an agent someone else published is the kind of
  thing this codebase has repeatedly decided against.

  **A copy bug worth remembering:** the warning originally rendered "It keepsanswering". JSX
  drops the whitespace around an expression in some positions, so text interleaved with ternaries
  lost a space. The source looked correct and the screenshot was ambiguous; it was caught only by
  asserting the rendered DOM text. The strings are now built whole.
  6 backend tests, 6 web unit tests, 2 Playwright checks. Suites: **719 pytest**, **156 vitest**.

- **Admin console: soft-deleted orgs were never filtered, and two more fixture sources were
  invisible to the sweep (2026-08-05).** Follow-up to the E2E sweep below, after the console
  still showed `provision@botforge.dev`, `owner@botforge.local`, *Acme Co*, *Globex Inc* and
  *Deny Default Probe*.

  **The code bug:** `admin.service.list_orgs` had no `deleted_at` filter, while `list_users`
  directly below it always had `.where(User.deleted_at.is_(None))`. A client who deleted their
  workspace stayed in the staff roster permanently, sitting next to live tenants. Now excluded
  by default, with `?include_deleted=true` keeping the audit view — "which client left, and
  when" is a real question, just not what the default roster should answer. Regression test
  asserts both directions.

  **The data:** two fixture accounts predate the `%@example.com` rule. `owner@botforge.local`
  is `seed.py`'s account and is now swept — its demo org is rebuilt in full by `make seed`, so
  nothing is lost that one command cannot restore.

  **`@botforge.dev` is deliberately NOT swept, and the docstring says why.** It is
  `PROVISION_STAFF_EMAIL`, the account `scripts/provision-client.mjs` uses to stand up **real
  clients** — every org it provisions carries `created_by = provision@botforge.dev`. A pattern
  sweep on that domain would have deleted a real client workspace the first time one was
  provisioned through the script. Its leftover test orgs are indistinguishable from a real
  tenant at the schema level, so *Acme Co*, *Globex Inc* and *Deny Default Probe* were removed
  **by slug, once**, rather than by a rule that would fire again later on real data.

  Console now lists five real orgs and four real users; one soft-deleted org is correctly
  hidden. 711 pytest.

- **The Admin console was full of Playwright fixtures; the E2E run now sweeps after itself
  (2026-08-05).** Every "org" in the staff console — *A11y Widget Org*, *Bad Token Org*,
  *Sidebar Org*, *Somewhere Else* — was a **real row from a real signup**. The E2E suite tests
  real flows against a real API rather than mocking the backend, and `playwright.config.ts`
  sets no `DATABASE_URL`, so it signs its fixtures up in the same Postgres the dev app and the
  Admin console are pointed at. Nothing was faked and nothing was broken in the Admin page; the
  test run and real usage simply shared one database.

  Measured before touching anything: **118 scratch orgs and 147 scratch users against 7 real
  orgs.** `app/db/cleanup_devdata.py` already existed and is scoped to `%@example.com` only, so
  it structurally cannot touch a real account — it had just never been run automatically.
  Running it removed all 118/147 and left all 7 real orgs intact (verified by name).

  **The recurrence fix is `scripts/run-e2e.mjs`**, now what `npm run test:e2e` (and `make
  test-e2e`, and CI) actually invokes. It runs Playwright, then always sweeps. Two properties
  matter and both are tested by hand:
  - **Cleanup runs whether the suite passed or failed.** A *failed* run leaves more debris than
    a passing one, so skipping cleanup on failure would skip exactly the case that needs it.
  - **Playwright's exit code is preserved.** Verified explicitly, because getting this wrong
    would make CI green on a failing E2E suite — a far worse bug than the one being fixed. A
    cleanup failure is logged loudly but never overwrites a test result.

  `test:e2e:raw` still runs Playwright without the sweep, for debugging a single spec or a
  `--ui` session where the fixtures should stay put. CI now calls the wrapper too, so the sweep
  cannot drift out of the local path and the CI path independently.

  **Not done, and not needed yet:** pointing the E2E run at a separate database. That is the
  only thing that protects you *mid-run* — the sweep is after-the-fact, so fixtures are still
  briefly visible in the Admin console while the suite is executing. Worth doing only if E2E
  runs start happening while the dashboard is being used for something real.

- **Final palette: four greys, light-first (2026-08-05, ADR-060).** The indigo below was
  rejected on sight and lasted one commit; this replaced it the same day. The interface is now
  `#F3F4F6` page, `#1F2937` ink, `#6B7280` and `#4B5563` for the two levels of secondary text —
  the "white and black premium SaaS" register the indigo attempt had missed.

  **Light became the default rather than an override.** `:root` now holds the light values with
  `.dark` overriding, and next-themes defaults to `light`; the structure was previously
  inverted, so a light palette expressed as an override would have left every un-themed surface
  dark. **The accent is the ink** — a primary button is `#1F2937` with white on it (12.6:1) —
  which constrains its use: anything wearing the accent reads as the single action on screen.

  **Status colours stay saturated**, and are now the only colour in the product. That is the
  argument for keeping them: a greyscale error state is not a state anyone notices. They're
  darkened to sit calmly against grey.

  Dark mode inverts the same four greys with one necessary departure: `#6B7280` is **3.04:1** on
  `#1F2937`, so the secondary greys lighten rather than mirror — the literal value would have
  failed AA on every caption in the app. Shadows became themed (`--shadow` plus two alphas),
  because black at dark-mode strength turns a white card into a smudge.

  One caveat is pinned in the token block: `#6B7280` is 4.83:1 on a white card but **4.39:1 on
  the page background**, so faint text belongs inside cards. axe confirms nothing currently
  violates it. Measured range: light 4.83–14.7:1, dark 5.3–16.1:1.

  The widget's default accent **and** mode moved together — `#1F2937` on a dark panel is
  invisible, so changing one without the other would have shipped a broken default. The four
  live agents had their current mode pinned alongside the colour pinned earlier, so their
  widgets are unchanged. Suites: **710 pytest**, **150 vitest**, tsc + eslint clean, axe green.

- **New palette: indigo on slate, replacing ember on graphite (2026-08-05, ADR-059 — superseded
  the same day by ADR-060 above; kept because the accessibility work it forced still applies).** The
  orange read as loud rather than premium — the opposite of what ADR-010 set out to do. The page
  is now void black `#0B0F19`, cards elevated slate `#131B2E`, the accent electric indigo into
  violet `#6366F1`→`#8B5CF6`, type crisp off-white `#F8FAFC` over muted slate `#94A3B8`. A cyan
  `--glow` (`#06B6D4`) is **rationed to live/running states** and nothing else; semantic
  red/amber/green stay conventional, retuned to the family. The base is deliberately not pure
  black — `#000` makes the hairline borders this UI is built from disappear on OLED. The light
  theme is the exact inverse (off-white page, void-black type) rather than an afterthought.

  `--ember*` became `--accent*` across **64 files**. A token named after a colour it no longer
  is would mislead every future reader — and since `ember` is a substring of *members* and
  *membership*, the replacement was anchored on a non-letter prefix rather than run as a bare
  find/replace.

  **The swap exposed two accessibility problems the orange had masked.** It was light enough to
  carry near-black labels, so eight files hardcoded `#0A0B0D` for text sitting on the accent.
  Indigo wants white — but white on `#6366F1` is **4.47:1**, which the repo's axe gate rejects
  for button text. Hence `--accent-strong` (`#4F46E5`, **6.3:1**) for filled surfaces and
  `--on-accent` for the label, rather than a hex repeated per file. Avatar initials moved from a
  two-stop gradient to a solid fill: white over the violet end was 4.2:1, and a gradient makes
  the contrast of 13px text depend on where the glyph happens to land. Measured across the whole
  palette, the lowest text pair is now `error` at **4.56:1**; axe (serious+critical) passes on
  the auth pages, dashboard, agents and the embedded widget.

  The logo and the usage chart had literal brand hexes; both read the tokens now, so they can't
  drift from the palette again. The invitation/verification email CTA did too.

  **Live widgets were deliberately not repainted.** Every published agent relied on the widget's
  default colour rather than setting one, so moving the default would have changed four client
  sites without warning. `#E8590C` was written explicitly onto those four first; the new indigo
  default applies only to agents created from now on, and each client can be restyled
  deliberately. Test-org agents were left to take the new default. Suites: **710 pytest**,
  **150 vitest**, tsc + eslint clean, axe + widget + shell E2E green.

- **Inviting someone who already has an account works (2026-08-04, ADR-058).** It dead-ended
  before: the accept page opened in signup mode, so an existing user typed their own address,
  hit `auth.email_taken`, and got *"An account with this email already exists."* as a bare
  error. The only way out was a small "I already have an account" toggle at the bottom of the
  form.

  **Nothing was wrong underneath**, which is worth recording because it shaped the fix.
  `accept_invitation()` already reactivates a dormant `Membership` or creates one and applies the
  invited role, and `OrgSwitcher` already moves between orgs — so an account could always hold
  several orgs. The page simply could not see any of it: the token is opaque and accepting is
  all-or-nothing, so it knew neither which org was being joined nor whether the address was
  registered.

  New unauthenticated, rate-limited `GET /v1/orgs/invitations/{token}` returns the org name,
  role, invited address and `account_exists`. The page now leads with **"Join {org} as {role}"**,
  opens in sign-in mode for an existing account, and renders the email **read-only** — the server
  rejects any other address with `org.invite_email_mismatch`, so an editable field could only
  ever produce that error. If signup still reports the address is taken (it was registered
  between preview and submit, or the preview never loaded) the form switches to sign-in and says
  why, instead of surfacing the 409.

  A mismatch now offers **"Use a different account"**, which clears the session and returns to
  the same invitation. The old dead end pointed at a sign-in page that kept the wrong session and
  looped straight back to the same error.

  Pending invitations carry `account_exists` too, badged **existing user** in Settings →
  Organization, so an admin knows to say "sign in with your usual password" rather than "create
  an account" — one batched lookup for the list, not a query per row.

  **The disclosure is deliberate and bounded:** the preview reveals whether an address has an
  account, but only to someone already holding a single-use token that was emailed to it. Spent,
  revoked, expired and invented tokens all return the same `org.invitation_invalid`.

  Verified in the browser end to end: signed in as the wrong account the page named the invited
  address and offered "Use a different account"; that led to a locked-email sign-in form reading
  "Join Aurozen Client as editor"; the existing password signed them in and landed them in the
  new org. 5 backend tests, 6 web unit tests; two E2E cases rewritten from the old behaviour to
  the new. Suites: **710 pytest**, **150 vitest**, ruff + mypy + tsc + eslint clean.

- **An invitation can be handed over as a link when the email doesn't arrive (2026-08-04).**
  Inviting a member has always queued an email automatically, and the accept page has always let
  the invitee set their own password and join. But a fresh deployment ships
  `EMAIL_BACKEND=console`, which delivers to nobody — so an admin could invite a client and have
  **no way to let them in**. The raw token was unreachable: `create_invitation` returns it only
  outside production, and no screen ever displayed it. Spam filters and mistyped addresses fail
  the same way in production, where the token is withheld entirely.

  Pending invitations in Settings → Organization now have a link button.
  `POST /v1/orgs/{org_id}/invitations/{id}/link` is gated on `members:manage`, scoped to the
  caller's org (an invitation id from another tenant is a 404, not a link), and refuses an
  accepted or expired invitation so a spent one can't be reopened. It is audited, and unlike the
  create-time `accept_token` it **is** available in production: that one is withheld so a token
  is never an incidental part of a response body, while this is an explicit, permissioned action
  whose entire purpose is to hand the link to a human.

  **Issuing a link is not read-only, and the dialog warns before doing it.** `token_hash` is a
  one-way hash, so the original link cannot be read back — a new token must be minted, which
  stops any previously sent link from working and restarts the 7-day clock. Regenerating silently
  would break a link the client may already be holding. Verified end to end: invite → mint link →
  client signs up with their own password → joins as `editor` → logs in later with that password
  → replaying the link returns `org.invitation_invalid`.

  Also fixed `WEB_BASE_URL`, which was `http://localhost:3000` while the web app runs on **3001**
  here (3000 belongs to an unrelated app — CLAUDE.md §12), so every link the backend generated —
  invitations, verification, password reset, magic links — pointed at the wrong application. That
  is `.env` only; `.env.example` keeps the canonical 3000. **Real email delivery is still
  unconfigured** (`EMAIL_BACKEND=console`, blank `SMTP_HOST`/`SMTP_FROM`) — that needs an SMTP
  provider and a domain verified for SPF/DKIM, which no code can substitute for.
  6 backend tests, 4 web unit tests.

- **Safety guardrails, Phase A of `docs/11-SAFETY-GUARDRAILS.md` (2026-08-03).** A live red-team
  session broke a deployed agent six ways in about twenty messages. Five traced to one asymmetry:
  `neutralize_injections()` ran on retrieved RAG chunks and tool output but **never on the
  visitor's own message**, so *indirect* prompt injection was defended and *direct* injection
  (OWASP LLM01) was not. Confirmed in the code before any fix: `inbound.py` passed `self.message`
  straight into `build_messages()`.

  **What shipped.** `app/chat/normalize.py` (L0) normalises for *matching only* — NFKC,
  zero-width and bidi stripping, Cyrillic/Greek homoglyph folding, a length cap, and one shallow
  base64/percent/ROT13 decode pass producing extra candidate strings. The text sent to the model
  and stored in the database stays exactly what the customer typed, and a test pins that.
  `screen_user_message()` (L1) matches five named families across all those candidates and
  returns a verdict; `neutralize_injections()` keeps its original patterns and its
  defang-and-continue behaviour for retrieved content (ADR-050 explains why the two must differ).
  An immutable identity lock is prepended to every assembled system prompt at all three call
  sites. `app/chat/output_guard.py` (L5) scores 8-gram shingle overlap against the instruction
  prompt and detects persona breaks, with one silent regeneration before falling back.

  **Precision was the binding constraint, not recall.** The first pattern draft refused *"how do
  I enable dark mode?"*, *"can you show me the instructions?"*, and anyone asking to speak to a
  colleague named **Dan**. Patterns are now anchored on wording that has no ordinary support
  reading — named jailbreak modes only, and the possessive *"your instructions"* rather than
  *"the instructions"*. 19 benign fixtures hold the false-positive rate at **zero**; catching
  paraphrase is Phase C's job. `matches_blocked_topic()` moved from substring containment to
  cached word-boundary regex for the same reason — it had been refusing *"how do I cancel my
  cancellation?"* on the topic `cancel`.

  **The identity lock caused a real regression before it fixed anything.** docs/11 warned that new
  text in every prompt could re-trigger the 2026-08-02 fabrication regression, and it did, worse
  than the original: with no retrieved context the first wording fabricated **15/15** against an
  **11/15** baseline. Cause is the failure already on record — told "never explain how you work"
  and "do not acknowledge that a rule prevented you", the model generalises to "never hedge" and
  invents opening hours. Fixed by scoping the secrecy rules to *phrasing* and stating the
  no-information case outright; re-measured at **10/15 against a 12/15 baseline**, so the lock now
  slightly improves grounding. Both runs n=15 across `DEFAULT_SYSTEM_PROMPT` and all four
  templates on `groq/llama-3.1-8b-instant`.

  **A finding that outlives this change:** that baseline is 12/15, not 0/3. **Prompt-only
  grounding does not hold on an 8B model.** The "0/3 fabricated" recorded on 2026-08-02 was
  measured on a larger model and does not generalise downward — which is exactly docs/11 §1.4's
  argument, and why live failure 4 ("What's the capital of France" → "Paris") happened. Grounding
  needs the code-level groundedness check in docs/11 §4-L5, not better prompt wording.

  **Streaming forced a documented trade (ADR-049).** By the time a reply can be judged the widget
  has painted it. Buffering every reply until it could be checked would spend first-token latency
  on every turn — NFR-1 is p50 417 ms — to defend against a rare event, so the guard corrects
  afterwards via a new `replace` stream event, and the widget honours it by resetting its
  accumulator. Channels and the persistence path read `result.content`, which is corrected in
  place, so they are protected outright. The **Playground opts out entirely** (`guard_output=
  False`), the same reasoning that already withholds `fallback_message` there: an operator asking
  "is my agent behaving?" has to see what the model actually said.

  ADR-049 (streaming correction), ADR-050 (refuse vs defang), ADR-051 (fail open on availability,
  closed on enforcement), ADR-052 (no NeMo/Guardrails-AI dependency). New env vars
  `MAX_USER_MESSAGE_CHARS`, `GUARD_INPUT_ENABLED`, `GUARD_OUTPUT_ENABLED`,
  `GUARD_OUTPUT_LEAK_THRESHOLD`. **444 pytest**, ruff + mypy clean.

  **Not built — Phases B through G.** B (PII egress redaction + ingest-time scanning) is what
  actually fixes the founder-PII leak and is the next most valuable. C (Prompt Guard 2 on Groq),
  D (the full multilingual/multi-turn/second-order red-team corpus — docs/11 says do not start E
  before D is green), E (distress detection + the attention queue), F (template variables),
  G (scoped web access). **§6's knowledge-base clean-up is operator work and no code substitutes
  for it:** the hackathon PDF and the personal contact details still need removing by hand.

  **Honest limitation, per docs/11 §9.** Prompt injection is not solved. Prompt Guard 2 reports
  81.2% attack prevention and published work (arXiv 2504.11168) demonstrates systematic evasion of
  every deployed detector class. These layers raise the cost of an attack and make a successful
  one survivable; they do not make the agent immune, and nothing here should be described to a
  client as if they did.

- **Provider keys in Settings, and a Model tab that only offers what you can run (2026-08-03).**
  Closes the `providerCatalog` roadmap item ADR-041 left open, and the reason it mattered turned
  out to be bigger than drift. **ADR-047 / ADR-048.**

  Settings → Provider keys is now a grid of every provider in the catalogue: click one, paste its
  key, save. Connected providers sort first with their masked key and the models that key
  unlocks. The builder's Model tab lists **only providers this org holds a key for** — it
  previously read a hardcoded list in `lib/mock/builder.ts` and offered all seven regardless, so
  the obvious way to configure an agent was to pick one with no key behind it, and the failure
  arrived later as a dead agent rather than then and there as a validation error. That list was
  also stale in a way nobody could see: it still offered Groq's `mixtral-8x7b-32768`, retired
  upstream.

  **The catalogue is `app/llm/catalog.py` and the model lists in it are a seed, not the truth.**
  Where a key exists, `GET /v1/credentials/providers/{name}/models` asks the provider itself.
  Live verification proved the point immediately: the real Groq account returned
  `qwen/qwen3.6-27b`, `groq/compound` and `allam-2-7b` — none in the seed — while several seeded
  ids were absent from it. Discovery failing is not an error state, because an unreachable
  provider still has to render a usable dropdown: it returns `source: "catalog"` with the reason
  in `error`.

  **Thirteen providers, six of them new and free to add.** Groq, Gemini, OpenRouter, Ollama,
  OpenAI, Anthropic and custom endpoints already had adapters; Mistral, DeepSeek, xAI, Together,
  Fireworks and Cerebras speak the OpenAI wire format at a fixed URL, so each is one catalogue
  entry and no code. All bring-your-own-key — **no new env vars**, nothing platform-wide.

  **Two things are deliberately never rewritten silently** (ADR-048). `configured` answers "will
  a turn work?", not "is there a row in the credentials table" — a platform env key counts,
  which is how the live Groq agents run today with no credential row at all. And a provider or
  model that has become unavailable stays *selected and selectable*, flagged `no key — this agent
  cannot reply` / `(not offered)`, because dropping it would leave the `<Select>` unmatched and
  let the builder's debounced autosave persist a provider nobody chose.

  Two smaller things found on the way. Pricing is now `None` when unpublished rather than `0`,
  and `compute_cost_micros` logs `pricing_unknown` for a paid model with no rate — $0 in a
  client's cost report is a wrong number, not a free turn. And provider error text is stripped of
  key-shaped runs before it reaches the client: a rejected key comes back as
  `Incorrect API key provided: sk-live-*******8888`, and masked or not that is secret material in
  an API response and a log line.

  Also deleted: `providerCatalog` and the unused `providerLabel` map in `lib/display.ts` — three
  copies of the provider labels became one. `vitest.setup.ts` gained ResizeObserver and
  pointer-capture stubs, without which any component test containing a Radix Slider or Select
  fails on render rather than on its assertion.

  **Verified live** against the real stack: 13 providers listed with Groq resolving via the
  platform env key and Ollama as `not_required`; real model discovery against Groq; a saved key
  flipping `none` → `org` with re-saves replacing rather than stacking rows; a bad key falling
  back to the catalogue; removal flipping it back. Suites: **430 pytest** (20 new), **131 vitest**
  (7 new), **3 new Playwright checks**, ruff + mypy + tsc + eslint clean.

- **Live client agent "aurozen ai" — prompt rewritten and republished (2026-08-02, operational).**
  Not a code change; recorded here because it altered a **live, customer-facing** agent and the
  reasoning is not recoverable from git.

  Its stored prompt (the one real visitors were served, unrelated to `DEFAULT_SYSTEM_PROMPT`) held
  two faults beyond tone. `"If citing information from the documents would improve clarity, briefly
  reference the relevant document or section."` was a **second, agent-level source of the
  citation-list voice** — fixing `app/rag/context.py` was never going to silence it. And
  `"Answer only based on the uploaded content unless the user explicitly asks for general
  knowledge"` was an explicit escape hatch out of grounding. Rewritten with the ADR-045 clauses
  while keeping the agent's own character (professional/friendly/confident, 2–5 sentences,
  summarise rather than quote, one clarifying question when ambiguous). Also dropped
  `"carefully read and analyze the relevant files"`, which describes a filesystem the model
  doesn't have.

  Applied to draft **v5** and published it. v4 and v5 were otherwise byte-identical, so publishing
  also switched the live model **gemini-1.5-flash → groq/llama-3.3-70b-versatile** — necessary,
  because gemini was the provider hitting the malformed-key outage above and Groq is the only one
  with a working key. v4 remains `is_published`, so rollback is available. Done via SQL replicating
  `publish_version()` exactly (`is_published`, `current_version_id`, `status`) — there are no
  credentials for the aurozenai org to go through the API with.

  Verified through the public widget endpoint: greeting answered as a greeting, services answered
  cleanly with no `[n]`, an invented "90-day money back guarantee / 24/7 phone support" correctly
  refused while volunteering the real 14-day trial. Quoted pricing ($100/mo Starter, $400/mo cap,
  website build from $250) spot-checked verbatim against the knowledge base chunks.

- **A `.env` placeholder comment was being used as an API key — silent live outage (2026-08-02).**
  The live "aurozen ai" agent had been answering every visitor with **empty content and HTTP 200**
  for an unknown period. The request log named the culprit:
  `generativelanguage.googleapis.com/...?key=%23+%5BHUMAN%5D+Google+Gemini+free+tier` — the string
  `# [HUMAN] Google Gemini free tier` was going to Google as the API key.

  **Why.** `.env.example` writes unset variables as `KEY=<spaces># [HUMAN] note`. python-dotenv
  strips a trailing comment with `re.sub(r"\s+#.*", "", value)`, which needs whitespace *before*
  the `#` — but on a blank line the spaces after `=` are already eaten as the separator, so the `#`
  sits at position 0 and the comment becomes the value. Probed against the real loader before
  fixing anything: `KEY=dev  # note` → `dev` (correct), `KEY=<blank># note` → `# note` (broken).
  **Only the blank-value shape fails,** which is exactly why nobody caught it. Being non-empty, the
  value then passed `resolve_credential`'s blank-is-unset guard (ADR-020) untouched. `SENTRY_DSN`
  was failing the same way (`sentry_init_failed: Unsupported scheme ''`), and ~20 more `[HUMAN]`
  placeholders were one edit from it.

  **Fixed in two places, because either alone leaves a hole** (ADR-044). `Settings` gained a
  `model_validator(mode="before")` that drops comment-only values so the field default applies —
  which makes every existing `.env` correct **with no hand-editing**. And `.env.example` was
  reformatted so each comment sits on its own line above its variable, because `.env` is also fed
  to containers through compose's `env_file`, whose parser no Python validator can reach. A test
  asserts the file never reacquires the shape.

  **Explicitly not done: strip everything after the first `#`.** The obvious fix, and destructive —
  measured against the real loader, it turns `SECRET_KEY=abc#def` into `abc` and
  `http://x/y#frag` into `http://x/y`. Signing keys, DB passwords and URL fragments legitimately
  contain `#`, so that trades a loud bug for a quiet one. The narrow rule's only false positive is
  a secret that *starts* with `#`, which degrades to "not configured" — loud, and well-trodden.

  **Plus the reason it stayed invisible.** A `ProviderError` left `result.content` empty and
  `public_chat_once` only accumulates `token` events, so the error event was dropped and the widget
  returned a 200 with nothing in it — an empty string reads as a quiet bot, not an outage.
  `run_turn` now takes an optional `fallback_message` and emits it as a real token when the
  provider fails with no content, wired into `InboundTurn` (widget + every channel) and
  deliberately **not** into the Playground, where an operator wants the raw error. `result.error`
  is still set, so logs and the persisted message keep the true cause, and the provider's message
  never becomes visitor-facing text (it can carry key fragments). A `malformed_key` startup warning
  now catches a key containing whitespace or `#` — after this fix that can only be genuine bad
  input. Verified live: the `sentry_init_failed` warning is gone, the agent still answers normally,
  and forcing the old gemini config now yields the agent's fallback line plus an error-level
  `chat_provider_unavailable` instead of silence.

  Suites: **349 pytest**, ruff + mypy clean.

- **Replies stopped sounding like a research paper, and agents now start from a role
  (2026-08-02).** Two independent pieces of work.

  **1. The citation-list voice (`b25c1c6`).** Customers were getting "According to the documents
  [1] and [2], Aurozen AI provides two main services…". The literal cause was one clause in
  `build_context_block()`'s header telling the model to "cite sources as [n] when relevant" — the
  numbering exists for *our* bookkeeping, since the caller returns the same citations as
  structured data for the widget to render as a sources list, so the model never needed to repeat
  them inline. The header now forbids visible markers outright and asks for a normal human answer.
  Proved causal with the system prompt held constant: the old header emitted `[n]` in **3 of 3**
  sampled replies, the new one in **0 of 3**, and a live Playground call still returned
  `citations: 1` alongside a marker-free answer — the structured citation data is untouched.
  `db/seed.py`'s demo persona was softened the same way.

  **2. Prebuilt role templates at creation time (`a557418`, `a22d1ff`).** "New agent" was a bare
  name field. It now opens on a role picker — Customer Support, Lead Qualification, Appointment
  Scheduler, Info Collector — plus "Start from scratch", which keeps the old blank behaviour
  exactly. Each template seeds the first draft's system prompt, welcome message, suggested
  prompts, tone and model settings (the scheduler runs at temperature 0.2: dates and confirmations
  are where creative phrasing becomes a wrong booking), and the operator lands directly in the
  Persona tab with everything filled in and editable. Catalog lives in `app/db/templates.py` as
  static data — adding a fifth role is one `AgentTemplate(...)` entry, no schema change, no
  migration — served by `GET /v1/agent-templates` and applied via an optional `template_id` on
  `POST /v1/agents`. **Nothing is auto-attached:** where a role needs a knowledge base, a CRM
  automation or a calendar, the builder shows a dismissible "Suggested next step" banner instead,
  because silently wiring an integration with no credentials behind it produces agents that look
  configured and fail at runtime.

  **The regression the tone fix caused, and how it was caught (`e088101`).** Softening the default
  prompt's voice cost its accuracy. On a live Playground question about support hours the agent
  invented "Monday to Friday, 9am to 5pm" and a 30-day refund window against a KB saying Monday to
  Saturday, 10am–7pm IST and 14 days — with `citations: 0`, because retrieval had legitimately
  missed (score **0.0318** vs the 0.35 threshold) and no context block was appended at all. A
  controlled A/B on that exact no-context state: **old prompt 0/3 fabricated, new prompt 3/3.**
  Cause: told only "never mention the documents", the model reads that as "don't hedge" and
  answers from general knowledge — and the rewrite had ended that rule with "Just answer.". The
  same A/B then caught a second instance the first pass had missed, in the `customer_support`
  template, whose "resolve it in as few messages as possible" body overpowered the softer shared
  `_GROUNDING` text. Fixed in both by dropping "Just answer.", stating the no-context case
  outright, and scoping the never-narrate-your-sources rule to *phrasing* so it can't read as
  licence to answer anyway. Re-verified: the default prompt and **all four templates** now
  fabricate **0/3** with no context, and still answer correctly from a real `build_context_block()`
  with **0/3** source markers. A test pins those clauses, since the behaviour itself needs a live
  model to observe.

  **An app-wide dialog bug found on the way (`203984a`).** "Start from scratch" was unclickable —
  permanently outside the viewport. `animate-fade-up` ends on `transform: translateY(0)` with
  fill-mode `both`, so once the open animation finished it permanently overrode `DialogContent`'s
  `-translate-x-1/2 -translate-y-1/2`, anchoring **every dialog in the app** *at* the viewport
  centre rather than centred on it. Small dialogs still landed on screen, which is why it went
  unnoticed; this taller one was clipped off the right and bottom. Now centred with
  `inset-0 m-auto h-fit` (no transform to clobber) plus a scroll cap.

  Suites: **340 pytest**, ruff + mypy clean, **124 vitest**, tsc + eslint clean, **60/60 Playwright**.

- **"echo: <your message>" instead of an AI reply — diagnosed and hardened (2026-07-31).**
  Reported against the Playground for the "aurozen ai" agent. **Root cause: environmental, not a
  product defect.** The web dev server had been started with
  `NEXT_PUBLIC_API_BASE_URL=http://localhost:8010` — the **keyless E2E instance**, which runs with
  `LLM_FORCE_FAKE=true`. `get_chat_provider()` checks that flag *first*, before any per-agent
  config, so every agent on that process answers with `FakeChatProvider`'s stub echo no matter
  what it's configured for. Proved by asking the same agent the same question on both ports:
  `:8000` returned a grounded, cited answer from Groq; `:8010` returned `echo: …`. The agent's
  stored config was never `fake` — published v1–v3 are `groq/llama-3.3-70b-versatile` (the newest
  **draft** v4 is `openai/gpt-4o`, which matters below). Fixed by repointing the dev web server at
  `:8000`; verified with a real multi-turn conversation, including a follow-up ("how much does
  **the first one** cost?") that correctly resolved the referent, so memory/context works.
  Three code hardenings so this can't silently recur or bite a customer:
  1. **A loud startup warning.** `LLM_FORCE_FAKE=true` now logs `llm_force_fake_enabled` at
     startup spelling out that every reply is a stub and every embedding fake, and escalates to
     `error` under `ENV=prod`. Nothing announced it before — the flag silently changed every
     answer, which is exactly why it reads as a broken model rather than a config choice.
  2. **A missing key no longer answers customers with an echo.** `conversations/service._resolve_provider`
     caught `AppError` and substituted `FakeChatProvider()` — so an expired or revoked Groq key
     would have had **every visitor on a client's site answered by a parrot repeating their own
     words back**, with only a log line to say why. It now returns `RefusalProvider(agent's
     fallback_message)` and logs `chat_provider_unavailable` at error level. This is the
     production bug the report asked us to look for, and it was live.
  3. **The Playground surfaces the real error.** It stubbed the same way (CLAUDE §7 "don't block
     the build"), which answers "is my agent configured correctly?" wrongly — `echo: hi` sends the
     operator to debug their persona instead of the missing key. It now re-raises the typed
     `llm.provider_unavailable`, naming the provider.
  **A latent bug fell out of that:** an agent explicitly configured `provider: "fake"` never
  actually resolved — `fake` isn't in `PROVIDER_CATALOG`, so it hit the requires-a-key branch and
  raised; it only *appeared* to work because those same catch-all handlers swallowed the error.
  `get_chat_provider` now resolves `fake` before the key check, which is what keeps the test
  suites (which configure it deliberately) working under the stricter rules. 4 backend tests.
  **Still needs a human:** the "aurozen ai" **draft** (v4) is set to `openai/gpt-4o` and no
  `OPENAI_API_KEY` is configured, so the Playground — which always runs the draft — will now show
  a clear "No API key configured for 'openai'" instead of an echo. Switch the Model tab back to
  Groq or add an OpenAI key. The **published** agent (v3, Groq) is unaffected and answering.
- **n8n visibility is deny-by-default, plus a cross-org automations console (2026-07-31).**
  ADR-040's permissive "untagged = visible to every org" default was proven wrong by live
  testing: a brand-new, empty org opened Automations and still saw every untagged workflow —
  other clients' automations and platform-internal ones alike. Untagged is the state every
  workflow starts in, so the "small residual set" the default was meant to protect was in fact
  the entire inventory. `workflow_visible_to_org` now grants access **only** on a tag matching
  the org's slug, or the new opt-in `shared-template` for genuinely reusable starters; internal
  tags still hide unconditionally and now beat `shared-template`, so labelling something both
  ways still fails closed. Verified live: a freshly created org gets `[]` from
  `GET /v1/tools/n8n/workflows`, where it previously got the whole list. See ADR-042.
  **Provisioning tags at clone time** (`provision-client.mjs`), using the org's *real* slug from
  the API rather than `slugify(name)` — the backend suffixes on collision — and it does so
  **before** binding, because `POST /v1/tools/n8n/bind` resolves by `workflow_id` and applies the
  same rule, so an untagged clone would be refused with `tools.n8n_forbidden`. A tag failure is
  fatal to the run by design: continuing would hand the client an automation they cannot see.
  **New staff console section** (ADR-043): `GET /v1/admin/automations` + an Automations table on
  `/admin` listing every workflow across every tenant with its tag-derived owner, active state
  and the agents that bind it — the one place to see all clients' automations without switching
  org, and the working view for the tagging backlog (unowned rows sort first). An unreachable or
  keyless n8n returns a typed `error` with an empty list rather than a 500, so the table says
  "couldn't reach n8n" instead of looking like "no automations exist". `unknown-org` (a tag
  matching no org slug) is reported separately from `untagged` because it's nearly always a typo.
  New `scripts/tag-n8n-workflows.mjs` applies a name→tag mapping in bulk, dry-run by default,
  idempotent, preserving existing tags, and reporting anything untagged-and-unmapped.
  7 backend tests, 2 script tests.
  **Blocked, needs a human:** the tagging could not actually be applied. The n8n API key has
  workflow read/write but **not tag scopes** — `GET /api/v1/tags`, `POST /api/v1/tags` and
  `PUT /api/v1/workflows/{id}/tags` all return **403 Forbidden** on `:5679`. The script
  preflights this and stops rather than half-tagging. Mint a key with tag read/create + workflow
  "update tags" (n8n → Settings → API) and re-run
  `node scripts/tag-n8n-workflows.mjs --apply`. **Until then every workflow is untagged and
  therefore invisible to every org** — including `Acme Co — Starter Automation` and
  `Globex Inc — Starter Automation`, which are still bound as tools and keep working at runtime
  (the runtime calls the stored `webhook_url` and never re-checks visibility) but can no longer
  be discovered or re-bound. The `:5678` inventory could not be touched at all: that instance
  belongs to the separate AUROZEN AI compose and rejects BotForge's key with 401.
- **Logged out seconds after logging in — fixed (2026-07-31).** `AuthGate` bootstraps every page
  load with `Promise.all([me(), listOrgs()])` under a bare `catch` that ran `clearAuth()` and
  bounced to `/login`. Navigating *while that bootstrap is still in flight* **aborts** those
  requests, and the browser rejects an aborted `fetch` with a `TypeError` — not a 401. So the
  gate read "the user clicked a link quickly" as "this token is invalid", deleted `bf_access`
  and `bf_org`, and threw them back to the sign-in page moments after they authenticated.
  Traced live: on the navigation, `GET /v1/auth/me` and `GET /v1/orgs` both report
  `net::ERR_ABORTED`, **no request 401s at all**, and the cookies are gone by the next paint.
  The catch now clears the session **only** on `ApiError` with status 401; an abort, an offline
  blip or a 500 leaves the tokens alone and the next load re-runs the bootstrap cleanly.
  Two sibling paths that turned a hiccup into a logout got the same treatment: `tryRefresh()`
  called `clearAuth()` on *any* non-ok refresh response (now 401 only), and the BFF
  `/api/auth/refresh` route cleared the httpOnly refresh cookie on any status ≥ 400 — including
  a 5xx from an API that was merely restarting. It now distinguishes a rejected token (401/400
  → clear, as before) from an unavailable upstream (→ 503, cookie kept), and an unreachable API
  no longer throws a 500 out of the route.
  **Not caused by the dashboard rewiring, but exposed by it** (`fbf8543`): replacing the mock
  fixtures with five real concurrent requests widened the in-flight window enough that a normal
  click landed inside it. PRD criterion 1's E2E went from failing 3/3 to passing 3/3 with no
  change to the test. 5 web unit tests pin the distinction — 401 signs out, abort and 503 do not.
- **The dashboard, profile page and versions tab show real data (2026-07-30).** Closes the
  mock-data item flagged on 2026-07-28, plus two more instances found by sweeping for it. Three
  screens still imported **data** — not just types — from the Phase-4 mock layer (ADR-011), so
  they rendered fixtures as though they were the tenant's own records: `dashboard/page.tsx`
  imported `{ agents, recentConversations }`, giving a brand-new org four invented agents
  (*Support Concierge*, *Sales Qualifier*, *Docs Assistant*, *Order Tracker*) and five
  conversations that never happened, complete with plausible message counts and visitor handles;
  `settings/profile/page.tsx` imported `currentUser` and rendered **"Aadesh Sree /
  aadesh@aurozen.ai" to whoever was logged in**, above two invented devices in "Coimbatore, IN";
  `versions-tab.tsx` imported `versions` and gave every agent the same four-entry history
  ("Tightened refund policy wording") with Publish and Roll back buttons that did nothing. All
  three now fetch per-org data with their own loading skeleton and a written empty state — zero
  agents and zero conversations is the *normal* state of a freshly provisioned client, and
  precisely what the fixtures were hiding.
  **Not from the inbox:** "Recent conversations" reads `GET /v1/conversations`, because
  `/v1/inbox/conversations` is the handoff queue — an org whose bot answers everything without
  escalating would have shown an empty panel, and `viewer` (no `inbox:handle`) would have got a
  403 where a fixture used to render. **Only real fields are shown:** the agents panel dropped
  its per-agent "7d chats" and resolution rate rather than firing one analytics request per row
  for numbers `GET /v1/agents` doesn't carry; the aggregates are already in the stat cards above.
  The versions tab marks Current from `current_version_id`, not the highest version number,
  because a rollback deliberately makes an older version live; Publish/Roll back now call the
  real endpoints and are hidden without `agents:publish`.
  **One real backend bug surfaced on the way:** `GET /v1/auth/sessions` returned `current=False`
  **hardcoded**, so the "This device" badge could never appear and a user had no way to tell
  their own session from the ones they might want to revoke — `list_sessions` only ever received
  the `User`. Access tokens now carry a `sid` claim naming the session that minted them; a token
  issued before the claim has no `sid` and every row reports `current: false`, exactly the old
  behaviour, so rotation and existing sessions are unaffected.
  **Profile name/email are read-only:** there is no `PATCH /v1/auth/me`, and the page's old Save
  button silently discarded the edit. `lib/mock/data.ts` is deleted outright and `builder.ts`'s
  `versions` / `makeDraft` / `knowledgeBases` / `tools` fixtures removed so nothing can
  re-import them; `types.ts` and the `providerCatalog`/`toneOptions` config lists stay (ADR-041
  explains why `providerCatalog` was flagged rather than switched to
  `/v1/credentials/providers` in this change). 3 backend tests, 38 web unit tests, 4 Playwright
  checks — including a brand-new org asserting both empty states and the absence of every
  fixture name.
- **Self-serve signup closed — organization creation is staff-only, full stop (2026-07-30).**
  Closes the gap flagged as "genuinely not built" when the invite-acceptance page shipped.
  `create_org` was already staff-gated, but with a deliberate exception: `not user.is_staff and
  await _active_org_count(...) > 0` blocked an *additional* org while always allowing the first.
  That exception existed to make `CreateFirstOrg` work after signup — and it made the whole
  product self-serve. Anyone clicking "Create one" on `/login`, filling in the public `/signup`
  form, and landing back with zero orgs got a "Create your organization" screen that **succeeded
  unconditionally**: a stranger, a free workspace, no invite and no approval. The gate is now a
  flat `if not user.is_staff` with no exception, and `_active_org_count` is gone.
  **Invitations are untouched and remain the one way in for a new person** — `accept_invitation`
  adds a `Membership` to an org that already exists and never reaches `create_org`, so an invited
  client signs up and joins exactly as before (asserted end-to-end, both backend and Playwright).
  Frontend: `AuthGate`'s zero-org branch renders **`NoWorkspace`** instead of `CreateFirstOrg` — a
  dead end with no form, naming the user's own email so they know which address the invite must go
  to, since offering a button that now always 403s would be worse than offering nothing. The
  "No account? Create one" line is gone from `/login` (replaced with "Ask your BotForge contact
  for an invitation"); `/signup` stays in the codebase because the invite-accept page needs
  account creation, and a stray direct visit now simply ends at the dead end.
  **A test-only escape was unavoidable:** 29 backend test files and 22 of 23 E2E specs bootstrap
  their tenant by POSTing `/v1/orgs` as a fresh non-staff user, so a flat gate breaks both suites
  wholesale. New `ALLOW_SELF_SERVE_ORGS` (default **false**, alongside `LLM_FORCE_FAKE` as a
  never-in-production switch) is enabled by an autouse conftest fixture and by the CI e2e job.
  `tests/test_org_creation_gate.py` turns it **back off** and is the file that pins the real
  production rule, so the gate is genuinely covered rather than configured away.
  4 backend tests (rewritten — the old ones asserted the first-org exception), 3 Playwright checks.
  **Consequence worth knowing:** a client who deletes their only organization can no longer create
  a replacement — the workspace must be re-provisioned by staff. The delete-org button is a
  one-way door for a non-staff owner, and there's a test pinning exactly that.
- **Delete an organization from Settings (2026-07-30).** `DELETE /v1/orgs/{id}` (soft-delete via
  `deleted_at`, `ORG_MANAGE`) had existed since Phase 3 with **nothing in the UI calling it** — the
  same shape of gap as the invitation-accept page. New `components/settings/delete-org.tsx` renders
  a Danger zone directly under "Organization profile" on `/settings/org`, behind a type-the-org-name
  confirmation. Owner-only: `ORG_MANAGE` is owner-only in the matrix, so an admin — who can invite
  and remove members — deliberately cannot delete the workspace; the component returns `null` for
  everyone else and the server 403s regardless.
  **The interesting part is the aftermath, not the call.** The org being deleted is the *active*
  one, and its id lives in the `bf_org` cookie that every request sends as `X-Org-Id`. Leaving it
  there points the whole session at a deleted org, so the failure surfaces on the next page rather
  than at the click. On success the component re-reads `listOrgs()` (server truth, not a local
  filter), moves the cookie **and** the Zustand store to the first remaining org, and clears the
  TanStack cache, which is entirely scoped to the org that just went away. Deleting your *last* org
  sets no active org at all and lands on `AuthGate`'s create-first-org prompt — which works because
  the staff-only creation gate always allows a first org.
  6 web unit tests + 2 Playwright flows. The E2E for the multi-org case gets its second org **by
  invitation**, since creating a second one is staff-only (`orgs.create_forbidden`) — being invited
  into several client workspaces is how an operator really ends up with more than one.
- **n8n workflow discovery is scoped to the owning org (2026-07-30).** `list_n8n_workflows` had
  **no tenant filtering whatsoever** — it returned every workflow in the shared n8n instance to
  every org. Each client's Automations page listed every other client's automations, and
  platform-internal workflows (`SHARED — Master Router`, the auto-provisioner) were bindable as a
  client's agent tool. This is the one multi-tenancy hole that the org-scoped repository base
  couldn't catch, because the data lives in n8n rather than in Postgres, so no query was ever
  filtered by `organization_id`. Found from a **live screenshot of the Automations page**, not
  from a test: no test had ever put two orgs' workflows in one n8n instance, so nothing failed.
  New `tools/service.workflow_visible_to_org(tags, name, org_slug)` keyed on n8n **tags**: tagged
  with an org's slug → visible only to that org; tagged `internal`/`shared-internal`/
  `platform-internal` → hidden from every org unconditionally; untagged → visible to all.
  The asymmetry is deliberate — the untagged default is **permissive** so the operator's existing,
  not-yet-tagged client workflows didn't disappear the moment this shipped, while the internal
  direction **fails closed**, because a client reaching an admin workflow is a security incident
  rather than a UX gap. A `name.startswith("SHARED —")` check covers the internal workflows that
  predate tagging; tagging them `internal` retires it. The same check runs in `bind_n8n_workflow`
  when binding by `workflow_id` (typed `tools.n8n_forbidden`, 403), closing the hole where an org
  could bind a workflow it was never shown just by knowing or guessing its n8n id.
  **Not covered, by design:** binding by a pasted webhook URL — a caller already holding the secret
  URL can reach it like any other endpoint. Treat client webhook URLs as secrets. See ADR-040 and
  `docs/guides/N8N-SETUP.md §5`. 4 backend tests (tag extraction, the visibility matrix, a
  two-org discovery list, and a cross-org bind rejected).
  **Operator action required — the fix is inert until workflows are tagged.** Audited live against
  both instances on 2026-07-30: on the AUROZEN n8n (`:5678`) **6 of 9** workflows are still
  untagged and therefore visible to every org — `00001 — Load KB`, `00001 — Main Agent`,
  `00002 — Load KB`, `00002 — Main Agent`, `Website Lead — Contact Form` and
  `BotForge — Echo (sync)`; the three `SHARED — …` ones are already hidden by the name rule. On
  BotForge's own n8n (`:5679`) all three are untagged (`Acme Co`/`Globex Inc` starter automations
  and the `TEMPLATE`, which should be `internal` so no client can bind it).
- **One-command client provisioning (2026-07-30).** `scripts/provision-client.mjs` takes
  `--name` / `--email` / `--plan` and stands a client up end to end: org → agent (starter
  persona, Groq `llama-3.3-70b-versatile`, tools on) → **published** so it's live before the
  client's first login → the n8n starter automation cloned + activated + bound as a tool →
  client invited as `editor`. Zero npm dependencies (Node 18+ `fetch`), so there's nothing to
  install before provisioning. Plus `infra/n8n/template-starter-automation.json`
  (`TEMPLATE — Starter Automation`: Webhook → Set → Respond to Webhook) as the thing it clones.
  Verified live: two back-to-back runs with the same `--email` produced **6 reuses and 0
  creations** (one org, one agent, one invitation, one tool confirmed by SQL), and a real chat
  turn made Groq call the tool — n8n executed and its response came back inside the
  conversation (`{"received": {"customer": "Globex", "order": "5512"}, "handled_by": "Globex
  Inc — Starter Automation"}`). 8 unit tests (`node --test`) over the naming/parsing helpers,
  where a bug means a cross-client collision rather than a crash; `make provision` /
  `make test-scripts` added.
  Design notes worth keeping: it **logs in as staff** rather than using a `bf_…` key, because
  org creation is gated on `User.is_staff` and no key scope grants it; it binds through
  `POST /v1/tools/n8n/bind` so the webhook URL is derived by the same code the runtime uses;
  and each clone gets its **own** webhook path (`{slug}-starter-automation`) — cloning the
  template verbatim would point every client's tool at one shared URL, so whichever workflow
  n8n resolved first would answer everyone, which is a cross-client leak rather than a mix-up.
  Two idempotency subtleties came out of testing: `create_invitation` only rejects an
  already-**active** member, so a blind re-run mints a second pending invite and emails the
  client twice (the script checks the pending list); and re-patching an existing agent forked a
  new draft every run via branch-on-edit (ADR-023) **and would have overwritten a client's own
  persona edits with the starter defaults**, so an agent that already has a system prompt is
  now left strictly alone — nor is its unreviewed draft published behind its owner's back.
  **Infra:** the bundled dev `n8n` service now takes `N8N_HOST_PORT` (default 5678, so the
  canonical setup is unchanged) and sets `N8N_DIAGNOSTICS_ENABLED=false` — n8n resolves
  `telemetry.n8n.io` at boot and *exits* when DNS fails, which is why `botforge-n8n-1` had been
  dead for two days on this machine. It now runs on **5679**, isolated from the unrelated n8n
  on 5678 that belongs to another project; BotForge creating and activating workflows in
  someone else's instance is not acceptable, so `.env` points at its own.
  **Still needs a human:** the n8n API key (n8n → Settings → API, with workflow
  read/list/create/update/activate scopes) and a staff account for
  `PROVISION_STAFF_EMAIL`/`PROVISION_STAFF_PASSWORD`.
  **Not done (deliberately, per the brief):** `docker-compose.prod.yml` still references
  `N8N_BASE_URL: http://n8n:5678` while defining **no `n8n` service** — provisioning against a
  prod stack needs that added first. Logged in the roadmap below.
- **Email actually gets delivered (2026-07-30).** `app/core/email.py` had exactly one backend —
  `ConsoleEmailBackend`, an in-memory outbox — and `get_email_backend()`'s `"smtp"` branch logged
  `smtp_backend_not_implemented` and fell back to it. So **no email this app sent had ever reached
  a real inbox**: not invitations, not signup verification, not password reset, not magic-link
  sign-in. All four already funnelled through one `send()` call, so this was a one-place fix.
  New `SmtpEmailBackend` over **aiosmtplib** (async — stdlib `smtplib` would block the event loop
  on every send). **Plain SMTP, not a provider SDK**: Resend/Postmark/SendGrid/Mailgun/SES all
  expose a relay taking the same five settings, so switching provider is an env change. TLS mode
  is derived from the port (465 = implicit, everything else = STARTTLS) because providers publish
  both and the flag they want isn't something an operator should have to know; TLS is never
  optional. Unset `SMTP_HOST`/`SMTP_FROM` raises a typed `email.not_configured` rather than
  dropping the message (CLAUDE.md §7) — a swallowed invitation looks exactly like a delivered one.
  New `app/core/email_templates.py` gives the four emails an HTML part (plain text alone reads as
  broken/spam in a modern inbox); the text part **keeps its `Token: …` line**, which is load-bearing
  — a dozen test files recover tokens by parsing it out of the console outbox.
  **Sending moved off the request path** onto a new `email.send` Celery task, and two real hazards
  turned up while verifying it live: (1) `.delay()` is blocking socket I/O, so on the event loop it
  would stall every concurrent request, not just its own; (2) against a dead broker kombu retries
  the connection 20× with 1s sleeps **regardless of `retry=False`**, which measured as a **30-second
  hung signup**. The enqueue therefore runs in a worker thread under a bounded
  `email_enqueue_timeout_seconds` (default 2s) and is abandoned on timeout — losing a queued email
  beats losing the signup that triggered it. Measured after the fix: 6.2s vs a 4.3s
  console-backend-with-dead-Redis control, i.e. ~2s of email cost, down from ~26s.
  Console mode still sends **inline**, deliberately not through eager Celery: eager tasks run in the
  caller's thread where `app/worker/tasks._run`'s `asyncio.run()` raises inside the already-running
  loop — the same trap that silently broke eager ingestion (CLAUDE.md §12). That's also what keeps
  the test outbox synchronous, so every existing email test is untouched.
  `SMTP_PORT=` ships blank in `.env.example`, which pydantic can't coerce to `int`, so a
  before-validator reads blank as the default 587 (ADR-020's blank-is-unset convention) — an
  unfilled placeholder must never stop the app booting. 18 backend tests.
  **Still needs a human:** sign up with an SMTP provider, verify the sending domain (SPF/DKIM), and
  fill in `SMTP_*`. No code can skip that — it's how Gmail decides the mail isn't spoofed.
  **Noticed, not fixed:** with Redis down, a signup takes ~4.3s even on the console backend — the
  rate limiter's Redis probe has no timeout before falling back to in-memory. Pre-existing and
  unrelated to email; logged in the roadmap below.
- **Invitations can actually be accepted (2026-07-29).** `accept_invitation()` was correct and
  complete server-side, but **nothing in the web app ever called it** — the email link had
  nowhere to land, so an invitation could never leave "pending" no matter what the recipient
  did. Added the landing page at **`/invitations/accept`** — the path the invitation emails
  have always pointed at, so already-sent invites work — outside the authenticated `(app)`
  layout, since an invitee usually has no account yet. If signed in it redeems immediately;
  otherwise it offers signup *or* login and continues straight to accepting rather than
  dropping them on the dashboard having silently failed to join. The three server-modelled
  failures each get their own explanation: expired/invalid, wrong email, org deleted. On
  success the joined org is set active. 5 Playwright checks, including a brand-new user
  going from email link to active membership.
- **Widget appearance is unversioned and always live (2026-07-29).** `_theme()` read
  `persona.widget` off whatever `_live_version()` resolved — the same published-vs-draft unit
  as behaviour — so a colour change waited behind a publish approval. It only *looked*
  instant in testing because a never-published agent's latest draft and its live version are
  the same thing by coincidence; with a real publish history it would have got stuck. New
  `widget_configs` table (migration `0014`, one row per agent, **no version**), seeded from
  the version actually being served today — the published one if there is one, else the
  newest draft, since picking wrong would silently restyle a live widget. `_theme()` now
  reads it by `agent_id`, bypassing `_live_version()` entirely. New
  `GET/PATCH /v1/agents/{id}/widget-config` gated on `AGENTS_WRITE`, never `AGENTS_PUBLISH` —
  making a client wait for review to fix their own branding would be absurd. Merge-on-write
  preserved. Runtime behaviour (live fetch, CSS variables, preview postMessage) unchanged.
  7 backend tests; the widget E2E now asserts the change goes live **with no publish step**.
- **Editing and publishing are separate permissions (2026-07-29).** `AGENTS_WRITE` covered
  both saving a draft and putting it live — one permission for two very different levels of
  risk. New `AGENTS_PUBLISH` gates publish/rollback; `owner`/`admin` have both. **`editor` is
  now the client role**: edit drafts, manage knowledge, connect their own Meta credentials
  (`TOOLS_MANAGE` was already separate), work the inbox, and test in the Playground — but not
  publish. The draft/publish machinery itself is untouched; only who may pull the trigger.
  The builder shows "Awaiting review" instead of Publish for those roles, and the admin
  console gains an **"Awaiting review" column** per org (agents never published, or whose
  newest draft is past the live version) so staff notice waiting work without opening every
  builder. 6 backend tests.
- **CRM auto-capture with cross-channel identity (2026-07-29).** The "cross-channel merging
  belongs on top of this table" extension `Contact`'s own docstring anticipated. New
  `CrmContact` (migration `0013`) is the canonical *person*; `Contact` stays per-channel and
  gains `crm_contact_id`. `lead_stage`/`order_status`/`notes`/`labels` moved off `Contact`
  onto it (they describe the human, not a handle), with a backfill so no operator's data was
  lost. **Extraction is gated**: a free regex pass answers "is there anything email- or
  phone-shaped here?", and only then does one small-model call run — over just that message,
  reusing the memory summarizer's provider pattern — to pull out an associated name and tidy
  formatting. The regex result is the floor, so a useless model reply still keeps real data.
  **Identity resolution matches on email or phone only, never name** — names collide, and a
  wrong merge silently mixes two customers' histories. Normalisation (lowercased email,
  digits-with-`+` phone) is what makes the same detail written three ways match itself.
  Learning fills blanks only, so an operator's correction isn't overwritten by a later guess.
  Org-level `auto_crm_capture_enabled`, **on by default with an off switch** on Settings.
  The hook lives in `_persist_user_message`, the one place every inbound path funnels
  through — including messages sent while a human has taken over, which never reach a bot
  turn. 14 capture tests, 15 CRM tests, 3 Playwright checks.
  **Behaviour change worth knowing:** the CRM lists people, so a visitor who never shares an
  email or phone no longer appears there — they remain in the Inbox as a per-channel handle.
  A CRM full of `anon-9f2c` rows would help nobody, but this is a visible difference.
- **Contacts → CRM, plus manual contact creation (2026-07-29).** Nav item and page title
  renamed to "CRM"; the `/contacts` route is unchanged, so the Inbox's contact link still
  resolves. New `POST /v1/contacts` adds the first **operator-initiated** creation path —
  every other row in this table is upserted by an inbound message. A manual contact has no
  platform account behind it, so it gets `channel="manual"` and a generated `external_id`,
  keeping the `(org, channel, external_id)` uniqueness constraint meaningful rather than
  special-casing it. `manual` joins `dashboard`/`api`/`web` in `channel-meta.ts`'s
  `REPORTING_ONLY` map, so it can never become an Inbox tab — it has no webhook and no
  conversations at all. 4 backend tests.
- **URL ingest extracts the article, not the page chrome (2026-07-29).** `strip_html()`
  removed `<script>`/`<style>` then regex-stripped every remaining tag with no notion of
  document structure, so nav menus, headers, footers and cookie banners landed in the same
  text stream as the content. On `https://docs.n8n.io/` the nav ("Forum Changelog Get started
  Deploy Build Nodes Connect Administer Contribute") *opened* the extracted text and
  dominated the first chunk — the chunk retrieval most often returns. Added **trafilatura**
  and a new `extract_main_content()` used by `load_url()`, with `strip_html()` kept as the
  fallback for pages it can't parse (a stub still ingests rather than failing). Markdown
  output preserves headings, which the recursive chunker splits on, so chunks land on section
  boundaries. The real docs.n8n.io page is committed as a test fixture — one test pins the
  *old* broken behaviour so the file documents what was wrong, others assert the nav is gone
  and the first chunk is content. 8 tests.
- **Creating an organization is staff-only (2026-07-29).** BotForge is run as one org per
  client, provisioned for them — not a self-serve product where anyone spins up as many as
  they like. A client seeing "New organization" invites an empty, confusing second org.
  `OrgSwitcher` now gates the create item + dialog on `is_staff` (the same
  `useSession((s) => Boolean(s.user?.is_staff))` pattern the Admin nav item uses); the
  switcher itself stays visible, and **switching between orgs you were invited to keeps
  working** — an agency may reasonably invite one person into several client orgs.
  **Enforced server-side too**, since hiding a button doesn't stop a direct API call:
  `POST /v1/orgs` returns a typed `orgs.create_forbidden` (403) when a non-staff user
  already belongs to an org. The **first** org is always allowed — signup's create-first-org
  step comes through the same endpoint and a brand-new user obviously isn't staff — and the
  count ignores soft-deleted orgs, so deleting your only org doesn't strand the account.
  Membership via *invite* counts too: the gate is "has an org", not "created one".
  5 backend tests, 5 web unit tests.
- **Team performance reporting (2026-07-29).** The analytics module reported on channels,
  providers and models — never on *people*. New `agent_performance()` +
  `GET /v1/analytics/agents` groups by `Handoff.assigned_to` and reports handoffs handled,
  average first-response time (first `provider="operator"` message minus the handoff's
  creation), average resolution time, and conversations closed. **No new tracking was added**
  — every input already existed. Durations are `None`, not `0`, when there's nothing to
  average: a teammate who has never resolved anything hasn't achieved a 0ms resolution time,
  and the UI renders that as an em dash. Operator messages that predate a handoff are excluded
  from the response average (they belong to an earlier handoff on the same conversation), and
  unassigned handoffs are attributed to nobody. A "Team performance" section on the Analytics
  page reuses the per-channel breakdown's visual language. 6 backend tests, 7 web unit tests,
  2 Playwright checks.
- **Campaigns — proactive widget messages (2026-07-29).** `campaigns` (migration `0012`) with
  two kinds. **`widget_trigger` ships live**: an active campaign rides the existing public
  config, and the widget schedules it client-side — fires once per visitor per campaign
  (sessionStorage), only on URLs matching an optional pattern, and **never once a real
  conversation has started or the panel is already open** (an unprompted message on top of
  someone's in-progress chat is an interruption, not a greeting). Delay is bounded 3–3600s:
  firing on page load reads as a popup ad. **`broadcast` is draft-only by design** — the model
  and UI exist but activating one is refused with a typed error until consent tracking, an
  unsubscribe path and rate-limited batch sending are built (ADR-039). Builder section on the
  Channels tab. 6 backend tests, 3 Playwright checks (including the real widget bundle opening
  itself on a plain page).
- **Help Center (2026-07-29).** Public-facing articles, deliberately a **separate store from
  the RAG Knowledge module**: Knowledge is retrieval material for the model, this is prose a
  human reads. `help_articles` (migration `0011`, slug unique per agent since slugs address
  articles in public URLs); authenticated CRUD at `/v1/help-articles`; unauthenticated
  `GET /v1/public/agents/{key}/help[/{slug}]` mirroring the widget-config pattern — a draft
  404s exactly like a non-existent article, so unpublished titles can't be enumerated.
  Authoring UI at `/knowledge/help-center` (plain `<textarea>`; no editor dependency), public
  pages at `/help/{agentKey}[/{slug}]` outside the authenticated layout. **Opt-in RAG sync**
  per article: publishing with "Also teach the AI" pushes the body into a knowledge base the
  agent *actually retrieves from* — targeting an unread KB would look like it worked and
  change nothing, so an agent with no RAG source gets a typed error instead. Editing replaces
  the synced document rather than accumulating a stale copy, and unpublishing removes it (the
  AI shouldn't answer from something a human can no longer read). Markdown rendered by a small
  in-repo subset renderer that **escapes before formatting**, so raw HTML in a body can never
  become live markup. 7 backend tests, 7 renderer unit tests, 3 Playwright checks.
  Also adds `/contacts` to the auth guard — it was missed when that page shipped earlier today.
- **Contacts / CRM (2026-07-29).** Extends the existing `Contact` (from the unified-inbox work)
  rather than introducing a second contact concept: `lead_stage`, `order_status`, `notes`,
  `labels` (migration `0010`, + a GIN index on labels and a composite on `(org, lead_stage)`
  for the list filters). `notes` and `labels` deliberately mirror `Handoff.notes`/`.tags`
  shapes so both timelines render the same way. New `/v1/contacts` module: paginated list
  searchable by **name or platform id** (an operator searching a phone number means the phone
  number), filterable by stage/label/channel; detail joins the contact's conversations;
  PATCH for stage/order status, PATCH for labels, POST for notes. `lead_stage` is a fixed
  funnel — free text would fragment into `qualified`/`Qualified`/`QUALIFIED` and break the
  filter — while `order_status` stays free text because what counts as one is
  business-specific. New `/contacts` page (table + detail panel reusing `ContactAvatar` and
  the inbox's card style), a `Contacts` nav item under Operate, and the inbox thread header
  now links the contact through to `/contacts/{id}`. 9 backend tests, 2 Playwright checks.
- **Macros (2026-07-29).** A named, ordered list of inbox actions (`reply` / `add_tag` /
  `assign` / `resolve`) run against one conversation in a click — model + migration `0009`,
  CRUD at `/v1/macros`, execution at `POST /v1/inbox/conversations/{cid}/macros/{id}`.
  Execution **reuses the same inbox service functions the manual buttons call**, so a macro
  can't drift from what those do (RBAC, webhook events, realtime publishes included).
  Atomic in two layers: every step is **validated before any is applied** — a DB rollback can
  undo a tag but cannot un-send a WhatsApp message, so a deleted canned response or a
  departed teammate fails while the conversation is still untouched — and execution is then
  wrapped in a SAVEPOINT. `add_tag` merges rather than replacing, and re-running doesn't
  duplicate. Per-action params are validated at *save* time, so a half-formed macro can't be
  stored and discovered later by an operator. Builder UI with reorder/remove; a Run macro
  dropdown in the thread header that stays hidden until the org has one. 8 backend tests,
  3 Playwright checks.
- **Canned responses (2026-07-29).** Org-scoped reply snippets (`canned_responses`, migration
  `0008`, unique per `(org, shortcut)` — shortcuts are typed rather than picked, so a duplicate
  would make the picker ambiguous). CRUD module at `/v1/canned-responses`; reading needs only
  `READ` (every operator needs them to reply), writing needs `INBOX_HANDLE`. Settings page for
  create/edit/delete. In the inbox composer, typing `/` opens a filtered picker — the trigger
  only fires at the start or after whitespace, so `example.com/refunds` and `24/07` don't
  misfire it — with arrow-key/Enter/Tab selection, and insertion replaces the trigger text
  rather than appending. 8 backend tests, 9 web unit tests, 2 Playwright checks.
  **Also fixes a pre-existing 500**: any endpoint whose Pydantic `field_validator` raised
  `ValueError` returned `internal_error` instead of a 422, because Pydantic puts the raised
  exception in the error's `ctx` and the handler json-encoded the raw list. Widget-config
  writes were affected long before this feature existed.
- **WhatsApp 24-hour customer-service window (2026-07-29).** A silent-failure bug, not a
  missing feature: `send()` fired free-form text with no awareness of Meta's window, so a
  reply sent more than 24h after the customer's last message was rejected (error `131047`)
  and simply never arrived — while sitting in the transcript looking sent. Adds
  `Conversation.last_inbound_at` (migration `0007`, backfilled from the newest `role="user"`
  message) kept **separate from `last_message_at`**, which moves on our own outbound too and
  would have made a bot reply look like customer activity and falsely re-open the window. New
  `BaseChannel.check_can_send()` hook — overridden only by WhatsApp — is called *before* the
  message is persisted, so a refusal leaves no phantom message; it raises a typed
  `whatsapp_window_closed` (409) per CLAUDE.md §8 instead of a swallowed no-op. Meta's own
  `131047` is honoured too, in case our clock disagrees. `send_template()` posts
  `type: "template"` with positional body params; approved names live on `Channel.config`
  (`templates`, comma-separated — Meta approval is a Meta-side process BotForge can't do).
  `InboxDetail.send_window` reports `{open, closes_at, templates}` (null on channels with no
  such limit), and the composer swaps the free-text box for a warning + template picker when
  it's shut. 7 backend tests, 3 Playwright checks.
- **Inbox shows every channel tab, connected or not (2026-07-29).** Reversal of a deliberate
  choice from the 07-28 inbox build: tabs were filtered to connected channels only, which hid
  exactly the platforms a user still needs to set up and made discovery depend on already
  knowing the builder's Channels tab exists. Now `inboxChannelTabs()` returns all 7
  unconditionally (replacing `visibleChannelTabs`), and the new `isChannelConnected()` drives
  *styling* instead of presence: an unconnected tab is dimmed with a hollow dot and an
  `aria-label` of `"{Channel} (not connected)"`. Selecting one replaces both panels with
  `"{Channel} isn't connected yet."` + a **Connect {Channel}** button — kept deliberately
  distinct from the connected-but-quiet `"Nothing in the inbox yet."`, since a channel that
  can't receive at all is a different situation from one that simply hasn't. **The credential
  form is not duplicated**: channels belong to an agent while the inbox spans the org, so the
  button resolves the target agent first (straight through when there's one, a picker when
  there are several, a create-agent prompt when there are none) and deep-links to
  `/agents/{id}?tab=channels&connect={type}`; `MessagingChannels` reads that param on mount,
  opens the existing `ConnectDialog`, and strips it so a reload doesn't reopen it. 11 web unit
  tests + 2 Playwright flows; analytics' channel breakdown is untouched.
- **Per-channel analytics — Dashboard + Analytics breakdown (2026-07-28).** With up to 7 channel
  types per agent, analytics had no channel dimension at all: `usage()` grouped only by
  day/provider/model and `overview()` returned one flat aggregate. **Backend**
  (`modules/analytics/`): new `ChannelBucket` + `Overview.by_channel`, `group_by=channel` on
  `usage()`, a `channels` CSV export kind, and a `channel=` filter param on every analytics
  endpoint. The channel set is the **union of channels with traffic and channels that are
  connected** — a plain `GROUP BY channel` would drop a live-but-silent channel, which is
  exactly the case that matters when WhatsApp/Instagram go live days before the first real
  message. `widget` is always included (every agent has the embeddable chat inherently, so it
  has no `channels` row to enable — the same rule the inbox tab bar follows); disabled channels
  are excluded. Rates guard the zero-conversation case. **Frontend:** one `ChannelBreakdown`
  component shared by the Dashboard and the Analytics page (icon + label from the existing
  `channel-meta` map, conversations with a proportional bar, resolution rate, cost), a "Tokens by
  channel" bar list, and an "Export channels" CSV control. A connected-but-empty channel renders
  as a flagged zero row with an em dash for its rate — `0%` resolution reads as failure rather
  than silence. `dashboard`/`api`/`web` got real labels (they're genuine conversation channels
  but never inbox tabs) instead of falling back to "Other". 5 backend tests, 9 web unit tests,
  1 Playwright flow covering one populated + one connected-but-empty channel across the
  Dashboard, the Analytics page, and the CSV. No migration — grouping by an existing column.
- **Unified multi-channel inbox — contacts, Instagram + Messenger, channel tabs (2026-07-28).**
  The inbox could previously only show a raw `channel_user_id`; it now shows a person, per
  channel. **Contacts** (`models/contacts.py`, migration `0006_contacts`): a `contacts` table
  unique on `(organization_id, channel, external_id)` + `conversations.contact_id`, with one
  upsert helper (`app/contacts/service.py`) used by every inbound path including the widget's
  `Visitor`. Names/avatars refresh with `COALESCE(new, old)` + a JSONB merge, so a payload that
  omits a name never erases one we had. Adapters supply identity inline
  (`InboundMessage.profile` — Telegram, WhatsApp) or through a new optional
  `BaseChannel.fetch_profile` hook, called only while the contact still lacks a name or avatar.
  **Instagram + Facebook Messenger** (`channels/instagram.py`, `facebook.py` over a shared
  `meta_messaging.py`): Meta unified the Send API, so one implementation covers both; the
  `X-Hub-Signature-256` check and `hub.challenge` handshake were factored out of `whatsapp.py`
  into `meta_signature.py` so all three Meta surfaces share one verification path. Deliveries
  are matched on the webhook `object` (`page` vs `instagram`), and echoes/read receipts are
  ignored. Profiles come from the Graph API. **Inbox API:** nested `contact` on
  `InboxItemOut`/`InboxDetail` (batched, no N+1) and a `?channel=` filter so tabs page
  server-side. **Inbox UI:** a channel tab bar — Web Chat always (the widget needs no
  connecting), every other tab only once that channel has an enabled row in the org
  (**superseded 2026-07-29: all 7 tabs now always render**, see the entry above), plus a
  "New" badge for the first week — and contact avatars with a platform badge in both the list
  row and the thread header. 9 backend tests, 12 web unit tests, 1 Playwright flow. New human
  secrets documented: `META_PAGE_ACCESS_TOKEN`, `INSTAGRAM_PAGE_ACCESS_TOKEN` (+ ids and a
  shared `META_VERIFY_TOKEN`); unset → warn and skip sending, inbound still works. Public
  **post-comment moderation is deliberately not included** (ADR-037). See ADR-036/037/038.
- **Widget customization → full parity (2026-07-21).** Extended the embeddable widget from
  accent/text/position to a complete design system, all flowing through the existing
  live-fetched public config (`GET /v1/public/agents/{key}/config`) so the embed snippet never
  changes. **Backend** (`modules/public/schemas.py` `WidgetTheme` + validated `WidgetConfigIn`,
  `modules/agents/service.py`): `widget_style` (solid|transparent), 4 colors
  (background/text/bubble/typing-area), `font_family` (5 system-safe stacks, no webfonts),
  `floating_button_style` (6 designs) + independent `floating_button_color`, `logo_url`,
  `input_bar_buttons` (attachment|emoji) — all nullable (unset = today's look), hex/enum
  validated with typed errors, **merge-on-write** so a partial PATCH never nulls sibling keys.
  **Logo upload** (`POST /v1/agents/{id}/widget/logo`) reuses the KB upload storage; rejects SVG +
  double-checks MIME **and** extension, 2 MB cap; served public + cross-origin at
  `GET /v1/public/agents/{key}/widget-logo`. **Widget bundle** (`packages/widget`): rewritten to
  apply everything via CSS custom properties (no per-agent rebuild), 6 inline-SVG launcher designs,
  `pulse-ring` keyframes gated behind `prefers-reduced-motion`, transparent frosted-glass style,
  emoji picker, and a `data-preview-mode` that live-applies posted config. **Builder** (`channels-tab`):
  full customization UI + a **real-widget iframe preview** driven by `postMessage` (not a CSS mock),
  reusing the debounced autosave. 4 backend tests + 2 Playwright checks (live embed reflects a
  saved change without re-pasting the script; builder preview applies a posted config instantly).
  No new env var (file storage reuses `UPLOAD_DIR`). See ADR-035.
- **Widget customization — parity fixes (2026-07-21).** Follow-up after a live comparison:
  (1) **bug** — the preview widget force-opened on every control change (`rebuildForPreview` did a
  full teardown + `api.open()` per `postMessage`). Split `build()` (once) from `applyConfig()`
  (in-place, preserves `state.open`, never force-opens); preview now starts **closed** like a real
  embed. (2) Transparent style uses its **own glass palette** (no solid header bar, white-glass bot
  bubbles + fixed near-black text, dark floating input pill), not "theme + blur". (3) The launcher
  picker previews the **real widget SVGs** (`lib/widget-icons.tsx`), not Lucide stand-ins. (4) The
  Channels tab renders **full-width** (drops the Playground column it doesn't need). (5) A
  builder-only **preview backdrop** swatch (local state, applied to the preview page via
  `postMessage`, never persisted). Regression tests: a config change never changes the panel's
  open/closed state; the transparent header has no solid backdrop. Widget still one file. See ADR-035.
- **Widget mobile-collision bug fix (2026-07-21).** On viewports under 768px the floating launcher
  and the fullscreen panel shared the same `z-index`, so an open panel painted over the launcher
  and could swallow clicks meant for the send button (a real bug for phone visitors). `api.open`/
  `close` now toggle a `bf-open` host class and `@media (max-width:767px){:host(.bf-open)
  .bf-launcher{display:none}}` hides the launcher while open at phone widths, relying on the panel's
  own header close button; ≥768px the launcher stays visible and still doubles as close. Playwright
  covers both breakpoints. (The preview-layout widening from the same original change was reverted
  per request; only the bug fix was re-applied.)
- **Widget preview single-host + sidebar flex fix (2026-07-27).** (1) A reported preview "overlap"
  (a white panel + dark launcher behind the themed one) was diagnosed live in the real Channels tab
  (Playwright): exactly **one** `#botforge-widget` host — not a duplicate mount. Hardened `widget.js`
  anyway: `build()` removes any pre-existing host first (idempotency), and now builds the widget
  **detached and themes it before inserting into the DOM**, so there's no unstyled/default first
  paint (the white/black flash the screenshot caught). New Playwright asserts a single host across
  repeated config posts + open/close toggles. (2) **Sidebar** (`shell/sidebar*.tsx`): the `<nav>`
  flex child lacked `min-h-0`, and the `aside` was `lg:static` under a `min-h-screen` shell, so it
  grew past the viewport and pushed the footer (Scale plan card + Collapse button) below the fold.
  Fix: `min-h-0` on the scrolling `<nav>` **and** `lg:sticky lg:top-0 lg:h-screen` on the aside to
  bound it to the viewport. Added a **header collapse toggle** beside the logo (rendered in both
  expanded + collapsed states, same `toggleCollapsed` as the footer control). Playwright: footer
  visible without scrolling at a short viewport; header toggle collapses/expands.

## Stubbed keys awaiting a real value
Provider/channel/billing keys that are stubbed and need a real value. (See `ENV.md`.)

As of **2026-08-02**, `GROQ_API_KEY` is the **only** LLM key set. `GEMINI_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY` and `ANTHROPIC_API_KEY` are all blank, as are every OAuth,
channel, Stripe and Sentry value — so an agent configured for any of those answers with its
fallback message and an error-level `chat_provider_unavailable`. Until ADR-044 those blanks were
*worse* than unset: each held its own placeholder comment as its value, which read as configured
and was sent to the provider as a real key.

**Pointing an agent at an unkeyed provider is therefore a live outage**, not a degraded mode.
Check the Model tab against this list before publishing.

## Roadmap / deferred enhancements
- **"aurozen ai"'s welcome message is `HI dude`.** Left as found on 2026-08-02 — the prompt rewrite
  was the requested scope — but this is the **first thing every visitor to the client's widget
  sees**, and it reads as a test string that got saved. One edit in the builder's Persona tab.
- **The real `.env` on this machine still uses the old inline-comment shape.** Harmless since
  ADR-044 (`Settings` drops comment-only values), and deliberately not hand-edited — but the
  reformatted `.env.example` is the shape to copy, and compose's `env_file` parser reaches
  containers that the Python fix does not. Worth a pass when the file is next touched.
- **A prompt's *tone* cannot be edited without re-checking its *grounding*.** ADR-045 records a
  rewrite that silently cost the refuse-to-guess behaviour (0/3 → 3/3 fabricated with no retrieved
  context). Tests pin the wording, but the behaviour needs a live model on the no-retrieval path —
  re-run that A/B before softening any prompt voice.
- ✅ ~~**Realtime hub → Redis pub/sub.**~~ **DONE (Phase 20, ADR-028).** The hub bridges over Redis
  behind the same `subscribe`/`unsubscribe`/`publish` interface; cross-node delivery is tested.
- ✅ ~~**Webhook retry sweep.**~~ **DONE (Phase 20).** `webhooks.sweep_pending` Celery beat job
  re-enqueues `pending` deliveries past `next_retry_at`; a `beat` service runs it.
- ✅ ~~**`provision-client.mjs` doesn't tag the workflows it clones.**~~ **DONE (2026-07-31,
  ADR-042)** — it now tags each clone with the org's real slug, before binding it.
- **The n8n API key needs tag scopes before any tagging can happen.** Blocking the ADR-042
  rollout as of 2026-07-31: the current key does workflows but **403s on every tag endpoint**
  (`GET`/`POST /api/v1/tags`, `PUT /api/v1/workflows/{id}/tags`), so
  `scripts/tag-n8n-workflows.mjs --apply` stops at its preflight and the whole inventory stays
  untagged — which under deny-by-default means invisible to every org. Mint a key with tag
  read/create **plus** workflow "update tags" (n8n → Settings → API) and re-run. Separately, the
  `:5678` instance (AUROZEN AI's, not BotForge's) rejects BotForge's key with 401, so its
  workflows can only be tagged with that project's own key or by hand in its UI.
- **Two `:5678` workflow groups have no BotForge org to map to.** `00001 — Load KB` /
  `00001 — Main Agent`, `00002 — …`, and `Website Lead — Contact Form` have no corresponding
  `organizations` row (checked by slug and by name), and `Website Lead — Contact Form` is bound
  as a tool by nobody. They look like AUROZEN AI clients rather than BotForge tenants; decide per
  workflow whether to create the org, tag it `internal`, or leave it to the other project.
- **No `PATCH /v1/auth/me`, so the profile page can't be edited.** Name and email render
  read-only (2026-07-30) because the endpoint doesn't exist — the page previously showed inputs
  and a Save button that discarded the edit. Adding it is small (validate + update `User`,
  re-verify on email change) but it needs a decision about whether changing an email re-triggers
  verification and invalidates sessions, which is why it wasn't bolted on to a mock-removal.
- ✅ ~~**`providerCatalog` is a hardcoded client-side list that can drift from the server.**~~
  **DONE (2026-08-03, ADR-047/048)** — deleted. The Model tab reads
  `GET /v1/credentials/providers` and shows only providers the org holds a key for, with models
  discovered from the provider itself. It had indeed drifted: it was still offering Groq's
  retired `mixtral-8x7b-32768`.
- **Four mock modules are now dead files.** `lib/mock/{analytics,automations,inbox,settings}.ts`
  have no importers anywhere in `apps/web/src` (verified 2026-07-30 while removing `data.ts`).
  They're harmless but they're also exactly how this bug happened — a fixture sitting in the tree
  long enough to look importable. Delete them once nothing is mid-flight against those screens.
- ✅ ~~**Consider deny-by-default for n8n visibility**~~ **DONE (2026-07-31, ADR-042)** — untagged
  is now visible-to-none. The alternative floated at the time, a BotForge-side
  `workflow_id → org_id` mapping table that doesn't depend on the operator remembering to tag, is
  still the more robust design and worth revisiting if tagging discipline slips; n8n tags were
  kept because they need no migration and no second place to look.
- **`docker-compose.prod.yml` has no `n8n` service.** It passes `N8N_BASE_URL: http://n8n:5678`
  to api/worker, but nothing in that file defines an `n8n` host, so every n8n feature (tools,
  automations, `scripts/provision-client.mjs` step 5) fails against a prod stack unless an
  external instance is supplied. Fix: add the service, internal-only with no published port,
  plus a volume for `/home/node/.n8n` and `N8N_DIAGNOSTICS_ENABLED=false` (see the dev service).
- **The rate limiter has no timeout on its Redis probe.** Measured 2026-07-30 while verifying the
  email work: with Redis unreachable, a signup takes **~4.3s** before `ratelimit_redis_unavailable`
  fires and the in-memory fallback takes over — per request, on every rate-limited endpoint (auth,
  public chat, channel webhooks). The fallback is correct, it's just reached slowly. Fix: a short
  connect/op timeout on the limiter's Redis client, or a cached "Redis is down" flag with a
  re-probe interval so only the first request pays. Unrelated to email; email's own enqueue is
  already bounded.
- **Async n8n late-result re-injection** (from ADR-025): async n8n tools resolve the `tool_run`
  record via the signed callback, but a late result is not re-injected into the same generation
  turn. Options: a "pending → notify" follow-up message on the conversation, or a short bounded
  wait on the callback before the turn ends. Deferred; the callback + record resolution work.
- **The E2E suite has outgrown the hardcoded public rate limits.** At 41 specs, a full
  back-to-back `playwright test` run intermittently trips `public_chat` (60/min) and
  `channel_webhook` (120/min) — 19 `429`s in one observed run — so a handful of specs fail and
  pass again in isolation. `AUTH_RATE_LIMIT` is already env-tunable for exactly this reason;
  these two are not. Fix: make their limits settings-driven and lift them in the E2E env,
  rather than raising them in production defaults.
- **Dashboard `AgentsPanel` / `ConversationsPanel` still render mock data.** `dashboard/page.tsx`
  passes `agents` and `recentConversations` from `@/lib/mock/data` into those two panels, so a
  fresh org sees invented agents ("Support Concierge", 1.3K chats) and invented conversations
  next to its own real stat cards, usage chart, and channel breakdown — which *are* wired to the
  live API. Noticed while adding the per-channel breakdown (2026-07-28) and deliberately left
  alone rather than silently widening that change. Fix: swap in `listAgents()` and the real
  conversations/inbox list the way `DashboardStats` already does.
- **Analytics page has no date-range or per-agent filter UI.** Every query on
  `analytics/page.tsx` calls the API with zero params, so the page always shows the backend's
  default last-30-days across all agents — even though `overview`/`usage`/`latency`/`export` all
  accept `agent_id`, `from`, `to`, and now `channel`. The controls were never built. Adding an
  agent picker + range picker would light up filtering across every view at once, including the
  new channel breakdown.
- ✅ ~~**httpOnly cookie migration for web auth tokens (ADR-019).**~~ **Refresh token DONE (Phase
  20)** — moved to an httpOnly/Secure/SameSite cookie set by a Next BFF (`/api/auth/*`). The
  **access token remains JS-readable by architectural necessity** (cross-origin Bearer + SSE + the
  inbox WebSocket's `?token=` query param). Documented in `docs/SECURITY.md §1`.
- ~~**Next.js 14 → 16 major upgrade (clears 5 web advisories).**~~ **DONE (Phase 19).** Upgraded to
  Next 16.2 + React 19.2 + ESLint 9 flat config; migrated async route params, `middleware.ts`→`proxy.ts`,
  and `next lint`→ESLint CLI; pinned `postcss ^8.5.10` to clear the nested-next copy. `npm audit` now
  reports **0 vulnerabilities** (was 4 high + 1 moderate). tsc + eslint + build + the full Playwright
  suite are green.
- **Drop `python-jose` → `PyJWT`/`authlib`** to clear the transitive `ecdsa` advisory (PYSEC-2026-1325,
  no fix). Not exploitable today (JWTs are HS256 symmetric; no ECDSA ops). See `docs/SECURITY.md §8`.

## Out of scope for v1
Full visual flow builder (ship minimal first), voice/telephony, native mobile apps, bot
marketplace, SSO/SAML, fine-tuning UI, MCP tool bridge, Qdrant swap, Kubernetes/Helm.
