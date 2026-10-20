# 28 — Implementation prompt: per-agent MCP, API connections, HTTP tools, webhooks

> **Give this whole file to Claude Code.** Say: *"Execute `docs/28-IMPLEMENTATION-PROMPT.md`, phase T1"*
> (then T2, T3 … one phase per session, each phase on its own branch + PR).
>
> Read first, in order: `CLAUDE.md` → `docs/28-AGENT-TOOLS-MCP-API-WEBHOOKS.md` (the plan; every decision
> is explained there) → `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §2 and §6 (non-negotiable tool rules) →
> `docs/SECURITY.md` → the `session-log` skill.
> Work task by task. Commit after every task (Conventional Commits). Do not stop for approval.
> Stop only for a secret only the human can create.

---

## Ground rules (apply to every phase)

- **Extend, don't rebuild.** Reuse `app/tools/service.execute_tool_call`, `app/tools/http_tool.py`,
  `app/tools/mcp_client.py`, `app/webhooks/*`, `app/chat/budget.AgentBudget`, `app/core/crypto`,
  `app/core/ssrf`, `app/core/plans.py`, the `Handoff` model and `neutralize_injections()`.
- **No live agent changes behaviour.** Every existing tool, MCP server and webhook keeps working. The full
  backend suite must pass after every task.
- **Every tool result** goes through plan §7 step 12 (`neutralize_injections` / `wrap_untrusted`). No bypass, ever.
- **Secrets** never appear in model context, `tool_runs`, `agent_steps`, logs, webhook payloads or API responses (masked only).
- **Migrations:** run `alembic heads` first and chain from the real head. Additive only. Write a working
  downgrade. Verify `upgrade → downgrade -1 → upgrade` against real Postgres.
- **ADRs:** next free number after the highest in `docs/DECISIONS.md` (currently ADR-131 → start at ADR-132).
- **Plan gates:** add `max_connections`, `max_mcp_servers`, `max_inbound_hooks` to `app/core/plans.py`
  with the values in plan §10. The trial/Starter value is 0 (locked). Enforce in the service layer with `402 plan_limit`,
  and at runtime in `build_tooling`.
- Every new env var goes into `.env.example` (comment on its own line, ADR-044) and `docs/ENV.md`.
- **Definition of Done** = `CLAUDE.md §2` (ruff, mypy, pytest, tsc, eslint, vitest, Playwright for UI).
- **No live third-party calls in tests.** Use `httpx.MockTransport` and in-process MCP fixtures.
  Live LLM tests only behind `RUN_LIVE_LLM_TESTS=1`.
- At the end of each phase, update `docs/PROGRESS.md` (Shipped enhancements), `docs/SESSION-HANDOFF.md` and
  `docs/RISK-REGISTER.md` if a risk changed. Tag `tools-phase-T<n>-complete`.

---

## T1 — Connections + API tool v2 + egress guard v2

**T1.1 Migration + models.** Add the `connections` table, the `tools` columns (`connection_id`, `messages`,
`approval_required`, `rate_limit`, `mode`), widen `tools.type`, add `agent_tool_bindings`, and add the `tool_runs`
columns (`agent_id`, `provider`, `request_sanitized`, `attempt`), all exactly as plan §6. **Backfill**
`agent_tool_bindings` from every `tools.agent_id`. Update `tests/test_db.py::EXPECTED_TABLES`.

**T1.2 Egress guard v2** (`app/core/egress.py`):
- `safe_request(method, url, *, allowed_hosts, headers, timeout, max_bytes, max_redirects=0)`.
- Resolve DNS first, reject private/loopback/link-local/CGNAT/metadata/IPv6-ULA, then connect to the **resolved IP**
  with the original Host/SNI (this stops DNS rebinding).
- Re-check every redirect hop.
- Stream the body and stop at `max_bytes`.
- Tests: the full SSRF matrix in plan §12. Move the old `http_tool` onto it.

**T1.3 Connections module** (`app/modules/connections/`): CRUD + `/test` per plan §8, gated by
`TOOLS_MANAGE`.
- Fernet `secrets_enc`. Masked reads. A blank field on PATCH keeps the secret.
- `oauth2_client_credentials` tokens are cached in Redis with their TTL.
- Audit log on every change.
- Tests: CRUD, RBAC, cross-org 404, masking, the test endpoint never returns a body.

**T1.4 API tool executor** (`app/tools/api_tool.py`, `type="api"`):
- Templates use `{{param}}` and `{{vars.x}}`. URL-encode path/query values; strip CR/LF from headers.
- `body_type` json/form/raw.
- JSON Schema arg validation (use the `jsonschema` lib if it is already present; otherwise add it) → structured error to the model.
- `response.pick` selectors (write a small safe JSONPath subset: `$.a.b[0].c`, with **no eval**) and `max_chars`.
- Retry only for idempotent requests. Secrets are injected from the Connection at execution time only.
- Treat `type="http"` as `api` with no connection (back-compat).
- Tests: templating fuzz, pick, retry rules, secret-leak grep over `tool_runs` after a real `run_turn`.

**T1.5 Binding-aware resolution.** `resolve_agent_tools` reads `agent_tool_bindings` (enabled, ordered).
Add `GET/PUT /v1/agents/{id}/tools` (`AGENTS_WRITE`). Prove that live agents resolve the same tool set as before.

**T1.6 Workflow `http` node** using the same executor. Add it to `app/workflows/graph.py` and the diff tables.

**T1.7 ADR-132** (Connections + API tool v2 + egress v2 + bindings).

---

## T2 — Per-agent MCP

**T2.0** Verify that the installed `mcp` SDK has `streamablehttp_client`. If not, raise the pin in `pyproject.toml`
and record the version in the ADR.

**T2.1 Migration:** extend `mcp_servers` (transport `streamable_http`, `connection_id`, `tools_cache`,
`tools_cached_at`, `last_error`) and add `agent_mcp_bindings` (plan §6).

**T2.2 Client:**
- Add the `streamable_http` transport to `mcp_client.py`. Header/bearer auth comes from `auth_config_enc` or the Connection.
- Destination check goes through the egress guard host rules.
- Timeout ≤ 30 s.

**T2.3 Stdio block:** the service layer refuses `transport="stdio"` unless the user `is_staff`, with
`mcp.transport_forbidden` (403). Test it. Existing stdio rows created by staff keep working.

**T2.4 Discovery cache:** `POST /v1/mcp/servers/{id}/refresh-tools`, plus PATCH/DELETE. Turns read the cache only.
Refresh when stale (> 24 h) in a Celery task, never inside a visitor turn.

**T2.5 Bindings:**
- `GET/PUT /v1/agents/{id}/mcp-servers`. `enabled_tools` empty = none.
- 20-tool cap per agent (MCP + API tools combined); over the cap → 400 `tools.too_many`.
- Names exposed as `<server_slug>__<tool>`. Per-tool overrides (description, approval, messages).
- Replace the org-wide `include_mcp` behaviour with bindings. **Migrate** any org that has `include_mcp` on by binding
  its servers' tools to the agents that used them, so nothing breaks.

**T2.6 Tests:**
- In-process Streamable HTTP MCP fixture server: discover → bind to agent A → `run_turn` calls it →
  result neutralized → `tool_runs` row.
- Agent B (same org, unbound) cannot call it. Org B cannot see it.
- New red-team fixture: an MCP result containing instructions.

**T2.7 ADR-133.**

---

## T3 — Webhooks: per-agent outbound + inbound trigger hooks

**T3.1 Migration:** `webhook_endpoints.agent_id`, plus `inbound_hooks` and `inbound_hook_events` (plan §6).

**T3.2 Outbound per agent:**
- Agent filter in the existing dispatch.
- New events `tool.called`, `variables.captured`, `conversation.ended` (with summary + variables),
  `lead.captured`, added to the event catalogue and docs.
- Payloads are sanitized (no secrets, args redacted).
- Tests: an agent-scoped endpoint gets only its agent's events; an org-wide endpoint still gets all events.

**T3.3 Inbound hooks module** (`app/modules/inbound_hooks/`):
- CRUD, `rotate-secret` (returns the secret once), `events` log.
- The public `POST /v1/hooks/in/{public_id}` endpoint:
  - HMAC + timestamp (300 s) or bearer auth, constant-time compare
  - Rate limit per hook (env-tunable), 256 KB body cap, `Idempotency-Key` dedupe in Redis for 24 h
  - Payload mapping, then the payload goes through `neutralize_injections()` before any model sees it
- The four actions: `send_message` (respect `check_can_send()` / WhatsApp templates), `run_workflow`,
  `set_variables` (writes to conversation variables; if T4 hasn't shipped, store on the contact and note it),
  `agent_turn` (sync, 25 s cap, reply in the response).
- 30-day event retention via Celery beat.

**T3.4 Tests:**
- Each action happy path; bad signature / replay / oversize / rate limit → correct status.
- Cross-org isolation. Plan gate.
- New red-team fixture: an inbound payload trying to redirect the agent.

**T3.5 ADR-134.** Add a public docs page `/docs/api/inbound-hooks` with placeholder values only (ADR-098 rule).

---

## T4 — Advanced tool behaviour

**T4.1 Conversation variables:**
- Decide between JSONB and a table (default JSONB on `conversations`) and record it in ADR-135.
- Built-in `set_variable` / `get_variable`. `{{vars.x}}` in prompts goes through the Phase F escaping.
- PII redaction on egress.

**T4.2 Tool messages:**
- `on_start` / `on_delay` / `on_error` as new stream events on the widget and every channel.
- The Playground shows them labelled. The widget renders them as normal bot bubbles.
- Never send a raw error to a visitor.

**T4.3 Async tools:**
- `mode=async` reuses the n8n signed-callback path.
- When the result arrives, post a follow-up assistant message (one extra generation with the result, through
  the full pipeline). This closes the "async late-result re-injection" roadmap item.
- Handle a disconnect safely (R14 lessons: use a Celery task, not an inline await).

**T4.4 Approval required:**
- Pause the tool call and create `Handoff(reason="tool_approval")` with the sanitized args.
- `GET /v1/inbox/tool-approvals` and `POST …/decide` (`INBOX_HANDLE`).
- Approve → execute, then continue the turn as a follow-up message. Reject → the model receives "declined".
- Reuse the workflow-approval code path.

**T4.5 Flow built-ins:** `end_conversation`, and `collect_field` (ask, validate the type email/phone/number/date/text, store as a variable).
`request_handoff` already exists; keep it.

**T4.6 Rate limits + tool choice + parallel calls:**
- Per-tool limits per conversation / per minute (Redis) → "rate_limited" result.
- Agent setting `tool_choice` (`auto` / `required_first_turn` / `none`).
- Parallel calls via `asyncio.gather`, each decrementing `AgentBudget`.
- Per-org outbound concurrency cap (env `TOOL_MAX_CONCURRENCY_PER_ORG`, default 5).

**T4.7 Tests:** every behaviour through the real `run_turn`, plus a budget-inheritance test with parallel and async calls.
**ADR-135.**

---

## T5 — UI (apps/web)

Use the existing design system only: tokens, `components/ui/*`, `StatusPill`, light + dark.
`style-guardrails.test.ts` must stay green.

**T5.1 Settings → Connections page:** list, add/edit (auth-type-specific fields), test, masked secrets, delete
(blocked while tools use the connection, and say which tools).

**T5.2 Agent builder → Tools tab redesign:** sections Built-in · API tools · MCP servers · Automations · Webhooks.
Each card shows its status, last run and 7-day calls. Locked features show a neutral lock chip with an upgrade link.

**T5.3 API tool editor (dialog/drawer):**
- Connection picker, method + path, params builder (name/type/required/description/enum)
- Headers/query/body editors with `{{` autocomplete (params + vars)
- Response pick + capture mapping, messages, approval toggle, mode
- **Test** panel (sample args → sanitized request/response + latency)

**T5.4 MCP panel:** add server (URL, transport, auth) → Test → Refresh tools → per-agent checklist (shows count/20)
→ per-tool overrides.

**T5.5 Webhooks panel:**
- Outbound endpoints for this agent, with event checkboxes and the delivery log.
- Inbound hooks: copy URL, reveal secret once, sample cURL (placeholders only), last 20 events, action config forms.

**T5.6 Logs:** a `tool_runs` table (filters: tool/status/date) with an expandable sanitized I/O view and "Replay in test panel".

**T5.7 Inbox:** a "Tool approvals" view (args, approve/reject).

**T5.8 Tests:**
- vitest for the params builder and template autocomplete.
- Playwright: connection → API tool → test → bind → Playground uses it; MCP add → bind (against a test fixture
  server); inbound hook created + called via `request` → event visible. Both themes. axe green.

---

## T6 — OpenAPI import + presets (only when asked by name)

- `POST /v1/tools/import/openapi` (file, text or SSRF-guarded URL; OpenAPI 3.x only; 25-operation cap).
  Generate API tools on a chosen Connection, with schemas from parameters/requestBody.
- Presets as data files in `app/tools/presets/*.json` (connection template + suggested tools): HubSpot, Calendly,
  Stripe (read-only), Shopify, Airtable, Notion, Slack incoming webhook.
- UI: "Import from OpenAPI" and "Start from preset" in the Tools tab. ADR-136.

## T7 — Agent as an MCP server (only when asked by name)

- `/v1/mcp/agents/{public_key}`: Streamable HTTP, API-key auth (scope `agents:chat`), one tool `ask_agent(message,
  conversation_id?)` running the normal guarded turn. Counts against message metering.
- Test it with the MCP inspector. ADR-137.

---

## Final report format (each phase)

- What shipped: bullet list of files/endpoints.
- Verification: commands run + pass counts.
- What was NOT done and why.
- Anything the human must do (secrets, plan-limit confirmation, deploy steps).
