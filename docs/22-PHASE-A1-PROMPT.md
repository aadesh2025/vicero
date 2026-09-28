# 22 — Phase A1 side task: migration, billing period, payment ledger, storage accounting

> **Give this whole file to Claude Code.** Say: *"Execute `docs/22-PHASE-A1-PROMPT.md`."*
>
> **This is a parallel track.** Another agent is working on `app/modules/billing/` and
> `app/modules/orgs/schemas.py` at the same time. **The file boundary in §1 is not advice — it is
> the thing that stops the two tracks destroying each other's work.**
>
> **Why this phase runs here and not in the cloud:** it is migration + SQL work. It needs the real
> Postgres in `infra/docker-compose.yml` and the Windows venv to be tested at all. Nobody can
> verify an atomic `UPDATE` or an Alembic downgrade by reading it.

---

## 1. File boundary — absolute

**You may create or modify ONLY these:**

```
apps/api/migrations/versions/0029_paid_plans_admin_grants.py   (new)
apps/api/app/models/platform.py                                (add 3 models, edit 1)
apps/api/app/models/identity.py                                (add 5 columns to Organization)
apps/api/app/models/__init__.py                                (export the new models)
apps/api/app/billing/usage.py                                  (rewrite reserve(), add start_period())
apps/api/app/billing/cycles.py                                 (new)
apps/api/tests/test_billing_period.py                          (new)
apps/api/tests/test_billing_cycles.py                          (new)
apps/api/tests/test_message_metering.py                        (existing — extend only if it breaks)
docs/PROGRESS.md  docs/DECISIONS.md                            (append only)
```

**You must NOT touch, for any reason:**

```
apps/api/app/core/plans.py          ← Phase A0, already committed and green. Read it. Do not edit it.
apps/api/app/modules/**             ← the other track owns modules/billing and modules/orgs
apps/api/app/main.py                ← the other track adds a router line here
apps/web/**                         ← not this phase
```

If this phase seems to need a file outside the allowed list: **stop and report it**. Do not edit
it "just a little".

**Commits are path-scoped.** `git status` shows ~26 modified files that belong to neither track.
Use `git add -- <explicit paths>`. Never `git add .` or `git add -A`.

---

## 2. Before you write anything

1. Read `CLAUDE.md`, then `docs/22-BILLING-PAID-PLANS.md` **§5, §5.1, §6, §7** in full.
2. Read `app/core/plans.py` — especially `Entitlements.effective_max_messages` and
   `MESSAGES_PER_EXCHANGE`. **Every message cap in this phase is `effective_max_messages`
   (plan allowance + packs), never `spec.max_messages`.** A pack that did not raise the enforced
   cap is money taken for nothing.
3. Read `app/billing/usage.py` end to end. `reserve()` / `refund()` / `record_unanswered()` are
   live on the visitor chat path (`chat/inbound.py`) — this is the hottest code you will touch.
4. Load the `session-log` skill; read any entry about `app/billing/` or metering (ADR-088).
5. Bring the stack up: `cd infra && docker compose up -d postgres redis`, wait healthy.
6. Report the current Alembic head (expected `0028_self_serve_trial`) and your plan in ~10 lines.

---

## 3. Build

### 3.1 Migration `0029_paid_plans_admin_grants`

Down-revision `0028_self_serve_trial`. Every column nullable or server-defaulted, so **no table
rewrite that blocks writes**.

```sql
-- organizations: who granted what, and until when (docs/22 §5)
ALTER TABLE organizations ADD COLUMN plan_source     VARCHAR(16) NOT NULL DEFAULT 'system';
ALTER TABLE organizations ADD COLUMN plan_expires_at TIMESTAMPTZ NULL;
ALTER TABLE organizations ADD COLUMN plan_granted_at TIMESTAMPTZ NULL;
ALTER TABLE organizations ADD COLUMN plan_granted_by UUID NULL REFERENCES users(id);
ALTER TABLE organizations ADD COLUMN plan_note       VARCHAR(500) NULL;

-- org_message_usage: the billing window and packs (docs/22 §6)
ALTER TABLE org_message_usage ADD COLUMN period_start   TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE org_message_usage ADD COLUMN period_end     TIMESTAMPTZ NULL;   -- NULL = never rolls
ALTER TABLE org_message_usage ADD COLUMN extra_messages INTEGER NOT NULL DEFAULT 0;

-- grant history: append-only billing evidence (docs/22 §5)
CREATE TABLE plan_grants (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    action           VARCHAR(16) NOT NULL,   -- granted|extended|revoked|pack_added|payment_marked
    from_plan        VARCHAR(32) NULL,
    to_plan          VARCHAR(32) NULL,
    expires_at       TIMESTAMPTZ NULL,
    extra_messages   INTEGER NULL,
    amount_usd_cents INTEGER NULL,           -- recorded, NEVER charged
    note             VARCHAR(500) NULL,
    actor_id         UUID NULL REFERENCES users(id),
    invoiced         BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_plan_grants_org ON plan_grants (organization_id, created_at DESC);

-- payment ledger: one row per 30-day cycle (docs/22 §5.1)
CREATE TABLE billing_cycles (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    plan             VARCHAR(32) NOT NULL,
    period_start     TIMESTAMPTZ NOT NULL,
    period_end       TIMESTAMPTZ NOT NULL,
    amount_usd_cents INTEGER NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'pending',   -- pending|paid|waived
    paid_at          TIMESTAMPTZ NULL,
    method           VARCHAR(32)  NULL,
    reference        VARCHAR(120) NULL,
    note             VARCHAR(500) NULL,
    marked_by        UUID NULL REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_billing_cycles_org    ON billing_cycles (organization_id, period_start DESC);
CREATE INDEX ix_billing_cycles_status ON billing_cycles (status, period_end);

-- storage accounting, same shape as org_message_usage
CREATE TABLE org_storage_usage (
    organization_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    bytes_used      BIGINT  NOT NULL DEFAULT 0,
    documents_count INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Then **backfill `org_storage_usage` inside the same migration** from the existing documents
table. Find the real table and size column by reading `app/models/` — do not guess a name. If no
byte size is stored anywhere, insert rows with `documents_count` only, set `bytes_used = 0`, and
**say so in your report** so the human knows storage enforcement starts from zero.

`downgrade()` must drop all of it cleanly.

### 3.2 Models

Three new models in `app/models/platform.py` next to `OrgMessageUsage`, matching its style
(docstring saying *why* the table exists). Add the five columns to `Organization` in
`app/models/identity.py`. Export everything from `app/models/__init__.py`.

`plan_source` values: `system` | `trial` | `admin` | `stripe` (last one reserved for docs/23).

### 3.3 `usage.reserve()` — the atomic roll-and-increment

This replaces the current statement. It must **keep the existing contract: returns `None` when
blocked, the new total when allowed.** Callers on the chat path depend on that exactly.

```sql
UPDATE org_message_usage
   SET messages_used  = CASE WHEN period_end IS NOT NULL AND period_end <= :now
                             THEN :n ELSE messages_used + :n END,
       extra_messages = CASE WHEN period_end IS NOT NULL AND period_end <= :now
                             THEN 0 ELSE extra_messages END,
       period_start   = CASE WHEN period_end IS NOT NULL AND period_end <= :now
                             THEN :now ELSE period_start END,
       period_end     = CASE WHEN period_end IS NOT NULL AND period_end <= :now
                             THEN :now + INTERVAL '30 days' ELSE period_end END
 WHERE organization_id = :org_id
   AND ( (period_end IS NOT NULL AND period_end <= :now)
         OR messages_used + :n <= :limit )
RETURNING messages_used;
```

- `:limit` is **`effective_max_messages`**. Pass it in; do not recompute a cap inside this module.
- A stale window always admits the reservation — that is the roll.
- **Lazy rollover, never a cron.** A scheduler that dies would leave paying customers blocked;
  this is the same reasoning `plans.py` already uses for computed expiry. Do not add a Celery beat
  job for this.
- `trial` and `legacy` keep `period_end = NULL` → behaviour byte-identical to today.

Add `start_period(session, org_id, days=30)`: sets `period_start = now`, `period_end = now + days`,
`messages_used = 0`, `extra_messages = 0`, creating the row if absent. Called when a paid plan is
granted.

Also add `add_extra_messages(session, org_id, n)` — a single atomic `UPDATE … SET extra_messages =
extra_messages + :n`.

### 3.4 `app/billing/cycles.py` (new)

```python
async def open_cycle(session, org_id, *, plan, days=30, paid=False,
                     method=None, reference=None, note=None, marked_by=None) -> BillingCycle
async def mark_paid(session, cycle_id, *, method, reference=None, note=None,
                    marked_by, renew=False) -> BillingCycle
async def waive(session, cycle_id, *, note, marked_by) -> BillingCycle
async def current_cycle(session, org_id) -> BillingCycle | None
def payment_state(cycle, now) -> Literal["paid", "pending", "overdue", "waived"]
```

- `amount_usd_cents` comes from `PLANS[plan].price_usd_month * 100` **at cycle open**, copied onto
  the row so a later price change never rewrites history.
- `payment_state` is **computed**, never a stored flag:
  `paid`/`waived` pass through; otherwise `overdue` if `now >= period_end`, else `pending`.
- `mark_paid(renew=True)` must also extend `Organization.plan_expires_at` by 30 days **and** open
  the next cycle as `pending`. One transaction.
- **Payment status must not gate access anywhere.** Access is decided by `plan_expires_at` alone.
  If you find yourself reading `billing_cycles` from an access check, stop — that is wrong
  (docs/22 §5.1 rule 1).
- Every state change writes a `plan_grants` row **and** calls
  `write_audit(session, org_id, actor_id, "billing.<action>", …)`. Two records on purpose.

---

## 4. Definition of Done

`CLAUDE.md` §2 in full, plus these specific proofs:

| Test | Must show |
|---|---|
| **Lazy rollover** | A metered org at `limit/limit` with `period_end` in the past answers the next visitor message and reads `2 / limit`. **No scheduler running.** |
| **Packs raise the cap** | Org at 10,000/10,000 blocked; `add_extra_messages(1500)` → replies again; `messages_remaining == 1500`. |
| **Packs do not carry over** | After a rollover, `extra_messages == 0`. |
| **Trial unchanged** | A `trial` org (`period_end IS NULL`) behaves exactly as before — run the existing `test_message_metering.py` untouched. |
| **Concurrency** | Two concurrent `reserve()` calls at the boundary cannot both win. One statement, no read-then-write. |
| **`payment_state`** | pending → overdue at `period_end`; paid and waived pass through. |
| **Mark paid & renew** | `plan_expires_at` +30 days **and** a new `pending` cycle exists, in one transaction. |
| **Migration round-trip** | `alembic upgrade head` → `downgrade -1` → `upgrade head`, clean. |
| **Backfill** | `org_storage_usage` totals match a direct `COUNT`/`SUM` over the documents table. |

Run the **whole** backend suite, not just the new files — `usage.py` is on the visitor chat path.

**Commit (path-scoped):**
```
feat(billing): billing period, payment ledger and storage accounting (0029)
```

---

## 5. Stop and ask the human if

- The documents table has no byte-size column (storage enforcement would start from zero).
- The backfill would touch far more rows than the org count suggests.
- An existing test fails in a way that looks like a **real** behaviour change on the chat path
  rather than a fixture needing an update.
- You believe a file outside §1 must change.

Do **not** stop for: naming choices, index decisions, test structure. Decide, write it in
`docs/DECISIONS.md`, keep going.

---

## 6. Report when done

```
PHASE:     A1 — migration, billing period, payment ledger, storage
COMMIT:    <sha> <subject>
MIGRATION: 0029 up/down verified · backfilled <n> orgs, <n> docs, <n> bytes
CHECKS:    ruff <> · mypy <> · pytest <n passed / n failed>
CHAT PATH: existing metering tests <pass/fail>
DECISIONS: <ADR ids added>
BLOCKED:   <anything needing the human>
```
