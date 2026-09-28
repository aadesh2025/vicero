# 22 — Implementation Prompt: Paid Plans, Admin Provisioning & Feature Gating

> **This file is the prompt.** Point Claude Code at it and say: *"Execute Phase A1 of
> `docs/22-IMPLEMENTATION-PROMPT.md`."* Run **one phase per invocation**. Do not ask it to run
> A1–A6 in a single go: each phase ends with a commit and a checkpoint you should look at.
>
> **The spec is `docs/22-BILLING-PAID-PLANS.md`.** This file says *how to execute it*; that file
> says *what is true*. Where they disagree, the spec wins and this file is wrong — fix it.

---

## Step 0 — Preflight (every phase, no exceptions)

Do all of this **before writing any code**, and report the results in one short block:

1. Read, in order: `CLAUDE.md` → `docs/22-BILLING-PAID-PLANS.md` (the whole file) → the section
   of it this phase implements.
2. Load the `session-log` skill and read any entry touching `app/core/plans.py`,
   `app/billing/`, `app/modules/admin/` or `app/modules/orgs/`.
3. `git status --short`. **The working tree has ~26 pre-existing modified files that are not
   yours.** Do not stage them, do not revert them, do not "fix" them. Every commit in this track
   is **path-scoped** — `git add -- <explicit paths>`, never `git add .` or `git add -A`.
4. `git log --oneline -3` and confirm which phases already landed.
5. State: the phase you are about to run, the exact files you will touch, and anything in the
   spec you think is wrong. **If something is wrong, stop and say so before building.**

---

## Hard rules for this whole track

| Rule | Why |
|---|---|
| **No payment provider. At all.** No Stripe, no SDK, no checkout, no webhook, no card field. | Plans are granted by staff in the admin panel. Self-serve payment is `docs/23`, deferred. If you reach for a payment library you are off-spec. |
| **Extend, never fork.** `app/core/plans.py`, `app/billing/usage.py`, `app/modules/admin/`, `app/modules/orgs/`, `/billing/upgrade` all exist and work. | A second, parallel plan system is the #1 failure mode for this track. |
| **Every limit lives in `app/core/plans.py`.** Nothing else may contain a number, a plan name comparison, or a price. | The file's own docstring says so and the codebase already honours it. |
| **One error shape:** `plan_limit(feature)` → 402 `{"error":{"code":"plan_limit","message","details":{"feature"}}}`. | The frontend already keys locked states off `details.feature`. |
| **Revoke and downgrade never delete data.** | A client's agents must survive a late payment. There is a test for this. |
| **Path-scoped commits, Conventional Commits, one per task.** | `CLAUDE.md` §2, §3. |
| **Definition of Done per `CLAUDE.md` §2** — typecheck, lint, full suite green, new env vars in `.env.example` **and** `docs/ENV.md`, ADR in `docs/DECISIONS.md`. | Non-negotiable. |
| **Stop and ask** only for: a real blocker you cannot resolve, or a destructive action against real data. Everything else: decide, record it in `docs/DECISIONS.md`, continue. | `CLAUDE.md` §1. |

### Running the checks on this machine

From `apps/api` (Windows paths, per `CLAUDE.md` §12):

```
./.venv/Scripts/python.exe -m ruff check app tests
./.venv/Scripts/python.exe -m mypy app
./.venv/Scripts/python.exe -m pytest -q
```

Docker must be up for DB-backed tests: `cd infra && docker compose up -d postgres redis`.
Web: `cd apps/web && npm run dev -- -p 3001`, `npx tsc --noEmit`, `npm run lint`, `npx vitest run`.

---

## Phase A0 — Plans table — **VERIFY, do not rebuild**

A0 has already been written and committed (`feat(plans): add Starter/Pro/Business paid plans to
the entitlement table`). **Your job is to verify it, not to redo it.**

1. Read `app/core/plans.py` and check it against `docs/22 §4`:
   - `PlanSpec` stores **counts** (`max_workflows`, `max_tools`, `max_webhooks`) and exposes
     `workflows` / `tool_calling` / `n8n` as **derived properties**.
   - `PLANS` has `trial`, `starter`, `pro`, `business`, `legacy`; `_EXPIRED` is not in `PLANS`.
   - `get_entitlements()` computes `plan_expired` from `plan_expires_at`, applies
     `extra_messages`, and still resolves an unknown plan string to `legacy`.
2. Run `ruff`, `mypy`, and `pytest tests/test_plans.py tests/test_plans_paid.py`.
3. Run the **whole** backend suite. A0 changed a load-bearing file; anything it broke shows here.
4. Report: pass/fail per check, and any disagreement with the spec.

**If A0 was reverted** and `app/core/plans.py` still has only `trial` and `legacy`: build it from
`docs/22 §4.2–4.5`, add `tests/test_plans_paid.py` covering every row of the §3 pricing table,
and commit before moving on.

**Known follow-up A0 deliberately did not fix** (it is A2's first task, see below): the trial's
knowledge limits (`max_knowledge_bases=1`, `max_documents=10`, `storage_bytes=100MB`) were chosen
by the implementer, not by the product table. Confirm them with the human or leave as-is.

---

## Phase A1 — Migration & metering

**Read:** `docs/22 §5`, `§5.1`, `§6`, `§7`.

**Build**
1. Migration `0029_paid_plans_admin_grants` (head is `0028_self_serve_trial`):
   - `organizations`: `plan_source`, `plan_expires_at`, `plan_granted_at`, `plan_granted_by`,
     `plan_note` — all nullable or defaulted, **no table rewrite**.
   - New tables: `plan_grants`, `billing_cycles`, `org_storage_usage`.
   - `org_message_usage`: `period_start`, `period_end`, `extra_messages`.
2. Models for the three new tables in `app/models/platform.py`, exported from `app/models/__init__.py`.
3. `app/billing/usage.py`: rewrite `reserve()` to the single atomic roll-and-increment statement
   in **§6** — it must keep returning `None` when blocked. Add `start_period()`.
   **`:limit` is `effective_max_messages` (plan + packs), never `spec.max_messages`.**
4. `app/billing/cycles.py` (new): `open_cycle()`, `mark_paid()`, `waive()`, `payment_state()`.
5. Backfill `org_storage_usage` from existing documents **inside the migration**, and keep it
   current inside the ingestion transaction — not a nightly job.

**Done when**
- A metered org whose `period_end` is in the past answers the next visitor message and shows
  `2 / <limit>`, **with no scheduler running**.
- Packs raise the enforced cap; a new period resets `extra_messages` to 0.
- `payment_state()` returns pending / overdue / paid / waived per §5.1.
- Backfilled storage totals match a direct `COUNT`/`SUM` over the documents table.
- `alembic upgrade head` then `downgrade -1` then `upgrade head` is clean.

**Commit:** `feat(billing): billing period, payment ledger and storage accounting (0029)`

**Stop and ask if:** the backfill would touch more rows than you expect, or an existing row would
lose data.

---

## Phase A2 — Admin API

**Read:** `docs/22 §8`, `§12`.

**FIRST TASK — the landmine A0 found, one line, do it before anything else:**
`app/modules/orgs/schemas.py` has `PlanStatusOut.status: Literal["trial", "trial_expired",
"legacy"]`. The moment a paid plan is granted, `plan_status()` returns `"pro"` and **Pydantic
raises a 500**. Widen it to include `starter`, `pro`, `business`, `plan_expired`. Do the same to
the union in `apps/web/src/lib/api/plan.ts`. Add a test that every value in `PlanStatus`
validates through `PlanStatusOut`.

**Build** — all inside `app/modules/admin/`, all behind the existing `require_staff`:
- Extend `GET /v1/admin/orgs`: plan, status, expiry, messages used/limit, storage, payment state,
  `unanswered_messages`, owner email; filters `?q=`, `?plan=`, `?status=at_limit|near_limit|expiring|expired|payment_pending|overdue`.
- `GET /v1/admin/orgs/{id}` — detail + last 50 `plan_grants` + payment history.
- `POST /v1/admin/orgs/{id}/plan` — grant/change. **Note mandatory. Expiry must be in the future.
  Plan must be in `GRANTABLE_PLANS`. Granting a paid plan restarts the message period and opens a
  billing cycle.** Writes a `plan_grants` row **and** `write_audit`.
- `DELETE /v1/admin/orgs/{id}/plan` — revoke to `plan_expired`, waive open cycles.
- `POST /v1/admin/orgs/{id}/messages` — packs; amount computed from the plan, never from the body.
- `GET /v1/admin/billing/cycles`, `POST .../{id}/paid` (with `renew: bool`), `POST .../{id}/waive`.
- `GET /v1/admin/billing/packs`, `PATCH .../{id}`.

**Done when**
- Every new route 403s for a non-staff user — **extend the existing route-inventory test**.
- Grant → `get_entitlements` differs on the **next request**, no cache, no redeploy.
- **Mark paid & renew** extends expiry by 30 days *and* opens the next cycle as pending.
- A static test proves nothing outside `app/modules/admin/` and `app/billing/` assigns to
  `Organization.plan`, `plan_expires_at` or `extra_messages` (§12 rule 2).

**Commit:** `feat(admin): grant, revoke and bill plans from the admin API`

---

## Phase A3 — Customer read endpoints

**Read:** `docs/22 §8.1`.

Build `GET /v1/billing/plans` (public, rendered from `PLANS`) and `GET /v1/me/entitlements`
(the §8.1 JSON contract exactly).

**Done when** the payload is correct for a trial, an expired trial, each paid plan, a lapsed paid
plan and legacy — and a test asserts `GET /v1/billing/plans` matches `PLANS`, so the pricing table
and the code can never drift.

**Never** include `billing_cycles`, payment state, or amounts in a customer response (§16.3).

**Commit:** `feat(billing): public pricing and per-org entitlements endpoints`

---

## Phase A4 — Enforcement

**Read:** `docs/22 §11` — the matrix is the task list, one row at a time.

Each row: check in the **service layer**, raise `plan_limit(<feature>)`, one test proving a blocked
org gets 402 and an allowed org gets 200.

**The three rows that are easy to get wrong:**
1. **Count what exists**, not what was created — deleting an agent frees a slot.
2. **Downgrade/revoke never deletes.** Business org, 30 agents, revoked → all 30 survive,
   read-only, 31st refused. **Test it.**
3. **A new workspace is created on `trial`, never on the creator's plan.** `Organization.plan`
   defaults to `legacy` = unlimited, so without this a Pro client clicking "New workspace" gets an
   unlimited workspace for free. **This is the most expensive bug in the spec. Test it.**
   Also fix `orgs/service.py:213`, which currently checks `max_workspaces` against
   `SELF_SERVE_PLAN` instead of the user's own plan.

**Commit:** `feat(plans): enforce paid-plan limits across agents, knowledge, channels and automation`

---

## Phase A5 — Admin panel UI

**Read:** `docs/22 §9`.

Extend `apps/web/src/app/(app)/admin/page.tsx` into tabs. Add **Billing & Plans** and **Payments**.
Filter chips, colour-coded plan badges, usage bars (amber 80%, red 100%), change-plan dialog with
a **diff preview** (`Trial → Pro · agents 1 → 10 · n8n OFF → ON · expires 28 Oct`) and the
**☑ Payment already received** checkbox, add-messages dialog with the live invoice line, mark-paid
dialog, org drawer with grant + payment history.

**Done when** Playwright shows: grant Pro to a trial org → that org's n8n panel unlocks without a
redeploy; an unpaid grant lands in **Payment pending** and clears on **Mark paid**.

**Commit:** `feat(admin): billing and payments panel`

---

## Phase A6 — Customer UI

**Read:** `docs/22 §10`.

`/pricing` (public, **CTA is "Contact us", never "Buy"**), `/billing`, extend `/billing/upgrade`,
`useEntitlements()`, `<PlanGate>`, the global 402 → upgrade-modal interceptor, usage meters showing
packs separately.

**`PlanGate` renders locked, never hidden** — disabled children under an overlay naming the plan
that unlocks them, `aria-disabled` and keyboard-focusable, never `display:none`.

**Done when** a Starter org sees a locked n8n panel with an upgrade message — not a 404, not a
missing button — and no customer screen shows payment state.

**Commit:** `feat(web): pricing page, billing page and plan-gated feature states`

---

## Things that must never happen in this track

- A payment SDK, a checkout page, or a webhook endpoint.
- A limit, price or plan name written anywhere but `app/core/plans.py`.
- A second entitlement system beside `get_entitlements()`.
- `git add .` — the tree has unrelated uncommitted work.
- A customer-facing response containing payment state.
- Any org-scoped route writing `Organization.plan`.
- Deleting a client's agents, documents or conversations on downgrade or revoke.
- Leaving the build red at the end of a task.

---

## Report format at the end of every phase

```
PHASE:      A<n> — <name>
COMMIT:     <sha> <subject>
FILES:      <count> changed (list them)
CHECKS:     ruff <pass/fail> · mypy <pass/fail> · pytest <n passed, n failed> · tsc/vitest <...>
SPEC GAPS:  anything in docs/22 that turned out wrong, or that you decided differently (+ ADR id)
NEXT:       the next phase, and anything the human must decide first
```
