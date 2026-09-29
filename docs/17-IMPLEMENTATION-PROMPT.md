# docs/17-IMPLEMENTATION-PROMPT.md — Master prompt: Agentic Runtime, Builder & Integration SDK

> This is a **master prompt**, in the same family as `docs/11-SAFETY-IMPLEMENTATION-PROMPT.md`.
> It is meant to be handed to a Claude Code session **by name**, when the human explicitly
> wants to start this track. Do not self-trigger this from `docs/08-PHASES.md`'s autonomous
> loop — see CLAUDE.md §10b.

## Step 0 — Study before you build (mandatory, do not skip)

Before writing a single line of implementation code:

1. Read `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` in full. It is the spec for everything in
   this prompt — data model, security rules, phase order, and the open questions in its §12.
2. Clone and actually read the source of all three reference repositories. Do not rely on
   READMEs alone — go into the files named in `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §1:
   - `https://github.com/firecrawl/open-agent-builder` — the visual builder / node-graph /
     approval-node / MCP-registry pattern.
   - `https://github.com/FoundationAgents/OpenManus` — the think→act→observe agentic loop
     and generic MCP client pattern (`app/agent/manus.py`, `app/agent/toolcall.py`,
     `app/tool/mcp.py`, `app/flow/`).
   - `https://github.com/botpress/botpress` — the typed `IntegrationDefinition` contract and
     versioned CLI-deploy pattern (`packages/sdk`, `packages/cli`, any folder under
     `integrations/`). Remember: this repo is the Cloud SDK/Hub, not the chatbot engine —
     don't go looking for orchestration logic that isn't there.
3. For each repo, write one short paragraph in `docs/DECISIONS.md` (new ADR, next available
   number) stating: what pattern you are borrowing, what you are explicitly leaving behind
   (vendor lock-in, missing production concerns, etc.), and how it maps onto Vicero's
   existing stack (Postgres/SQLAlchemy/Alembic, Celery/Redis, FastAPI, `app/core/rbac.py`,
   the existing guardrail pipeline in `app/chat/`). This is the same discipline
   `docs/11-SAFETY-GUARDRAILS.md`'s ADRs already follow — a decision without a written reason
   is not traceable six months later.
4. **Stop and ask the human** the five open questions in
   `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §12 before starting Phase 1 implementation:
   - MCP server allowlist policy (opt-in only vs. platform-vetted defaults)
   - Default budget numbers (`max_steps`, `max_tool_calls`, `max_runtime_s`, `max_cost_usd`)
   - Sandbox provider for future code execution (E2B vs. self-hosted) — blocks Phase 2's
     Transform node, not Phase 1, but worth deciding early
   - Whether Phase 5 (Integration SDK) is in scope for v1 at all
   - Rollout policy: enable Phase 1's loop on the live `aurozenai` agent immediately, or hold
     for Phase 3's regression gate first
   Do not guess at these and proceed — they are genuine product/risk decisions, not
   implementation details. This matches CLAUDE.md §1's own carve-out: stop only for things
   you cannot resolve yourself, and these five qualify.

## Step 1 — Execute phases in order, one at a time

Follow `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §7 exactly: **Phase 1 → Phase 2 → Phase 3 →
Phase 4 → Phase 5.** Do not start a phase before the previous one's Definition of Done
(below) is fully green. This mirrors the A.1 → B → C → D → E → F → G discipline of the
safety track — skipping ahead there previously caused a measured regression, and the same
risk applies here since Phase 3 (testing) exists specifically to catch what Phase 1/2 miss.

### Phase 1 — Agentic Runtime — Definition of Done
- [ ] Bounded multi-step tool loop live in `app/chat/runtime.py`, gated behind a platform
      **and** per-org flag, off by default.
- [ ] Generic MCP client (stdio + SSE), org-scoped credentials, never resolved against the
      wrong tenant (same rule as ADR-055/047/048).
- [ ] Every tool result passes through `neutralize_injections()` / `wrap_untrusted()` before
      re-entering model context — write a test that proves this for at least: an MCP tool
      result, an n8n tool result, and a sub-agent result.
- [ ] `agent_steps` table + migration, storing sanitized input/output only.
- [ ] Budget enforcement (`max_steps`/`max_tool_calls`/`max_runtime_s`/`max_cost_usd`),
      inherited (never reset) across nested/sub-agent calls — write a test with a nested
      call that proves the parent budget is decremented, not bypassed.
- [ ] New red-team fixtures added to the Phase D corpus (`docs/11`) covering tool-result
      injection specifically; corpus is green.
- [ ] Full existing test suite still green (`pytest`, `vitest`) — a change this close to
      `app/chat/` has broken things before; do not assume isolation.
- [ ] `docs/PROGRESS.md` updated, git tagged `agentic-phase-1-complete`.

### Phase 2 — Visual Workflow Builder — Definition of Done
- [ ] `workflows` / `workflow_versions` / `workflow_runs` / `workflow_steps` tables, migration
      `0022`+, following the exact schema in `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §3.
- [ ] Celery-driven node execution; node progress published through the existing Redis
      pub/sub hub, delivered over the existing WS/SSE transport (no new real-time stack).
- [ ] React Flow canvas in `apps/web`, CORE node types + n8n/MCP tool nodes only (per §7's
      scoped node list — do not build the full catalog in this phase).
- [ ] Approval node routes through the existing `Handoff` model (ADR-057) — a workflow
      blocked on approval must show up in the same attention queue as a conversation handoff,
      not a separate inbox.
- [ ] Pause/resume backed by `workflow_runs.status`, resumable after a process restart (i.e.
      state lives in Postgres, not Python memory — write a test that kills the worker mid-run
      and confirms resume still works).
- [ ] `WORKFLOWS_WRITE` / `WORKFLOWS_PUBLISH` RBAC permissions added per §9, following the
      exact separation rationale already in `app/core/rbac.py`.
- [ ] Playwright check for: build a 3-node workflow, run it, see live per-node status.
- [ ] Full suite green, `docs/PROGRESS.md` updated, tag `agentic-phase-2-complete`.

### Phase 3 — Agent Testing — Definition of Done
- [ ] `agent_tests` / `agent_test_runs` tables + API (`POST /v1/agents/{id}/tests`, etc.).
- [ ] Regression runner defaults to cached/replayed model responses; a separate,
      explicitly-triggered "live" tier exists for release gating only — verify it does NOT
      run automatically on every draft save (this is the exact failure mode that already
      exhausted the Groq free tier once during manual checklist runs).
- [ ] Publish gate: `WORKFLOWS_PUBLISH`/`AGENTS_PUBLISH` blocked when the latest test run has
      failures; write a test proving publish is rejected in that state.
- [ ] Full suite green, tag `agentic-phase-3-complete`.

### Phase 4 — Version / Approval Workflow — Definition of Done
- [ ] `workflow_versions` gets the same draft → submit-review → publish → rollback flow
      already shipped for `AgentVersion` (2026-07-29 log entry) — reuse the pattern, do not
      invent a second one.
- [ ] Diff view covers: node/edge changes, tool/MCP-server changes, variable schema changes.
- [ ] Full suite green, tag `agentic-phase-4-complete`.

### Phase 5 — Integration SDK — Definition of Done
- [ ] Only start this phase if the human confirmed it's in scope (Step 0, question 4) **and**
      Phases 1–4 have been running against at least one real client workflow without a
      Sev-1/Sev-2 incident.
- [ ] `IntegrationDefinition` typed contract per §8; one existing channel (pick the smallest,
      e.g. Telegram) migrated to implement it as a proof, not a big-bang migration of all
      channels.
- [ ] `bf init` / `bf deploy` CLI, private-to-org by default, explicit `--visibility public`
      flag mirroring Botpress's model — no actual public Hub UI in this phase.
- [ ] Full suite green, tag `agentic-phase-5-complete`.

## Step 2 — Standing rules that apply across all five phases

- Every task still meets CLAUDE.md §2's Definition of Done (typecheck, lint, tests, commit,
  env vars documented) — this track does not get a lighter bar.
- Every new endpoint is org-scoped and RBAC-gated (CLAUDE.md §8) — no exceptions for
  "internal-only" endpoints; workflows and MCP servers are tenant data like everything else.
- Never let a tool result, workflow node output, or sub-agent result skip the security
  pipeline in `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md` §6 — this is the single rule most
  likely to be violated by accident under time pressure, and it is the one that matters most.
- If a phase surfaces a design question not already answered in `docs/17-AGENTIC-RUNTIME-
  AND-BUILDER.md` or Step 0's five questions, record the decision in `docs/DECISIONS.md` with
  reasoning — do not silently pick an option and move on, and do not stop and ask for
  something you can reasonably decide yourself (CLAUDE.md §1's own bar for what counts as a
  hard blocker still applies here).
