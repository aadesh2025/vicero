# docs/28 — Per-Agent Tools: MCP Servers, API Connections, HTTP Requests & Webhooks

> Status: **spec. Read-always, execute-on-request** (same rule as docs/11 and docs/17, CLAUDE.md §10b).
> Build only when a phase is triggered by name. The build prompts are in `docs/28-IMPLEMENTATION-PROMPT.md`.
> Written 2026-10-19 against `master` @ `6c564ab`.

---

## 0. Executive summary

- **Goal:** every Vicero agent can use external tools, configured **per agent**, the way Vapi, Botpress and ManyChat allow:
  1. **MCP**: connect the agent to the customer's own MCP server(s).
  2. **API connections**: save an external API once (base URL + auth), then build many tools on top of it.
  3. **HTTP request tools**: any method, headers, query, body, typed parameters and response mapping.
  4. **Webhooks**: outbound events **per agent**, plus **inbound** trigger URLs that start or feed an agent.
  5. **Advanced tool-calling behaviour**: tool messages, async tools, approval, variables, retries, rate limits, a test console and logs.
- **About 60% of the foundation already exists.** This track extends it and does not rebuild it (see §2).
- **Business reason:** this is the main feature that makes the **Pro ($99)** and **Business ($199)** plans worth paying for. Starter and the trial stay locked (see §10).

---

## 1. Goals and non-goals

### Goals

- Per-agent tool configuration. Today MCP servers are org-wide and only an `include_mcp` flag exists.
- One **unified tool registry**: built-in, API/HTTP, MCP, n8n and webhook tools all go through one dispatch, one security pipeline and one log.
- Secrets are **never** shown to the model, never stored in `tool_runs`, and never returned by the API (masked only).
- Self-serve: a customer can connect a tool **without Aadesh doing anything**.
- Every new capability is gated by plan, using `app/core/plans.py`.

### Non-goals (this track)

- **No tenant `stdio` MCP servers.** A customer's stdio server is an arbitrary command running on *our* VPS. That is remote code execution by design, so stdio stays **platform-staff only**.
- No visual "Execute Code" node (the Botpress feature). It needs a sandbox (docs/17 §11, still undecided).
- No public integration marketplace or Hub. Presets (Phase T6) are enough.
- No billing pass-through for third-party API costs. The platform-wide rule applies: the customer pays providers directly.

---

## 2. What already exists vs. the gap

| Area | Already built | Gap this track closes |
|---|---|---|
| HTTP tools | `app/tools/http_tool.py`: `{{arg}}` templating, SSRF host check, timeout, 4 KB cap | No reusable auth (secrets sit in tool `config`), no query params, no response mapping, no retries, no DNS-rebinding defence, no variable capture |
| MCP | `mcp_client.py` (stdio + SSE), `mcp_servers` table (org-scoped, encrypted auth), `/v1/mcp/servers` + test-connection | No **Streamable HTTP** transport (the current MCP standard; SSE is deprecated), no **per-agent binding**, no per-tool allowlist, no tool cache, stdio not blocked for tenants, no UI |
| n8n | Sync and async tools, signed webhooks, bind-as-tool | Unchanged. It remains the "automation" provider |
| Outbound webhooks | `webhook_endpoints` (org-level), HMAC signing, retry/backoff, delivery log, event catalogue | No `agent_id` scoping, no agent-level events (tool.called, variables.captured, conversation.ended with summary) |
| Inbound webhooks | Channel webhooks only (WhatsApp/Telegram/…) | No **generic inbound trigger URL per agent** |
| Agent loop | `run_turn` multi-step loop, `AgentBudget`, `agent_steps` trace, `neutralize_injections()` on every tool result | No tool messages, async tool results, per-tool approval, conversation variables, per-tool rate limits |
| Plans | `max_tools`, `max_webhooks` per plan | No `max_mcp_servers`, `max_connections` or `max_inbound_hooks` |

---

## 3. Competitor feature map, and what Vicero builds

| Feature | Vapi | Botpress | ManyChat | Vicero (this doc) |
|---|---|---|---|---|
| Custom function/API tool | ✅ function, apiRequest | ✅ API call card | ✅ External Request | ✅ **API tool v2** (T1) |
| Saved auth / credentials | ✅ credentials | ✅ integration config | ⚠️ per request | ✅ **Connections** (T1) |
| MCP client | ✅ MCP tool | ⚠️ limited | ❌ | ✅ **Per-agent MCP** (T2) |
| Map response to variables | ⚠️ | ✅ variables | ✅ custom fields | ✅ **Variable capture** (T4) |
| Messages while the tool runs | ✅ request-start / delayed / failed | ⚠️ | ❌ | ✅ **Tool messages** (T4) |
| Async tool (result arrives later) | ✅ async | ⚠️ | ❌ | ✅ (reuses the n8n async callback) (T4) |
| Server URL (all events to one URL) | ✅ | ❌ | ❌ | ✅ **Agent event webhook** (T3) |
| Inbound trigger | ⚠️ | ✅ webhook integration | ✅ via Zapier/Make | ✅ **Inbound hook URL** (T3) |
| End call / transfer / handoff tools | ✅ | ✅ | ✅ live chat | ✅ built-ins (handoff already exists) (T4) |
| Human approval before a risky tool | ❌ | ⚠️ | ❌ | ✅ reuses `Handoff` (T4). **Differentiator** |
| OpenAPI import → tools | ❌ | ❌ | ❌ | ✅ (T6). **Differentiator** |
| Tool test console + logs | ✅ | ✅ | ⚠️ | ✅ (T5) |
| Agent exposed *as* an MCP server | ❌ | ❌ | ❌ | ⏸️ optional (T7). **Differentiator** |

---

## 4. Architecture

### 4.1 Unified tool registry

```
                     Agent turn (run_turn + AgentBudget)
                                   │
                         resolve_agent_tools(agent)
                                   │
   ┌────────────┬──────────────┬───┴─────────┬─────────────┬──────────────┐
   ↓            ↓              ↓             ↓             ↓              ↓
 Built-in    API tool        MCP tool      n8n tool    Webhook tool    Flow tools
 (exists)   (http v2)      (per-agent    (exists)    (fire-and-     (handoff, end,
               │            binding)                  forget POST)    set_variable)
               ↓               ↓
          Connection      MCP server
        (auth, base URL,  (streamable_http
         allowed hosts)    / sse; stdio = staff only)
```

- **One dispatcher**: `execute_tool_call()` in `app/tools/service.py` stays the single entry point.
- **One result path**: every provider returns a `ToolResult`, then the result goes through the §7 pipeline.
- **Naming rule:** the model sees `snake_case` names, unique per agent. MCP tools are namespaced `<server_slug>__<tool>` so two servers can't collide.

### 4.2 Core concepts (plain words)

- **Connection**: "how to log in to an external system". Holds the base URL, auth type and encrypted secrets. It is reused by many tools and MCP servers.
- **Tool**: one action the AI can take. It has a name, a description, a parameter schema and a provider config.
- **Agent tool binding**: which tools **this agent** may use, plus per-agent overrides (messages, approval, rate limit).
- **Conversation variables**: a key/value bag on the conversation (like ManyChat custom fields), filled by tools and readable in prompts.
- **Inbound hook**: a secret URL that external systems call to trigger this agent.
- **Agent webhook**: an outbound URL that receives this agent's events.

---

## 5. Feature specifications

### 5.1 Connections (T1)

- **Auth types:** `none` · `api_key` (header or query, custom name) · `bearer` · `basic` · `custom_headers` · `oauth2_client_credentials` (token cached in Redis until expiry). Interactive `oauth2_authorization_code` comes later (T6+).
- **Fields:** `name`, `base_url`, `auth_type`, `secrets_enc` (Fernet via `app.core.crypto`), `default_headers`, `allowed_hosts` (defaults to the base URL host only), `timeout_s`, `enabled`.
- **Test connection:** a `GET` to an optional `health_path`. It returns status code + latency, **never** the response body.
- **Masking:** the API returns `••••last4` only. The update endpoint treats a blank field as "keep the existing secret".

### 5.2 API tool v2 (T1)

- **Config (`tools.type = "api"`):**
  - `connection_id` (optional; without one it behaves like today's raw HTTP tool)
  - `method`: GET/POST/PUT/PATCH/DELETE
  - `path` (joined to the connection's `base_url`) or a full `url`
  - `query`, `headers`, `body` templates using `{{param}}` **and** `{{vars.name}}` (conversation variables)
  - `body_type`: json | form | raw
  - `response.pick`: a list of JSONPath-like selectors (`$.data.items[0].price`) so the model gets **only** the useful fields
  - `response.max_chars` (default 4000, hard cap 16000)
  - `capture`: map response selectors into conversation variables
  - `retry`: `{max: 0-3, backoff_ms}`. Allowed **only** for GET/PUT/DELETE, or for POST with an idempotency key header
  - `timeout_s` (≤ 30)
- **Parameters:** a JSON Schema built in the UI (name, type, required, description, enum). Arguments are **validated before execution**. On failure the model gets a structured error back and can retry.
- **Templating rule:** values are inserted as **data**. URL path/query values are URL-encoded, and header values have CR/LF stripped (blocks header injection).
- **Back-compat:** the existing `type = "http"` tools keep working. They are migrated lazily (read as `api` with no connection).

### 5.3 Per-agent MCP (T2)

- **Transports:** add `streamable_http` (the default for new servers) next to `sse`. `stdio` is refused for non-staff by a code check (`mcp.transport_forbidden`), not just hidden in the UI.
- **Auth:** none / bearer / custom headers, or reference a **Connection**. OAuth for MCP comes later.
- **Discovery cache:** `mcp_servers.tools_cache` (JSONB) + `tools_cached_at`, refreshed on demand ("Refresh tools") and when stale (> 24 h). Agent turns read **the cache**, so a turn never waits on discovery.
- **Binding (`agent_mcp_bindings`):**
  - `agent_id`, `mcp_server_id`, `enabled_tools` (allowlist; **an empty list means none**, never "all")
  - `tool_overrides` per tool (custom description, approval required, messages)
- **Limits:** at most **20 MCP tools exposed per agent** (larger toolsets degrade model accuracy and token cost). Per-call timeout ≤ 30 s.
- **Result:** flattened to text/JSON, capped, then sent through the §7 pipeline (it is untrusted, like every other tool result).

### 5.4 HTTP request inside workflows (T1, small)

- The workflow `tool` node already calls `execute_tool_call`, so API tools v2 work in workflows automatically.
- Add an `http` workflow node type that uses the same executor with an inline config. That gives one execution path, not two.

### 5.5 Webhooks (T3)

**A. Outbound, per agent (the Vapi "Server URL" equivalent)**

- Add nullable `agent_id` to `webhook_endpoints`. Null keeps today's org-wide behaviour.
- New events:
  - `tool.called`: tool name, status, latency, sanitized args (secrets stripped)
  - `variables.captured`
  - `conversation.ended`: summary, captured variables, channel, contact
  - `lead.captured`: CRM contact created or updated
  - `handoff.requested` and `handoff.resolved` (exist; now also agent-filtered)
- The same HMAC signing, retry and delivery log as today. No new delivery engine.

**B. Inbound trigger hooks**

- `POST /v1/hooks/in/{hook_public_id}` is public and rate-limited. Auth is a signature (`X-Vicero-Signature`, HMAC + timestamp, 300 s window) **or** a bearer secret. The caller chooses at creation.
- **Actions** (one per hook):
  1. `send_message`: send a proactive message to a contact on a channel (WhatsApp must respect the 24 h window or use a template, reusing `check_can_send()`)
  2. `run_workflow`: start a published workflow with the payload as variables
  3. `set_variables`: write payload fields into a contact's conversation variables (e.g. "order shipped" from Shopify)
  4. `agent_turn`: run one agent turn with the payload as context and return the reply in the HTTP response (sync, ≤ 25 s)
- **Payload mapping:** selectors from payload to fields. Payload text is **untrusted**: it goes through `neutralize_injections()` before any model sees it.
- **Idempotency:** an optional `Idempotency-Key` header, deduped in Redis for 24 h.

### 5.6 Advanced tool behaviour (T4)

- **Tool messages:**
  - `on_start` ("Let me check that for you…")
  - `on_delay` (after N seconds)
  - `on_error` (sent instead of exposing the error)
  - Sent to the visitor as stream events on widget and channels. The Playground shows them labelled.
- **Async tools:** `mode: async`. The tool returns at once, the bot says `on_start`, and the result arrives through the existing signed callback (the n8n async pattern). A follow-up assistant message is then posted to the conversation. This closes the "async late-result re-injection" roadmap item.
- **Approval required:** the tool call pauses and creates a `Handoff(reason="tool_approval")` with the proposed args. The operator approves or rejects in the Inbox. On approve it executes and the bot continues; on reject the bot gets a "declined" result. This reuses ADR-057 / the workflow-approval path.
- **Conversation variables:**
  - `conversation_variables` table (or a `conversations.variables` JSONB; decide in T4 ADR)
  - Built-in `set_variable` / `get_variable` tools
  - `{{vars.x}}` usable in prompts (through the Phase F escaping) and in API tool templates
  - PII values follow the existing redaction-on-egress rule
- **Flow built-ins:** `end_conversation`, `transfer_to_human` (exists as `request_handoff`), `collect_field` (ask, validate type, store as a variable).
- **Per-tool rate limit:** calls per conversation and per minute per org (Redis). Over the limit, the model gets a structured "rate_limited" result.
- **Tool choice policy per agent:** `auto` (default) · `required_first_turn` · `none`.
- **Parallel calls:** allowed when the provider returns several calls in one step, run with `asyncio.gather`. Each call still decrements `AgentBudget.max_tool_calls`.

### 5.7 UI (T5)

- **Agent builder → Tools tab**, redesigned into sections:
  - Built-in · API tools · MCP servers · Automations (n8n) · Webhooks
  - Each card shows enabled/disabled, last-run status and calls in the last 7 days
- **API tool editor:**
  - Connection picker, method + path, params builder, headers/query/body editors with `{{` autocomplete
  - Response "pick" and capture mapping
  - Messages, approval toggle
  - **Test** panel (run with sample args, show request + sanitized response + latency)
- **MCP panel:** add server (URL + auth) → Test → Refresh tools → checklist of tools to enable for **this** agent → per-tool overrides.
- **Settings → Connections:** org-level list, add/edit/test, masked secrets.
- **Webhooks panel (per agent):** outbound endpoints + event checkboxes + delivery log; inbound hooks with a copyable URL, secret reveal-once, a sample cURL, and the last 20 requests.
- **Logs:** a `tool_runs` table filtered by agent/tool/status, with an expandable sanitized I/O view and a "Replay in test panel" button.
- **Plan gates:** locked features are shown with a neutral lock chip and are not hidden (the same pattern as the S1 trial).

### 5.8 OpenAPI import + presets (T6)

- Upload or paste an OpenAPI 3.x document (or give a URL, SSRF-guarded). Pick operations, and they generate API tools on a Connection. Cap: 25 operations per import.
- **Presets** (a Connection template + suggested tools): Google Sheets (via n8n), HubSpot, Calendly, Stripe (read-only), Shopify, Airtable, Notion, Slack incoming webhook. Presets are data files, not code.

### 5.9 Optional (T7): agent as an MCP server

- Exposes each published agent at `/v1/mcp/agents/{public_key}` (Streamable HTTP, API-key auth) with an `ask_agent` tool, so customers can use their Vicero agent inside Claude Desktop, Cursor or their own agents.
- Unique in this market, but **build it only after T1–T5 have real users**.

---

## 6. Data model (Alembic; check `alembic heads` first, the repo uses hash revision ids)

```
connections
├── id, organization_id (FK, index), name, base_url
├── auth_type, secrets_enc (Text, Fernet), default_headers (JSONB)
├── allowed_hosts (ARRAY[str]), timeout_s, health_path, enabled
├── created_by, created_at, updated_at
└── UNIQUE(organization_id, name)

tools  (existing; extend)
├── type: builtin | http | n8n | mcp | api | webhook | flow   (widen String(16) check)
├── connection_id (FK connections, nullable, ON DELETE RESTRICT)
├── messages (JSONB: on_start, on_delay{after_s,text}, on_error)
├── approval_required (bool, default false)
├── rate_limit (JSONB: per_conversation, per_minute)
└── mode (sync | async, default sync)

agent_tool_bindings          -- NEW: replaces "tool.agent_id only" for reuse across agents
├── id, organization_id, agent_id, tool_id
├── enabled, overrides (JSONB), position
└── UNIQUE(agent_id, tool_id)

mcp_servers  (existing; extend)
├── transport: stdio | sse | streamable_http
├── connection_id (nullable)
├── tools_cache (JSONB), tools_cached_at, last_error

agent_mcp_bindings           -- NEW
├── id, organization_id, agent_id, mcp_server_id
├── enabled_tools (ARRAY[str]), tool_overrides (JSONB), enabled
└── UNIQUE(agent_id, mcp_server_id)

webhook_endpoints  (existing; extend)
└── agent_id (nullable FK agents, ON DELETE CASCADE, index)

inbound_hooks                -- NEW
├── id, organization_id, agent_id, public_id (unguessable, unique)
├── name, action (send_message|run_workflow|set_variables|agent_turn)
├── action_config (JSONB), payload_mapping (JSONB)
├── auth_mode (hmac|bearer), secret_enc, enabled, last_called_at

inbound_hook_events          -- NEW (log; keep 30 days)
├── id, inbound_hook_id, status, http_status, latency_ms
├── payload_sanitized (JSONB, capped), error, created_at

conversation_variables       -- NEW (or conversations.variables JSONB, ADR in T4)
├── conversation_id, key, value (JSONB), source (tool|hook|user|operator), updated_at
└── PK(conversation_id, key)

tool_runs  (existing; extend)
├── agent_id (nullable), provider (builtin|api|mcp|n8n|webhook|flow)
├── request_sanitized (JSONB), attempt (int)
└── output stays sanitized only (docs/17 §2.4)
```

- **Backfill:** for every existing `tools` row with `agent_id`, create an `agent_tool_bindings` row, so nothing changes for live agents.
- **Indexes:** `(organization_id, agent_id)` on all binding tables, and `tool_runs(organization_id, agent_id, created_at DESC)`.

---

## 7. Runtime pipeline (every tool call, every provider; no exceptions)

```
Model proposes tool call
 ↓
1. Plan gate (plans.py)          → 402-style structured result if not allowed
2. Binding check                 → tool is bound + enabled for THIS agent
3. Arg validation (JSON Schema)  → structured error back to the model
4. Rate limit (Redis)            → "rate_limited" result
5. Budget decrement (AgentBudget, inherited, never reset)
6. Approval? → pause, Handoff(reason=tool_approval) → resume later
7. Emit on_start message (if set)
8. Resolve secrets server-side (Connection) → never in model context, never in logs
9. Egress guard: scheme http(s); host ∈ connection.allowed_hosts; DNS resolve →
   reject private/loopback/link-local/metadata IPs; connect to the RESOLVED IP
   (stops DNS rebinding); no redirects (or re-check each hop, max 3)
10. Execute (timeout ≤ 30 s, retry only if idempotent)
11. Cap size → response.pick → redact secrets/PII patterns
12. UNTRUSTED → neutralize_injections() / wrap_untrusted()   ← MANDATORY
13. Capture variables (sanitized values)
14. Persist tool_run (sanitized) + agent_step + emit tool.called webhook
15. Back into the loop (think again / final answer) → L5 output guard
```

---

## 8. API surface

```
# Connections (TOOLS_MANAGE)
POST/GET            /v1/connections
GET/PATCH/DELETE    /v1/connections/{id}
POST                /v1/connections/{id}/test

# Tools (existing routes; extended schemas)
POST/GET            /v1/tools                 (type=api|webhook|flow added)
POST                /v1/tools/{id}/test       (sanitized request + response returned)
POST                /v1/tools/import/openapi  (T6)

# Agent bindings (AGENTS_WRITE)
GET/PUT             /v1/agents/{id}/tools                  (bulk set bindings + order)
GET/PUT             /v1/agents/{id}/mcp-servers            (bindings + enabled_tools)

# MCP servers (TOOLS_MANAGE; existing + new)
PATCH/DELETE        /v1/mcp/servers/{id}
POST                /v1/mcp/servers/{id}/refresh-tools

# Webhooks
POST/GET            /v1/webhooks?agent_id=     (existing, agent filter added)
POST/GET            /v1/agents/{id}/inbound-hooks
PATCH/DELETE        /v1/inbound-hooks/{id}
POST                /v1/inbound-hooks/{id}/rotate-secret   (returns the secret ONCE)
GET                 /v1/inbound-hooks/{id}/events
POST                /v1/hooks/in/{public_id}               (PUBLIC, rate-limited, signed)

# Logs
GET                 /v1/tool-runs?agent_id=&tool_id=&status=&cursor=

# Approvals (reuse inbox)
GET                 /v1/inbox/tool-approvals
POST                /v1/inbox/tool-approvals/{id}/decide
```

- All of these follow the existing conventions: org-scoped, RBAC-gated, `{error: {code, message, details}}`, cursor pagination.

---

## 9. Security checklist (merge-blocking)

- [ ] Tenant `stdio` MCP is refused in **code** (service layer), with a test.
- [ ] DNS-rebinding-safe egress: resolve, check the IP, then pin the connection. Covers redirects and metadata IPs (`169.254.169.254`, `fd00::/8`, `::1`, `0.0.0.0`).
- [ ] Secrets: Fernet-encrypted, masked on read, never in `tool_runs` / `agent_steps` / logs / webhook payloads. A test greps a full turn's persisted rows for the secret.
- [ ] Header/CRLF injection and URL-encoding in templates. Fuzz-tested.
- [ ] Every result passes through `neutralize_injections()`. New red-team fixtures cover an MCP result and an inbound-hook payload carrying instructions.
- [ ] Inbound hooks: HMAC + timestamp (300 s) or bearer, constant-time compare, per-hook rate limit, body cap 256 KB, idempotency.
- [ ] Per-org concurrency cap on outbound tool calls (stops one tenant from flooding the VPS).
- [ ] Response size caps at read time (stream and stop at the cap; never buffer an unbounded body).
- [ ] Audit log entries for connection/tool/hook create, update, delete and secret rotation.
- [ ] Cross-org tests: org B cannot bind, test, read logs of, or trigger org A's connection, tool, MCP server or hook (extend `test_tenant_isolation.py`).

---

## 10. Plan gating (proposal; confirm in §14)

| Limit | Trial | Starter $49 | Pro $99 | Business $199 | Legacy |
|---|---|---|---|---|---|
| Tool calling | ❌ | ❌ | ✅ | ✅ | ✅ |
| API tools (`max_tools`) | 0 | 0 | 8 | ∞ | ∞ |
| Connections (`max_connections`, new) | 0 | 0 | 5 | ∞ | ∞ |
| MCP servers (`max_mcp_servers`, new) | 0 | 0 | 2 | ∞ | ∞ |
| Outbound webhooks (`max_webhooks`) | 0 | 0 | 5 | ∞ | ∞ |
| Inbound hooks (`max_inbound_hooks`, new) | 0 | 0 | 3 | ∞ | ∞ |
| Approval / async / OpenAPI import | ❌ | ❌ | ✅ | ✅ | ✅ |

- **Counting rule:** one MCP server counts as **1 MCP server**, not as N tools. API tools count against `max_tools`. Built-in tools never count.
- **Downgrade rule:** over-limit items are **disabled, not deleted**, newest first. The owner sees which ones were disabled.

---

## 11. Phases and definition of done

| Phase | Scope | Done when |
|---|---|---|
| **T1** | Connections + API tool v2 + egress guard v2 + `http` workflow node | CRUD + test endpoints; executor with pick/capture-ready/retry; SSRF + rebinding tests; secret-leak test; old `http` tools still pass |
| **T2** | Per-agent MCP: streamable_http, bindings, allowlist, cache, stdio block | A real Streamable HTTP test server (in-process fixture) is discovered, bound to one agent, called in a turn; agent B can't see it; stdio refused for non-staff |
| **T3** | Per-agent outbound webhooks + new events; inbound hooks (4 actions) | Signed inbound call runs each action; bad signature/replay → 401; agent-scoped endpoint receives only its agent's events |
| **T4** | Tool messages, async, approval, variables, flow built-ins, rate limits, parallel calls | Each behaviour has a test through the real `run_turn`; approval round-trip via inbox; budget still inherited |
| **T5** | UI: Tools tab, MCP panel, Connections, Webhooks, Logs, test console | Playwright: create connection → API tool → test → bind → playground uses it; both themes; axe green; plan locks visible |
| **T6** | OpenAPI import + presets | Import a fixture spec → tools generated + callable; caps enforced |
| **T7** | (optional) Agent as MCP server | Claude Desktop / MCP inspector can call `ask_agent` with an API key |

- **Order rule:** T1 → T2 → T3 → T4 → T5. T5 can start in parallel after T2 ships the backend for its panels. T6 and T7 come only after real usage.

---

## 12. Testing strategy

- **Unit:** templating/encoding, JSONPath pick, schema validation, signature verify, rate limiter, plan counting.
- **Integration (real Postgres):** CRUD, RBAC matrix, tenant isolation, backfill migration up → down → up.
- **Network fixtures:** a local `httpx.MockTransport` for API tools, and an **in-process MCP Streamable HTTP server** for T2. No live third-party calls in CI.
- **Security:** SSRF matrix (private IPs, IPv6, decimal IPs, `localhost.` with trailing dot, a DNS name resolving to `127.0.0.1`, redirect-to-private), secret-leak grep, red-team tool-result fixtures.
- **E2E:** one Playwright happy path per panel. Live-LLM tests stay opt-in (`RUN_LIVE_LLM_TESTS=1`) to protect the Groq free tier.

---

## 13. What NOT to build (yet)

- Arbitrary code execution / Execute Code cards. They need a sandbox decision first.
- OAuth authorization-code flows for every SaaS. Use n8n for complex OAuth apps until demand is proven.
- More than 20 tools per agent. It hurts accuracy; tell customers to split agents or use workflows.
- A separate webhook delivery engine. Reuse the existing one.
- A second approval primitive. Reuse `Handoff`.
- GraphQL-specific tooling. A POST API tool covers it.

---

## 14. Open questions for Aadesh (defaults are used if unanswered)

1. **Plan limits in §10.** Default: as proposed.
2. **Conversation variables storage:** a separate table or a JSONB column? Default: JSONB on `conversations` (simpler; fine at current scale).
3. **Inbound `agent_turn` sync timeout of 25 s.** Default: yes.
4. **Should T7 (agent-as-MCP-server) be on the roadmap now?** Default: hold.

---

## 15. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| SSRF / internal network access | Critical | §7 step 9, test matrix, no tenant stdio |
| Prompt injection through tool results | High | Mandatory neutralize + red-team fixtures |
| Small VPS (4 vCPU / 4 GB) overloaded by slow tools | Medium | 30 s cap, per-org concurrency, async mode |
| Too many MCP tools degrade answers | Medium | 20-tool cap, allowlist default = none |
| Secret leakage in logs | High | Sanitize-before-persist + leak test |

## 16. Confidence

- **High** for T1–T3: they extend proven code paths (http_tool, mcp_client, webhooks).
- **Medium** for T4 async/approval UX: it touches the streaming path, where R14 history shows disconnects need care.
- **Assumption:** the Python `mcp` SDK version in the repo supports `streamablehttp_client`; T2 must verify this first and upgrade if not.
