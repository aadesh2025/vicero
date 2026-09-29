# docs/17 — Agentic Runtime, Visual Workflow Builder & Integration SDK

> Status: **spec. Read-always, execute-on-request** — see CLAUDE.md §10b. This is NOT part
> of the §1 autonomous phase contract. Do not start Phase 1 of this track on your own
> initiative; it is triggered by name, the same way `docs/11-SAFETY-GUARDRAILS.md` is.

## 0. Why this doc exists

Vicero today (per `docs/PROGRESS.md` and the CLAUDE.md session log) is a mature,
production-hardened multi-tenant chatbot SaaS: 900+ backend tests, a 7-layer prompt-safety
stack (`docs/11-SAFETY-GUARDRAILS.md`, Phases A–G), measured hybrid RAG retrieval
(`docs/13-AI-COOKBOOK-REVIEW.md`, `docs/14-KNOWLEDGE-PIPELINE-V2.md`), and 6+ live channels.

What it does **not** have:

1. **A multi-step agentic tool loop.** One visitor turn today = one retrieval + one
   generation + at most one bound n8n tool call. There is no loop where the model can call a
   tool, read the result, and decide to call a second tool before answering.
2. **An in-app visual workflow/agent builder.** All non-chat automation lives in an external
   n8n instance, outside Vicero's own DB, auth, and guardrail pipeline.
3. **A pluggable integration contract.** WhatsApp/Instagram/Messenger/Telegram are each a
   bespoke hardcoded module in `apps/api`; there is no typed, versioned contract a new
   integration (first-party or third-party) could implement.

This track closes those three gaps **as layers on top of the existing architecture** —
Postgres + SQLAlchemy + Alembic, Celery + Redis, FastAPI, the existing RBAC model
(`app/core/rbac.py`), and — non-negotiably — the existing safety pipeline. It does **not**
replace n8n, does **not** adopt Convex/Clerk/LangGraph, and does **not** attempt to clone
Botpress's closed-source engine.

## 1. Read the reference repos BEFORE writing any code

**This is a hard prerequisite for Phase 1, not optional background reading.** Each repo
below teaches exactly one pattern this track needs. Clone each one into a scratch directory,
read the actual source (not just the README), and write a short note in `docs/DECISIONS.md`
for each pattern you plan to borrow and each thing you are deliberately leaving behind.
**Extract the pattern. Never port the vendor-locked plumbing.**

### 1.1 `https://github.com/firecrawl/open-agent-builder` — the visual builder pattern

What to study:
- The node type system: `Start`, `Agent`, `MCP Tool`, `Transform`, `If/Else`, `While Loop`,
  `User Approval`, `End`. Read how each node's config schema and runtime behavior are
  defined — this maps directly onto Vicero's `workflow_steps.node_type` /
  `node_config` design in §3 below.
- The **execution engine**: it runs on LangGraph's `StateGraph` for conditional routing,
  loops, and human-in-the-loop interrupts. Vicero will NOT adopt LangGraph — study the
  *shape* of the state machine (what a "paused on approval" state looks like, how a loop
  node re-enters) and reimplement it against Celery + Postgres (`workflow_runs.status`,
  `workflow_steps` rows) instead.
- How execution state streams to the UI (Convex reactivity, in their case). Vicero already
  has a Redis pub/sub hub (shipped 2026-07-20, used for WS delivery) — study *what events*
  they stream per node (started/completed/failed/waiting-for-approval) and reuse that event
  shape over Vicero's existing WS/SSE infrastructure, not Convex.
- The MCP Tool node and Settings → MCP Registry: how a user registers a custom MCP server,
  how the tool list is discovered and tested. Note their real limitation: MCP tool-calling
  only works natively with Anthropic models today — Vicero's MCP client (§4) must not
  inherit that limitation; make it provider-agnostic from day one.
- The User Approval node — study exactly what it blocks on and how resumption works. Map
  this onto Vicero's existing `Handoff` / attention-queue model (ADR-057) rather than
  building a second human-in-the-loop primitive.

Explicitly do NOT bring in: Convex, Clerk, Next.js 16 canary, E2B (evaluate that separately,
later, per §7 Phase 2 sandboxing note), or their Firecrawl-specific nodes.

### 1.2 `https://github.com/FoundationAgents/OpenManus` — the agentic loop pattern

What to study, file by file:
- `app/agent/manus.py` — the `Manus` agent class. Read `think()` closely: it is the
  ReAct-style decide-next-action step, called in a loop from the base `ToolCallAgent`.
  Note `max_steps: int = 20` and `max_observe: int = 10000` — the only two budget controls
  in the reference. Vicero's version needs more (§5 — inherited, per-tenant, cost-based).
- `app/agent/toolcall.py` (the base class `Manus` extends) — this is where the actual
  think→act→observe loop lives. Read how a tool call result re-enters `self.memory` before
  the next `think()` call — this is the exact insertion point where Vicero must route the
  result through `wrap_untrusted()` / `neutralize_injections()` before it becomes visible to
  the model again (§6, non-negotiable).
- `app/tool/mcp.py` (`MCPClients`, `MCPClientTool`) — the generic MCP client, supporting
  both `stdio` and `sse` transports, connecting to servers declared in `config.toml`. This is
  the direct model for Vicero's MCP tool provider (§4).
- `app/tool/browser_use_tool.py`, `app/tool/python_execute.py`, `app/tool/ask_human.py` —
  read these to understand the *tool interface shape* (a tool is a name + schema + async
  `execute()`), not to copy them. **Do not port `BrowserUseTool` or `PythonExecute` in
  Phase 1** — both are separately-gated, higher-risk capabilities (see §7 "What not to add
  yet").
- `app/flow/` (`run_flow.py`, multi-agent flow, the `DataAnalysis` agent) — study how a
  sub-agent is invoked from within a parent agent's loop. This is where the budget
  inheritance rule in §5.3 comes from: OpenManus does **not** enforce a nested budget, and
  that gap is exactly what Vicero must not repeat.

Note honestly: OpenManus has zero production concerns — no auth, no multi-tenancy, no
persistence beyond in-memory `Memory`, no guardrails. It is a single-user CLI. Take the loop
and the MCP client shape; take nothing else.

### 1.3 `https://github.com/botpress/botpress` — the integration contract pattern

**Correction to keep in mind while reading:** this repository is Botpress Cloud's public
integration SDK/Hub/CLI monorepo (`packages/sdk`, `packages/cli`, `integrations/`,
`interfaces/`, `bots/`). It is **not** the chatbot orchestration engine — that is closed-
source and runs only as Botpress's hosted Cloud product. Do not go looking for RAG,
conversation orchestration, or safety logic here; it isn't in this repo.

What to study:
- Any folder under `integrations/` — pick two or three (e.g. an email or CRM integration)
  and read `integration.definition.ts` (the typed contract: `actions`, `events`, `configuration`
  schema, `identifier`/auth requirements) alongside `src/index.ts` (the implementation). This
  pair is the direct model for Vicero's `IntegrationDefinition` (§8).
- `packages/sdk` — how the typed contract is authored once and consumed by both the CLI and
  the runtime.
- `packages/cli` (`bp init`, `bp deploy`, `bp deploy --visibility public`) — the versioned,
  private-by-default-then-optionally-published deploy model. This maps to `bf init` /
  `bf deploy` in §8, deferred to Phase 5.
- `interfaces/` — the shared-contract concept (e.g. any channel integration implements a
  common messaging interface) so core logic can target the interface, not each channel SDK.
  Vicero's WhatsApp/Instagram/Messenger/Telegram modules should eventually converge on one
  interface this way — but that refactor is Phase 5, not Phase 1.

## 2. Non-negotiable rules (carried over from docs/11, apply here without exception)

1. **A prompt line is not enforcement.** Every rule in this doc that matters must be a
   code-level check, not an instruction to the model. (docs/11 §"Two standing rules".)
2. **Every tool result is untrusted input.** It passes through the existing
   `neutralize_injections()` / `wrap_untrusted()` path (currently used for RAG chunks and
   n8n tool output) before re-entering model context. A new tool-calling loop that bypasses
   this reopens the exact OWASP LLM01 hole Phase A of the safety track closed. No exceptions
   for MCP tools, workflow nodes, or sub-agent results.
3. **Budgets are inherited, never reset.** If a workflow or agent step spawns a sub-agent or
   nested tool call, it consumes from the **parent turn's remaining** `max_steps` /
   `max_tool_calls` / `max_cost`, never a fresh allowance. (OpenManus does not do this — see
   §1.2. This is where Vicero must improve on the reference, not copy it.)
4. **Traces store sanitized data only.** The `agent_steps` / `tool_calls` table (§3) never
   persists a raw tool result — it stores the post-guard, post-redaction version, following
   the same rule as `documents.pii_flags` (nullable: never-scanned vs. scanned-clean vs.
   scanned-flagged) rather than storing raw PII "for debugging."
5. **Re-run the Phase D corpus, extended.** Before Phase 1 ships to any real agent, add new
   fixtures to the red-team corpus (`docs/11-SAFETY-GUARDRAILS.md` Phase D) covering
   tool-call injection specifically: a malicious instruction embedded inside a *tool result*
   (not a RAG chunk or the user's own message) trying to redirect the next tool call. That
   corpus has never been tested against a multi-step loop — assume it has gaps, don't assume
   coverage.

## 3. Data model (Postgres, via Alembic — next migration is `0022`)

```
workflows
├── id, organization_id, agent_id (nullable — a workflow can be agent-scoped or standalone)
├── name, description
├── created_by, created_at, updated_at

workflow_versions
├── id, workflow_id, version (int, monotonic per workflow)
├── graph (jsonb — {nodes: [...], edges: [...]}, see §3.1 for node schema)
├── status (draft | in_review | published | archived)  -- mirrors AgentVersion's draft/published split
├── created_by, created_at

workflow_runs
├── id, workflow_version_id, organization_id, conversation_id (nullable)
├── status (running | paused_approval | completed | failed | cancelled | budget_exceeded)
├── variables (jsonb — the workflow's runtime variable bag, see §3.2)
├── budget (jsonb — {max_steps, max_tool_calls, max_runtime_s, max_cost_usd, consumed: {...}})
├── started_at, completed_at, error

workflow_steps
├── id, workflow_run_id, node_id (references graph node), node_type
├── status (pending | running | completed | failed | skipped | awaiting_approval)
├── input (jsonb, sanitized), output (jsonb, sanitized)
├── latency_ms, cost_usd, error
├── started_at, completed_at

agent_steps          -- the per-turn agentic-loop trace (Phase 1), separate from workflow_steps
├── id, conversation_id, message_id, organization_id
├── step_index (0, 1, 2, ... within the turn)
├── kind (think | tool_call | observe | final_answer)
├── tool_name (nullable), tool_input (jsonb, sanitized), tool_output (jsonb, sanitized)
├── latency_ms, tokens_in, tokens_out, cost_usd, status, error
├── created_at
```

### 3.1 Node schema (graph stored in `workflow_versions.graph`)

```json
{
  "nodes": [
    { "id": "start", "type": "start", "config": {} },
    { "id": "agent1", "type": "agent", "config": { "agent_id": "..." } },
    { "id": "cond1", "type": "condition", "config": { "expression": "intent == 'booking'" } },
    { "id": "tool1", "type": "tool", "config": { "provider": "mcp", "server_id": "...", "tool": "getAvailability" } },
    { "id": "approve1", "type": "approval", "config": { "message": "Send this email?" } }
  ],
  "edges": [
    { "source": "start", "target": "agent1" },
    { "source": "agent1", "target": "cond1" },
    { "source": "cond1", "target": "tool1", "condition": "true" }
  ]
}
```

Node type catalog for Phase 2 (do not build all of these in one pass — see §7 phase split):

```
CORE:  Start · End · Agent · Message · Condition · Switch
       · Set Variable · Get Variable · Transform · Delay · Loop
AI:    LLM · Knowledge Search · Classifier · Summarizer · Sub-Agent
TOOLS: n8n · HTTP · MCP · Built-in Tool · OAuth Integration
HUMAN: Approval · Human Handoff
```

`Sub-Agent` nodes and any nested loop MUST decrement the parent `workflow_runs.budget`, per
rule §2.3 — this is enforced in code, not left to the node's own accounting.

### 3.2 Workflow variables

A flat namespaced key-value bag on `workflow_runs.variables`, e.g. `user.email`,
`appointment.date`, `lead.score`, `workflow.current_step`. `Set Variable` / `Get Variable`
nodes read/write it; `Condition`/`Switch` nodes evaluate expressions against it. Values that
look like PII (email/phone — reuse `app/chat/pii.py`'s `classify_contact()`) are subject to
the same redaction-on-egress rule as chat output before they can appear in a node's rendered
output back to a visitor.

## 4. MCP tool provider (Phase 1)

```
              Tool Registry
                    │
       ┌────────────┼─────────────┐
       ↓            ↓             ↓
     Built-in       n8n           MCP
                                   │
                     stdio / SSE transport, per §1.2
```

- n8n is unchanged and remains Vicero's automation/enterprise-integration surface per
  CLAUDE.md §6. MCP is a **new, separate** tool provider, not a replacement.
- MCP server registration is org-scoped: a new `mcp_servers` table
  (`id, organization_id, name, transport, url_or_command, auth_config (encrypted), enabled`).
  Credentials follow the exact rule ADR-055 established for guard models and ADR-047/048 for
  provider keys: **never** resolve a platform-wide MCP server against an org's own
  credentials or vice versa — each server's auth is scoped to the org that registered it.
- Tool discovery + a "test connection" action in the API (mirrors open-agent-builder's
  Settings → MCP Registry), surfaced in the UI in Phase 2, usable via API alone in Phase 1.

## 5. Agentic loop budgets (Phase 1)

```
Defaults (all overridable per-org, same pattern as Organization.guard_injection_enabled):
  max_steps        = 5      (think→act→observe cycles per turn)
  max_tool_calls    = 5
  max_runtime_s      = 30
  max_cost_usd       = 0.05  (per turn)
```

- On exceeding any limit: stop the loop, fall back to a direct answer from whatever context
  has been gathered so far (never hang the turn — this mirrors the existing
  `fallback_message` pattern from ADR-044), and log a `agent_budget_exceeded` structured
  event with the org id, workflow/agent id, and which limit tripped.
- Cost accounting reuses the existing per-token pricing catalog (`app/llm/catalog.py`).
  Where a model or tool has no published price, the trace and any dashboard MUST show a
  `pricing_unknown` flag — never default to `$0` (this exact silent-wrong-number mistake was
  already made once with unpublished LLM pricing; do not repeat it here).
- Nested/sub-agent budget consumption per rule §2.3.

## 6. Security architecture (mandatory read before Phase 1)

```
Input
 ↓
Tenant resolution → Auth/RBAC (new permissions, §9)
 ↓
Input safety (L0–L1, existing)
 ↓
Agent think step
 ↓
RAG (existing, unchanged)
 ↓
Tool selection → Tool permission check (org has this tool enabled?)
 ↓
Tool execution (n8n / MCP / built-in)
 ↓
UNTRUSTED RESULT → neutralize_injections() / wrap_untrusted()  ← MANDATORY, no bypass
 ↓
Back into agent loop (think again, or finalize)
 ↓
Output guard (L5, existing)
 ↓
Response to visitor
```

Never: `Tool → raw result → LLM`. If a future PR adds a tool integration that skips the
UNTRUSTED step, that is a merge-blocking defect, not a style nit.

## 7. Phased build order

Each phase has its own "definition of done" on top of CLAUDE.md §2. **Do not start a phase
before the previous one is green and its tests pass** — same discipline as docs/11's
A.1 → B → C → D → E → F → G ordering.

### Phase 1 — Agentic Runtime
- Multi-step tool loop in `app/chat/runtime.py` (bounded per §5).
- Generic MCP client (`app/tools/mcp_client.py`, modeled on §1.2's `MCPClients`).
- `agent_steps` table + trace persistence (sanitized only, per §2.4).
- Security validation: every tool result path audited against §6's diagram.
- New/extended red-team fixtures for tool-call injection (§2.5) — must be green before this
  phase is considered done, not treated as a follow-up.
- Feature flag: platform-wide **and** per-org, off by default (same pattern as Phase G web
  access / `guard_injection_enabled`).

### Phase 2 — Visual Workflow Builder
- `workflows` / `workflow_versions` / `workflow_runs` / `workflow_steps` tables (migration
  `0022`+).
- Node execution engine on Celery; node-progress events published through the existing Redis
  pub/sub hub (§1.1) to the existing WS/SSE transport — no new real-time layer.
- Canvas UI (React Flow, in `apps/web`) — pure frontend addition, no new backend framework.
- Approval node wired to the existing `Handoff` model (ADR-057), not a new primitive.
- Pause/resume: a workflow blocked on approval persists `workflow_runs.status =
  'paused_approval'` and resumes from Celery on the existing handoff-resolution path.
- Only the CORE + a minimal TOOLS subset (n8n, MCP) node types ship in this phase — defer
  Classifier/Summarizer/Sub-Agent to a follow-up once the engine itself is proven.

### Phase 3 — Agent Testing
- Test case model (`agent_tests`: input, expected tool calls, expected behavior, expected
  final-answer shape) + `agent_test_runs`.
- Regression suite runner, default mode uses **cached/replayed model responses**, not live
  calls — the live guard-model corpus already exhausted the Groq free tier once
  (2026-08-10 log entry) running only 61 manual probes; an automated suite firing on every
  publish must not repeat that failure. A separate, explicitly-triggered "live smoke" tier
  is fine for release gating, not for every draft save.
- Publish gate: `AGENTS_PUBLISH` (or a new `WORKFLOWS_PUBLISH`, §9) is blocked if the latest
  test run has failures, mirroring the existing draft/published split.

### Phase 4 — Version / Approval workflow
- Reuses the existing draft → submit-review → publish → rollback shape already built for
  `AgentVersion` (2026-07-29 log entry); extend it to `workflow_versions`.
- Diff view: system prompt / model / tools / RAG / workflow-graph changes between versions.

### Phase 5 — Integration SDK
- `IntegrationDefinition` typed contract (§8), only after Phases 1–4 are stable in
  production with at least one real client workflow running. This is an ecosystem
  investment, not a prerequisite for the rest — do not pull it forward.

## 8. Integration SDK shape (Phase 5 — reference only, not built yet)

```
IntegrationDefinition
├── name, version
├── auth (oauth2 | api_key | none, + config schema)
├── config_schema (jsonb schema for org-level settings)
├── actions (typed: name, input schema, output schema)
├── events (typed: name, payload schema)
└── webhooks (inbound signature verification requirements)
```

WhatsApp/Instagram/Messenger/Telegram/Gmail/Calendar/CRM connectors become implementations
of this contract over time — do not force a big-bang migration of the existing hardcoded
channel modules; new integrations use the contract first, existing ones migrate opportunistically.

`bf init` / `bf deploy` (private-to-org by default, explicit `--visibility public` to
publish to a future Hub) is deferred until there is real third-party developer demand.

## 9. New RBAC permissions

```
WORKFLOWS_WRITE    -- create/edit workflow drafts, run in test mode
WORKFLOWS_PUBLISH   -- publish a workflow version / roll back
TOOLS_MANAGE (existing) -- extend to cover MCP server registration
```

Follow the exact `AGENTS_WRITE` / `AGENTS_PUBLISH` split rationale already documented in
`app/core/rbac.py`: editing is reversible and private, publishing changes what a live agent
does — keep those two capabilities separate for workflows too.

## 10. API surface (Phase-gated — do not build ahead of the phase that needs it)

```
# Phase 2
POST   /v1/agents/{agent_id}/workflows
GET    /v1/agents/{agent_id}/workflows
GET    /v1/workflows/{id}
PATCH  /v1/workflows/{id}
DELETE /v1/workflows/{id}
POST   /v1/workflows/{id}/versions
GET    /v1/workflows/{id}/versions
POST   /v1/workflows/{id}/versions/{version}/publish
POST   /v1/workflow-runs/{id}/resume
POST   /v1/workflow-runs/{id}/cancel
GET    /v1/workflow-runs/{id}/steps

# Phase 3
POST   /v1/agents/{id}/tests
POST   /v1/agents/{id}/tests/run
GET    /v1/agent-test-runs/{id}

# Phase 1 (backend-only, no UI required yet)
POST   /v1/mcp/servers
GET    /v1/mcp/servers
POST   /v1/mcp/servers/{id}/test-connection
```

Every endpoint: org-scoped, RBAC-gated per §9, follows the existing typed
request/response + `{error: {code, message, details}}` convention (CLAUDE.md §8).

## 11. Explicitly out of scope for this track (see also CLAUDE.md §7 "What not to add yet")

- Full public integration marketplace/Hub UI — Phase 5 ships the contract only.
- Unrestricted autonomous browser tool (OpenManus's `BrowserUseTool`) — a materially larger
  attack surface than tool-calling; if ever built, it is its own future doc with its own
  red-team pass, same as Phase G's domain-allowlisted web access was.
- Arbitrary Python code execution — if a `Transform` node needs real code execution, it must
  run in an isolated sandbox (E2B, gVisor, or Firecracker — pick one, evaluate separately)
  with **no access to platform credentials**, and is deferred past Phase 2's initial ship.
- Billing/cost pass-through to clients — `max_cost_usd` in §5 is a safety rail, not a
  billing feature; Stripe integration is still the deferred Phase 18 per `docs/08-PHASES.md`.
- Replacing Postgres, Redis/Celery, or the existing RAG pipeline with anything from the
  reference repos.

## 12. Open questions for the human (answer before or during Phase 1 — do not guess)

1. **MCP server allowlist policy** — should MCP servers be enabled per-org opt-in only (like
   `guard_injection_enabled`), or does the platform want to offer a small set of
   pre-vetted/first-party MCP servers (e.g. a Google Calendar MCP) out of the box?
2. **Default budget numbers** (§5) — are `max_steps=5` / `max_cost_usd=0.05`/turn reasonable
   for the target client base, or should these be lower for the free/trial tier and
   configurable up for paid orgs?
3. **Sandbox provider for the Transform node's code execution** (§11) — E2B (hosted, adds a
   billed external dependency) vs. self-hosted gVisor/Firecracker (more ops burden, no
   per-call cost)? This decision blocks Phase 2's Transform node, not Phase 1.
4. **Is the Integration SDK (Phase 5) actually wanted for v1**, or should Phases 1–4 ship and
   prove themselves with real clients before any SDK/CLI work starts, per §7's sequencing?
5. **Per-org feature flag rollout** — should Phase 1's agentic loop be enabled for the
   existing live `aurozenai` agent immediately once it ships, or held back until Phase 3's
   test suite can regression-gate it first?
