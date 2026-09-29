# 22 — Phase A2 side task: the admin API (grant, revoke, packs, payment ledger)

> **Give this whole file to Claude Code.** Say: *"Execute `docs/22-PHASE-A2-PROMPT.md`."*
>
> **Parallel track.** Another agent owns `app/modules/billing/` and is building
> `GET /v1/me/entitlements` there right now. The file boundary in §1 is what stops the two
> tracks overwriting each other.
>
> **Phase A1 is done** (commit `3b78256`) and both of its reported blockers are closed
> (commit `4aabae0`): the chat path now reserves against `effective_max_messages`, and
> `EXPECTED_TABLES` knows about the three new tables. Build on that, do not redo it.

---

## 1. File boundary — absolute

**You may create or modify ONLY these:**

```
apps/api/app/modules/admin/router.py      (add routes)
apps/api/app/modules/admin/service.py     (add functions)
apps/api/app/modules/admin/schemas.py     (add schemas)
apps/api/app/modules/admin/deps.py        (only if a new dependency is genuinely needed)
apps/api/tests/test_admin_billing.py      (new)
apps/api/tests/test_admin_routes_staff_only.py  (extend if it already exists — see §4)
docs/PROGRESS.md  docs/DECISIONS.md       (append only)
```

**You must NOT touch:**

```
apps/api/app/modules/billing/**   ← the other agent is writing this file-by-file RIGHT NOW
apps/api/app/modules/orgs/**      ← its schemas were already fixed in d8ced48
apps/api/app/core/plans.py        ← Phase A0, committed and green
apps/api/app/billing/**           ← Phase A1, committed and green. Call it; do not edit it.
apps/api/app/chat/**              ← fixed in 4aabae0; CLAUDE.md §10a gates it anyway
apps/api/app/main.py              ← the admin router is already registered
apps/web/**                       ← that is Phase A5
```

If this phase seems to need a file outside the allowed list: **stop and report it.** That is
exactly what A1 did, and it was the right call.

**Commits are path-scoped.** ~26 modified files in the tree belong to neither track.
`git add -- <explicit paths>` only. Never `git add .`.

---

## 2. Before you write anything

1. Read `CLAUDE.md`, then `docs/22-BILLING-PAID-PLANS.md` **§5, §5.1, §7, §8.2, §12, §16** in full.
2. Read what already exists and will do the work for you:
   - `app/core/plans.py` — `GRANTABLE_PLANS`, `PAID_PLANS`, `get_entitlements()`
   - `app/billing/usage.py` — `start_period()`, `add_extra_messages()`, `load_entitlements()`
   - `app/billing/cycles.py` — `open_cycle()`, `mark_paid()`, `waive()`, `current_cycle()`,
     `payment_state()`
   - `app/modules/admin/` — the existing `require_staff` pattern, all 8 current routes
   - `app/core/audit.py` — `write_audit()` signature
3. Load the `session-log` skill; read anything about `app/modules/admin/`.
4. Bring the stack up: `cd infra && docker compose up -d postgres redis`.
5. Report your plan in ~10 lines, naming which A1 function each new route will call.
   **If you find yourself about to reimplement something A1 already built, stop.**

---

## 3. Build — all under `/v1/admin`, all behind the existing `require_staff`

| Method | Path | Notes |
|---|---|---|
| `GET` | `/orgs` | **Extend the existing route.** Add plan, status, `plan_expires_at`, messages used/limit, storage used/limit, payment state, `unanswered_messages`, owner email. Filters: `?q=` (name or owner email), `?plan=`, `?status=at_limit\|near_limit\|expiring\|expired\|payment_pending\|overdue`. |
| `GET` | `/orgs/{org_id}` | Detail + last 50 `plan_grants` + payment history. |
| `POST` | `/orgs/{org_id}/plan` | Grant / change. Body `{plan, expires_at?, note, payment_received, method?, reference?}`. |
| `DELETE` | `/orgs/{org_id}/plan` | Revoke to **`plan_expired`** (docs/22 §16.1). Waive open cycles. Body `{note}`. |
| `POST` | `/orgs/{org_id}/messages` | Packs. Body `{packs, note}`. |
| `GET` | `/billing/cycles` | `?status=pending\|overdue\|paid` — the collections list. |
| `POST` | `/billing/cycles/{id}/paid` | Body `{method, reference?, note?, renew: bool}`. |
| `POST` | `/billing/cycles/{id}/waive` | Body `{note}`. |
| `GET` | `/billing/packs` | Uninvoiced packs. |
| `PATCH` | `/billing/packs/{id}` | `{invoiced: true}`. |

### The rules that are easy to miss — all of them are in §8.2, repeated because they cost money

1. **`note` is mandatory** on grant and revoke. Empty or whitespace ⇒ 400. Billing evidence with
   no reason is not evidence.
2. **`expires_at` must be in the future** ⇒ 400 otherwise. A past date silently grants nothing.
3. **`plan` must be in `GRANTABLE_PLANS`** ⇒ 400 otherwise. A typo must never reach the column.
4. **Granting a paid plan calls `usage.start_period(...)`** — otherwise a client upgrading on day
   28 of a trial gets a window that dies in two days.
5. **Granting a paid plan opens a billing cycle** via `cycles.open_cycle(..., paid=payment_received)`.
   `payment_received=true` is the normal case: they pay, then you grant.
6. **Pack amounts are computed from the org's plan**, never taken from the request body. The
   client says how many packs; the server decides what that costs.
7. **`mark_paid(renew=True)`** extends `plan_expires_at` by 30 days **and** opens the next cycle
   as pending — one transaction.
8. **Every mutation writes a `plan_grants` row AND `write_audit(...)`** with the actor and IP.
9. **Payment status must never gate access.** If you find yourself reading `billing_cycles` from
   anything that decides what an org may do, that is wrong (docs/22 §5.1 rule 1).
10. **Revoke must not delete anything.** It changes a plan; agents, documents and conversations
    all survive, read-only.

---

## 4. Definition of Done

`CLAUDE.md` §2 in full, plus:

| Test | Must show |
|---|---|
| **Staff-only** | Every new route 403s for a normal user. Find the existing route-inventory test and **extend it** so it covers the new routes automatically rather than listing them by hand. |
| **Grant takes effect immediately** | Grant Pro to a trial org ⇒ the very next `get_entitlements` call differs. No cache, no restart. |
| **Note required / expiry in the past / unknown plan** | 400, and **nothing written** — no partial grant, no orphan cycle. |
| **Grant opens a cycle** | `payment_received=false` ⇒ cycle `pending`; `true` ⇒ `paid` with method and reference recorded. |
| **Mark paid & renew** | `plan_expires_at` +30 days **and** a new pending cycle, atomically. |
| **Packs** | Org at its cap ⇒ blocked; add 2 packs ⇒ replies again; amount recorded = plan rate × packs. |
| **Revoke keeps data** | Business org with 30 agents revoked ⇒ all 30 still exist, read-only, 31st refused 402. |
| **Audit trail** | Every mutation leaves both a `plan_grants` row and an `audit_logs` row naming the staff actor. |
| **No access path reads payment state** | A static check: nothing outside `app/modules/admin/` imports `billing.cycles`. |

Run the **whole** backend suite. Two failures are known and not yours:
`test_no_new_package_import_cycle` (documented baseline) — and nothing else should fail.

**Commit (path-scoped):** `feat(admin): grant, revoke and bill plans from the admin API`

---

## 5. Stop and ask if

- A route needs a file outside §1.
- The existing `GET /orgs` response shape is consumed somewhere you would have to change.
- A test fails in a way that looks like a real behaviour change rather than a fixture needing
  an update.

Do **not** stop for: naming, response-field ordering, test structure. Decide, record it in
`docs/DECISIONS.md`, keep going.

---

## 6. Report when done

```
PHASE:     A2 — admin API
COMMIT:    <sha> <subject>
ROUTES:    <n> added, <n> extended
CHECKS:    ruff <> · mypy <> · pytest <n passed / n failed>
STAFF GATE: route-inventory test covers <n> admin routes, all 403 for a normal user
DECISIONS: <ADR ids>
BLOCKED:   <anything outside the boundary, with the one-line fix you would have made>
```
