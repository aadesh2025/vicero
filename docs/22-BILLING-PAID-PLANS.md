# 22 — Paid Plans, Admin Provisioning & Feature Gating

> **Status: spec. Read-always, execute-on-request** — like `docs/11-*` and `docs/17-*`, this file
> is **outside the §1 autonomous contract** in `CLAUDE.md`. It changes who can use what, for
> customers who have paid real money. Do not start Phase A0 on your own initiative. Execute a
> phase only when asked for it by name.
>
> **Read before touching** `app/core/plans.py`, `app/billing/`, `app/modules/admin/`, or any
> service that raises `plan_limit`.
>
> **This EXTENDS `docs/18-SELF-SERVE-PLAN.md`. It does not replace it.** The trial system, the
> entitlement engine and the enforcement points already exist and already work. Building a second
> plan system beside them is the single biggest failure mode for this phase.
>
> **NO PAYMENT INTEGRATION IS IN SCOPE.** No Stripe, no checkout page, no webhook, no card data.
> Self-serve payment is specified separately in `docs/23-STRIPE-SELF-SERVE-BILLING.md` and is
> **deferred**. If a task in this file makes you reach for a payment SDK, you are off-spec.

---

## 0. One-liner

Add three paid plans — **Starter $49 / Pro $99 / Business $199 per month** — that are **granted by
platform staff from the admin panel**, enforced by the entitlement engine that already exists,
with every out-of-plan feature showing a **locked state with an upgrade message**; plus paid
**extra-message packs** for customers who exceed their monthly conversation limit.

---

## 1. The operating model — read this first, it explains every design choice

BotForge is being sold **hand-to-hand to agency clients**, not self-serve. The money never touches
the application:

```
1. Client talks to you   →  2. You agree a plan  →  3. Client pays you OUTSIDE the app
                                                       (bank transfer / invoice / UPI / whatever)
                                                              │
                                                              ▼
5. Client's features    ←  4. YOU open /admin, find their workspace,
   unlock instantly           and grant the plan
```

**Consequences that shape the whole build:**

| Because… | The design does this |
|---|---|
| You grant plans by hand | The **admin panel is the product surface** for billing, not a checkout page |
| The app never sees a payment | There is **no source of truth for "has paid"** inside the system — your grant *is* the truth. So every grant is **audit-logged with who, when, and why** |
| You might grant for one month | Grants support an **optional expiry date**, and an expired grant falls back safely |
| A client may stop paying | You need a **one-click revoke** that never deletes their data |
| Clients will exceed message limits | **Extra-message packs** are granted the same way, and the panel tells you **how much to invoice** |
| You will forget who owes what | The panel tracks **uninvoiced packs** and **orgs that hit their cap** |

**The honest limitation, stated once:** with no payment integration, nothing technical stops you
from forgetting to revoke a non-paying client. The audit log and the "expiring soon" list in §9
are the mitigation. That is an acceptable trade at your scale and is exactly why expiry dates are
in the model from day one rather than bolted on later.

---

## 2. What already exists — verified in the repo, DO NOT REBUILD

Read from the code on 2026-09-28. Re-verify before starting; if anything changed, fix this section
first and record why in `docs/DECISIONS.md`.

| Thing | Where | State |
|---|---|---|
| Entitlement engine | `apps/api/app/core/plans.py` | **Exists.** `PLANS`, `PlanSpec`, `Entitlements`, `get_entitlements()`, `plan_limit()`. Holds `trial` + `legacy` only. |
| One-place-for-limits rule | `app/core/plans.py` docstring | **Exists and is load-bearing.** No other module may contain a plan limit. |
| Message counter | `app/billing/usage.py` + `org_message_usage` | **Exists.** Atomic `reserve()` / `refund()` / `record_unanswered()`. **No billing period** — see §6. |
| Enforcement helpers | `app/billing/usage.py` | `require_feature`, `feature_allowed`, `require_agents_writable`, `require_new_agent_slot`, `playground_allowed`, `require_verified_email_to_go_live`. |
| Enforcement points | agents / workflows / tools / n8n / channels / orgs services, `chat/inbound.py` | **Exist** (docs/18 §2.3). New limits use the same pattern. |
| Error envelope | `plan_limit()` → **402** `{"error":{"code":"plan_limit","message":…,"details":{"feature":…}}}` | **Exists.** The frontend already keys locked states off `details.feature`. Do not invent a second shape. |
| Admin API | `app/modules/admin/` → `/v1/admin` with `require_staff` on **every** route | **Exists:** `GET /orgs`, `/users`, `/usage`, `/health`, `/automations`, `/n8n-signature-audit`, `GET+PUT /feature-flags`. **Extend this module.** |
| Admin UI | `apps/web/src/app/(app)/admin/page.tsx` | **Exists**, single page. Extend into tabs. |
| Audit helper | `app/core/audit.py` → `write_audit(session, org_id, actor_id, action, *, target_type, target_id, meta, ip)` | **Exists.** Every grant/revoke uses it. |
| `Organization.plan` | `app/models/identity.py` | `String(32)`, default **`legacy`** (unlimited) — a **deliberate fail-safe**. `trial_started_at` / `trial_ends_at` exist. |
| `Subscription` model | `app/models/platform.py` | Exists, unused, Stripe-shaped. **Leave it alone** — docs/23 uses it. |
| Upgrade page | `apps/web/src/app/(app)/billing/upgrade/page.tsx` | **Exists.** Extend; do not create a parallel page. |
| Migration head | `migrations/versions/0028_self_serve_trial.py` | Next is **`0029_paid_plans_admin_grants`**. |

---

## 3. The pricing table — single source of truth

`app/core/plans.py` is the only place these numbers may appear in code. This table and that file
must always agree, and a test asserts it (§14).

| | **Starter** | **Pro** | **Business** |
|---|---:|---:|---:|
| **Price (USD / month)** | **$49** | **$99** | **$199** |
| **Workspaces** | 1 | 2 | 5 |
| AI Agents | 3 | 10 | 30 |
| Messages / month | 2,000 | 10,000 | 30,000 |
| **Extra messages** | **$6 per 500** | **$5 per 500** | **$4 per 500** |
| Knowledge Bases | 1 | 5 | 20 |
| Knowledge Storage | 500 MB | 5 GB | 10 GB |
| Documents | 20 | 100 | 500 |
| Channels | Web Chat only | Web + WhatsApp + Instagram + Facebook | All supported channels |
| RAG / Knowledge search | ✓ | ✓ | ✓ |
| Tool Calling | — | 8 tools | Unlimited |
| n8n Workflows | — | 10 | Unlimited |
| Webhooks | — | 5 | Unlimited |
| API | Read-only | Full REST | Full + higher limits |
| Analytics | Basic | Advanced | Advanced + Export |
| Team Members | 1 | 5 | 15 |
| Remove branding | — | ✓ | ✓ |
| Support | Email | Priority | Priority |

**Rules that ship with the table**

1. **A visitor message + a bot reply = 2 messages.** `MESSAGES_PER_EXCHANGE` in `plans.py` already
   encodes this. Do not redefine it. Say it in plain words on the pricing page.
2. **Storage is the hard limit; document count is a soft guide.** A 500-page PDF and a 2-page PDF
   must not consume the same quota. Enforce bytes; show both numbers.
3. **Messages reset every 30-day billing period**, not lifetime (unlike `trial`). §6.
4. **Third-party provider costs are the customer's.** BotForge charges for the platform; Meta /
   WhatsApp messaging and other external providers bill the customer through their own connected
   accounts. The channel-connect screen must say this.
5. **Extra messages are sold in packs of 500** at the plan's rate, granted by admin, invoiced by
   you outside the app (§7).
6. **BYOK (customer's own model API key)** is allowed on every plan and does **not** change the
   message limit in this phase. Say so on the pricing page so it isn't a support surprise.
7. **Every limit above is PER WORKSPACE, and every workspace is billed separately.** The
   *Workspaces* row is how many workspaces that client may **own**, not a pool they share. A Pro
   client with 2 workspaces pays **$99 × 2** and each workspace has its own 10,000 messages.
   This is enforced structurally: `Organization` *is* the workspace and the plan lives on it, so
   a second workspace has its own plan row.
   **Critical rule:** a workspace created by a client beyond their first is created on **`trial`**,
   never on the owner's paid plan. Without this, a Pro client clicks "New workspace" and — because
   `Organization.plan` defaults to `legacy` — silently receives an **unlimited** workspace for
   free. See §11.

---

## 4. `plans.py` — extend, never fork

### 4.1 The compatibility rule

`PlanSpec` today has three booleans (`workflows`, `n8n`, `tool_calling`). Pro needs **counts**
(10 workflows, 8 tools, 5 webhooks). But `Entitlements.allows(feature)` does
`getattr(self.spec, feature)` and ~8 call sites read those names.

> **Store counts. Keep the booleans as derived properties.** Every existing caller keeps working;
> no enforcement point is touched in Phase A0.

Convention for every numeric limit (already used in the file): `0` = **not included**, a positive
integer = **that many**, `None` = **unlimited**.

### 4.2 Extended `PlanSpec`

```python
@dataclass(frozen=True, slots=True)
class PlanSpec:
    # ── existing ──────────────────────────────────────────────────────────
    trial_days: int | None
    max_workspaces: int | None
    max_agents: int | None
    max_messages: int | None          # PER BILLING PERIOD for paid plans (§6)
    playground_per_day: int | None
    publish_needs_verified_email: bool

    # ── counts replacing the three booleans ───────────────────────────────
    max_workflows: int | None         # 0 = feature off
    max_tools: int | None             # 0 = feature off (tool calling)
    max_webhooks: int | None          # 0 = feature off
    n8n_enabled: bool                 # n8n stays a straight on/off

    # ── new in this phase ─────────────────────────────────────────────────
    max_knowledge_bases: int | None
    max_documents: int | None         # soft guide, shown in UI
    storage_bytes: int | None         # HARD limit
    channels: frozenset[str] | None   # None = every supported channel
    max_team_members: int | None
    api_access: Literal["read", "full"]
    analytics: Literal["basic", "advanced", "advanced_export"]
    remove_branding: bool
    support: Literal["email", "priority"]

    # ── commercial metadata: DISPLAY AND INVOICING ONLY ───────────────────
    #: Never used for an access decision. Used by /v1/billing/plans and by the
    #: admin panel to tell you what to invoice for an extra-message pack.
    price_usd_month: int | None
    extra_message_pack_size: int | None      # 500
    extra_message_pack_usd: int | None       # 6 / 5 / 4

    # ── derived: keeps every existing caller working ──────────────────────
    @property
    def workflows(self) -> bool:  return self.max_workflows != 0
    @property
    def tool_calling(self) -> bool:  return self.max_tools != 0
    @property
    def n8n(self) -> bool:  return self.n8n_enabled
```

`FEATURES` grows — these strings are the `details.feature` values the UI keys locked states off,
so they are an **API contract**:

```python
FEATURES = (
    "workflows", "n8n", "tool_calling", "webhooks",
    "agents", "messages", "knowledge_bases", "documents", "storage",
    "channels", "team_members", "api_write", "analytics_advanced",
    "analytics_export", "remove_branding",
)
```

### 4.3 The three new `PLANS` entries

```python
_MB = 1024 * 1024
_GB = 1024 * _MB

PLANS: dict[str, PlanSpec] = {
    "trial":  PlanSpec(...),   # unchanged — docs/18
    "legacy": PlanSpec(...),   # unchanged — unlimited, fail-safe default

    "starter": PlanSpec(
        trial_days=None, max_workspaces=1, max_agents=3,
        max_messages=2_000, playground_per_day=None,
        publish_needs_verified_email=True,
        max_workflows=0, max_tools=0, max_webhooks=0, n8n_enabled=False,
        max_knowledge_bases=1, max_documents=20, storage_bytes=500 * _MB,
        channels=frozenset({"web"}),
        max_team_members=1,
        api_access="read", analytics="basic",
        remove_branding=False, support="email",
        price_usd_month=49, extra_message_pack_size=500, extra_message_pack_usd=6,
    ),
    "pro": PlanSpec(
        trial_days=None, max_workspaces=2, max_agents=10,
        max_messages=10_000, playground_per_day=None,
        publish_needs_verified_email=True,
        max_workflows=10, max_tools=8, max_webhooks=5, n8n_enabled=True,
        max_knowledge_bases=5, max_documents=100, storage_bytes=5 * _GB,
        channels=frozenset({"web", "whatsapp", "instagram", "facebook"}),
        max_team_members=5,
        api_access="full", analytics="advanced",
        remove_branding=True, support="priority",
        price_usd_month=99, extra_message_pack_size=500, extra_message_pack_usd=5,
    ),
    "business": PlanSpec(
        trial_days=None, max_workspaces=5, max_agents=30,
        max_messages=30_000, playground_per_day=None,
        publish_needs_verified_email=True,
        max_workflows=None, max_tools=None, max_webhooks=None, n8n_enabled=True,
        max_knowledge_bases=20, max_documents=500, storage_bytes=10 * _GB,
        channels=None,                    # every supported channel
        max_team_members=15,
        api_access="full", analytics="advanced_export",
        remove_branding=True, support="priority",
        price_usd_month=199, extra_message_pack_size=500, extra_message_pack_usd=4,
    ),
}

PAID_PLANS = ("starter", "pro", "business")
#: Plans an admin may grant from the panel. `legacy` stays grantable for your own/demo orgs.
GRANTABLE_PLANS = PAID_PLANS + ("trial", "legacy")
```

`PlanStatus` becomes:
```python
PlanStatus = Literal["trial", "trial_expired", "legacy",
                     "starter", "pro", "business", "plan_expired"]
```

### 4.4 `get_entitlements()` — two small changes

Signature gains `plan_expires_at: dt.datetime | None = None` and `extra_messages: int = 0`.

1. **Expiry is computed, never stored** — the same rule `trial_expired` already follows, for the
   same reason: a cron that flips a column and then dies leaves a customer either locked out or
   free forever.
   ```python
   if plan in PAID_PLANS and plan_expires_at and now >= plan_expires_at:
       return Entitlements("plan_expired", _EXPIRED, ...)
   ```
2. **Extra messages raise the effective cap** for the current period:
   ```python
   effective_max_messages = None if spec.max_messages is None else spec.max_messages + extra_messages
   ```
   Expose it as `Entitlements.effective_max_messages`; **`reserve()` and every meter use this
   value**, never `spec.max_messages` directly.

**Keep the fail-open rule for unknown plan strings**: `get_entitlements("enterprise")` still
resolves to `legacy`. An unrecognised value is far more likely a future plan than a trial.

### 4.5 New `Entitlements` helpers

```python
@property
def storage_bytes(self) -> int | None: ...
@property
def max_knowledge_bases(self) -> int | None: ...
@property
def max_team_members(self) -> int | None: ...
@property
def effective_max_messages(self) -> int | None: ...
def allows_channel(self, kind: str) -> bool:
    return self.spec.channels is None or kind in self.spec.channels
def limit_for(self, feature: str) -> int | None:
    """Numeric cap for a countable feature, for '3 of 10 used' UI."""
```

---

## 5. The plan grant — data model

Add to `organizations` (all nullable → no table rewrite):

| Column | Type | Meaning |
|---|---|---|
| `plan_source` | `VARCHAR(16) NOT NULL DEFAULT 'system'` | `system` \| `trial` \| `admin` \| `stripe` (reserved for docs/23) |
| `plan_expires_at` | `TIMESTAMPTZ NULL` | `NULL` = until revoked. Past = `plan_expired`. |
| `plan_granted_at` | `TIMESTAMPTZ NULL` | When |
| `plan_granted_by` | `UUID NULL` → `users.id` | Which staff member |
| `plan_note` | `VARCHAR(500) NULL` | **Why** — client name, invoice number, what they paid |

New table — the grant history, because the columns above only hold the *current* state and you
will need to answer "what did we give this client in July?":

```sql
CREATE TABLE plan_grants (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    action           VARCHAR(16) NOT NULL,   -- granted | extended | revoked | pack_added
    from_plan        VARCHAR(32) NULL,
    to_plan          VARCHAR(32) NULL,
    expires_at       TIMESTAMPTZ NULL,
    extra_messages   INTEGER NULL,           -- for pack_added
    amount_usd       INTEGER NULL,           -- what you invoiced, for your records
    note             VARCHAR(500) NULL,
    actor_id         UUID NULL REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_plan_grants_org ON plan_grants (organization_id, created_at DESC);
```

**Rules**
- `plan_grants` is **append-only**. No updates, no deletes — it is your billing evidence.
- Every write also calls the existing `write_audit(...)` with `action="billing.plan_granted"` /
  `"billing.plan_revoked"` / `"billing.pack_added"`. Two records on purpose: `plan_grants` is the
  billing view, `audit_logs` is the security view.
- `amount_usd` is **recorded, never charged**. Nothing in this codebase moves money.

### 5.1 Payment ledger — "paid" vs "payment pending"

**The requirement:** a client is **payment pending** until *you* mark them paid in the admin panel.
The app can never know a bank transfer arrived, so your click is the only source of truth — the
same principle as the plan grant itself.

One row per 30-day cycle per workspace:

```sql
CREATE TABLE billing_cycles (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    plan             VARCHAR(32) NOT NULL,       -- plan at the time the cycle opened
    period_start     TIMESTAMPTZ NOT NULL,
    period_end       TIMESTAMPTZ NOT NULL,
    amount_usd_cents INTEGER NOT NULL,           -- from PlanSpec.price_usd_month × 100
    status           VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | paid | waived
    paid_at          TIMESTAMPTZ NULL,
    method           VARCHAR(32)  NULL,          -- free text: bank transfer, UPI, PayPal…
    reference        VARCHAR(120) NULL,          -- invoice / UTR / transaction ref
    note             VARCHAR(500) NULL,
    marked_by        UUID NULL REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_billing_cycles_org    ON billing_cycles (organization_id, period_start DESC);
CREATE INDEX ix_billing_cycles_status ON billing_cycles (status, period_end);
```

**Lifecycle**

| Moment | What happens |
|---|---|
| You grant a paid plan | A cycle opens: `period_start = now`, `period_end = now + 30 days`, `amount` from the plan, `status = 'pending'`. The grant dialog has a **"Payment already received"** checkbox — tick it and the cycle opens as `paid` in one step (this is the normal case: they pay, then you grant). |
| You click **Mark paid** | `status = 'paid'`, `paid_at`, `method`, `reference`, `marked_by` recorded. A `plan_grants` row (`action='payment_marked'`) and a `write_audit` entry are written. |
| You click **Mark paid & renew** | The above, **plus** `plan_expires_at += 30 days` and the **next cycle opens as `pending`**. This is the monthly renewal button — one click instead of two. |
| Cycle's `period_end` passes while still `pending` | The cycle is **overdue**. It shows red in the panel and in the *Payment pending* filter. |

**Status derivation (computed, never a stored flag):**

```python
def payment_state(cycle, now) -> Literal["paid", "pending", "overdue", "waived"]:
    if cycle.status in ("paid", "waived"):
        return cycle.status
    return "overdue" if now >= cycle.period_end else "pending"
```

**Rules**

1. **Payment status does NOT gate access.** Access is decided by `plan_expires_at` alone (§4.4).
   A `pending` cycle is a note to *you*, not a punishment for the client — if their transfer is
   slow, their bot must not stop mid-conversation. If you want to cut them off, revoke or let the
   expiry lapse; both are deliberate acts.
2. **The client never sees this.** `billing_cycles` is admin-only and appears in no customer API
   response. Per your decision in §16.3, there is no client-facing "payment pending" banner.
3. **`waived`** exists for comps, demos and your own workspaces — so a free org does not sit in
   your overdue list forever.
4. `amount_usd_cents` is copied from the plan at cycle open, so a later price change never
   rewrites history.
5. **Append-only in spirit:** a cycle may go `pending → paid/waived` and record its payment
   details, but is never deleted. Corrections are a new note, not an edit of the past.

---

## 6. Billing period & message reset

**The problem:** `org_message_usage.messages_used` is a **lifetime** counter — correct for a 10-day
trial, wrong for a $49/month plan where 2,000 messages must reset each period.

**The rule: roll the window lazily, never with a cron.** Same philosophy as computed expiry.

Add to `org_message_usage`:

```
period_start     TIMESTAMPTZ NOT NULL DEFAULT now()
period_end       TIMESTAMPTZ NULL      -- NULL = never rolls (trial / legacy)
extra_messages   INTEGER NOT NULL DEFAULT 0   -- packs bought THIS period
```

`reserve()` becomes one atomic statement that rolls a stale window **and** increments, keeping the
existing contract (`None` returned ⇒ blocked):

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
   AND ( (period_end IS NOT NULL AND period_end <= :now)          -- fresh window always fits
         OR messages_used + :n <= :limit )                        -- :limit = plan + extra_messages
RETURNING messages_used;
```

- `:limit` is **`effective_max_messages`** (plan cap + packs), never the raw plan cap.
- **Packs do not carry over.** A new period resets `extra_messages` to 0. Say this on the pricing
  page and in the admin panel's pack dialog, or you will have an argument with a client.
- Granting a paid plan sets `period_start = now()`, `period_end = now() + 30 days`.
- `trial` and `legacy` keep `period_end = NULL` → behaviour identical to today.

**Test that pins this:** a Pro org at 10,000/10,000 with `period_end` in the past answers the next
visitor message and shows `2 / 10,000` — with **no scheduler running**.

### 6.1 What happens at the limit

Reuse the trial-expired mechanics exactly (docs/18 §2.3) — do not invent a second behaviour:

| Who | What they see |
|---|---|
| Visitor | Bot stops replying. **No error text** — the visitor is never told the business ran out of credit. |
| Inbox | The message is still saved and parked in the handoff queue with `reason = plan_limit`, so a human can answer. |
| Org owner | Dashboard banner: *"Message limit reached (10,000 / 10,000). Contact us to add more messages."* |
| You | The org appears in the admin panel's **"At or near limit"** list (§9) with its `unanswered_messages` count — that number is your sales argument for the pack. |

---

## 7. Extra-message packs (the overage money)

**How it works end to end**

1. Client hits the cap → their dashboard tells them to contact you → the org shows up in your
   admin "At limit" list.
2. You agree a pack: **500 messages at the plan's rate** ($6 Starter / $5 Pro / $4 Business).
3. Client pays you outside the app.
4. You open the org in `/admin`, click **Add messages**, pick a quantity (1 pack = 500), and the
   dialog shows **"3 packs × 500 = 1,500 messages · invoice $15.00"** — computed from
   `extra_message_pack_usd`, never typed by hand.
5. `extra_messages += 1500`, a `plan_grants` row with `action='pack_added'` and
   `amount_usd=1500` (cents) is written, and the bot starts replying again **immediately**.

**Rules**
- Packs apply to the **current period only** (§6).
- The pack price is read from the org's **current plan's** spec at grant time and stored on the
  grant row, so later price changes never rewrite history.
- The admin panel has an **Uninvoiced packs** view (`plan_grants` where `action='pack_added'`) so
  you can reconcile what you have actually billed. Mark-as-invoiced is a flag on the row.
- **No auto-granting.** A pack is always a deliberate staff action; there is no code path that
  raises a limit on its own.

---

## 8. API

### 8.1 New public / user endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/billing/plans` | public | The pricing table rendered from `PLANS`, including `price_usd_month` and pack rates. **The marketing page must not hardcode numbers.** |
| `GET` | `/v1/me/entitlements` | user | What this org may do right now + usage. The frontend's single source of truth. |

`GET /v1/me/entitlements` response contract:

```jsonc
{
  "plan": "pro",
  "status": "pro",                       // pro|starter|business|trial|trial_expired|plan_expired|legacy
  "plan_expires_at": "2026-10-28T00:00:00Z",   // null = no expiry
  "limits": {                            // null = unlimited
    "agents": 10, "messages": 10000, "knowledge_bases": 5, "documents": 100,
    "storage_bytes": 5368709120, "workflows": 10, "tools": 8, "webhooks": 5,
    "team_members": 5
  },
  "usage": {
    "agents": 3, "messages": 1240, "knowledge_bases": 2, "documents": 37,
    "storage_bytes": 411041792, "workflows": 4, "tools": 2, "webhooks": 1,
    "team_members": 2
  },
  "messages": {
    "used": 1240, "plan_limit": 10000, "extra": 0, "effective_limit": 10000,
    "period_end": "2026-10-28T00:00:00Z", "unanswered": 0
  },
  "features": {
    "n8n": true, "tool_calling": true, "workflows": true, "webhooks": true,
    "api_write": true, "analytics_advanced": true, "analytics_export": false,
    "remove_branding": true
  },
  "channels": ["web", "whatsapp", "instagram", "facebook"],   // null = all
  "contact_url": "/billing"
}
```

**This endpoint is for rendering, not security.** Every limit is *also* enforced server-side at the
mutation. A client that lies about its entitlements still gets a 402.

### 8.2 New admin endpoints — `app/modules/admin/`, all behind `require_staff`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/orgs` | **Extend the existing route**: add `plan`, `status`, `plan_expires_at`, messages used/limit, storage used/limit, `unanswered_messages`, owner email. Query params: `?q=` search, `?plan=`, `?status=at_limit\|expiring\|expired`. |
| `GET` | `/v1/admin/orgs/{org_id}` | Full detail + last 50 `plan_grants` rows. |
| `POST` | `/v1/admin/orgs/{org_id}/plan` | **Grant or change a plan.** Body: `{plan, expires_at?, note}`. |
| `DELETE` | `/v1/admin/orgs/{org_id}/plan` | **Revoke.** Body: `{note}`. Always lands on **`plan_expired`** (§16.1) — read-only, bot stops, data intact. Open cycles are closed as `waived`. |
| `POST` | `/v1/admin/orgs/{org_id}/messages` | **Add packs.** Body: `{packs, note}` → computes messages + amount from the org's plan. |
| `GET` | `/v1/admin/billing/cycles` | Payment ledger. `?status=pending\|overdue\|paid` — **this is your collections list**. |
| `POST` | `/v1/admin/billing/cycles/{id}/paid` | **Mark paid.** Body: `{method, reference?, note?, renew: bool}`. `renew: true` = "Mark paid & renew": extends `plan_expires_at` by 30 days and opens the next cycle as `pending`. |
| `POST` | `/v1/admin/billing/cycles/{id}/waive` | Comps / demos / your own orgs. Body: `{note}`. |
| `GET` | `/v1/admin/billing/packs` | Uninvoiced packs, for reconciliation. |
| `PATCH` | `/v1/admin/billing/packs/{id}` | `{invoiced: true}`. |

Grant handler, with every rule that matters:

```python
@router.post("/orgs/{org_id}/plan", response_model=schemas.OrgAdminOut)
async def grant_plan(
    org_id: uuid.UUID,
    body: schemas.GrantPlanIn,                 # {plan, expires_at?, note, payment_received, method?, reference?}
    request: Request,
    staff: User = Depends(require_staff),      # already exists — do not reimplement
    session: AsyncSession = Depends(get_session),
):
    if body.plan not in GRANTABLE_PLANS:
        raise AppError("invalid_plan", f"Unknown plan {body.plan!r}.", 400)
    if body.expires_at and body.expires_at <= dt.datetime.now(dt.UTC):
        raise AppError("invalid_expiry", "Expiry must be in the future.", 400)
    if not body.note or not body.note.strip():
        raise AppError("note_required", "Say why — client name or invoice ref.", 400)

    org = await _get_org(session, org_id)      # 404 if missing
    previous = org.plan

    org.plan            = body.plan
    org.plan_source     = "admin"
    org.plan_expires_at = body.expires_at
    org.plan_granted_at = dt.datetime.now(dt.UTC)
    org.plan_granted_by = staff.id
    org.plan_note       = body.note.strip()

    if body.plan in PAID_PLANS:
        await usage.start_period(session, org_id, days=30)   # resets counter + window (§6)
        await billing.open_cycle(                            # payment ledger (§5.1)
            session, org_id, plan=body.plan, days=30,
            paid=body.payment_received,                      # the normal case: paid, then granted
            method=body.method, reference=body.reference, marked_by=staff.id,
        )

    session.add(PlanGrant(
        organization_id=org_id, action="granted", from_plan=previous,
        to_plan=body.plan, expires_at=body.expires_at,
        note=body.note.strip(), actor_id=staff.id,
    ))
    await write_audit(
        session, org_id, staff.id, "billing.plan_granted",
        target_type="organization", target_id=str(org_id),
        meta={"from": previous, "to": body.plan,
              "expires_at": body.expires_at.isoformat() if body.expires_at else None},
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return schemas.OrgAdminOut.from_org(org)
```

**Non-obvious rules encoded above — keep all of them:**
- **`note` is mandatory.** Six months from now you will not remember why an org is on Business.
- **Expiry must be in the future** — a past date would silently grant nothing.
- **Granting a paid plan restarts the message period.** Otherwise a client who upgrades on day 28
  of a trial gets a window that expires in two days.
- **Revoke never deletes data** (§11 rule 2).
- The change takes effect on the client's **very next request** — `get_entitlements()` reads the
  org row every time, there is no cached plan to invalidate. That is what "immediately" means.

---

## 9. Admin panel UI

Extend `apps/web/src/app/(app)/admin/page.tsx` into tabs. **New tab: "Billing & Plans".**

### 9.1 Workspaces table (the main view)

| Column | Notes |
|---|---|
| Workspace | name + owner email |
| Plan | colour-coded badge: trial (grey) · starter (blue) · pro (violet) · business (gold) · expired (red) |
| Status | `active` · `expires in 5 days` · `EXPIRED` · `AT LIMIT` |
| Messages | `8,412 / 10,000` + progress bar; bar turns amber at 80%, red at 100% |
| Storage | `1.2 GB / 5 GB` |
| **Payment** | **`PAID` (green) · `PENDING` (amber) · `OVERDUE` (red) · `WAIVED` (grey)** — the current cycle's state (§5.1) |
| Granted | date + which staff member |
| Note | the client/invoice ref, truncated |
| Actions | **Change plan** · **Mark paid** · **Add messages** · **Revoke** |

Filters across the top, as one-click chips:
- **Payment pending** — granted, not yet marked paid. *This is your collections list.*
- **Overdue** — pending **and** the cycle's 30 days are up. Red. Chase today.
- **At limit** — orgs at 100% messages. *This is your sell-a-pack list.*
- **Near limit (80%+)** — call them before they stop working.
- **Expiring in 7 days** — the renewal is due; take payment, then **Mark paid & renew**.
- **Expired** — should they be revoked or renewed?
- Search by workspace name or owner email.

A header strip shows the month at a glance: **`$594 collected · $198 pending · 2 overdue`**.

### 9.2 Change-plan dialog

- Plan dropdown (Starter / Pro / Business / Trial / Legacy) showing each plan's headline limits, so
  you are not guessing.
- Expiry: **30 days (default, pre-selected)** · **90 days** · **Custom date** · **No expiry**.
  30 days is the default because billing is monthly (§16.2) — a forgotten revoke then costs you
  one month, not forever.
- **☑ Payment already received** — ticked by default, since the normal flow is *they pay, then you
  grant*. Ticking reveals **Method** (bank transfer / UPI / PayPal / other) and **Reference**.
  Unticking opens the cycle as **pending** and the workspace appears in your collections list.
- **Note (required)** — placeholder: *"Acme Corp — paid $99 via bank transfer, invoice INV-004"*.
- A diff preview before confirming: **`Trial → Pro · agents 1 → 10 · messages 500 → 10,000 ·
  n8n OFF → ON · expires 28 Oct 2026`**. Never a bare "are you sure".

### 9.3 Add-messages dialog

- Quantity in **packs** (1 pack = 500 messages), with a live line:
  **`3 packs × 500 = 1,500 messages · invoice $15.00 (Pro rate: $5 / 500)`**
- Warning line, always shown: *"Packs do not carry over to the next period."*
- Note field (invoice ref), optional here but pre-filled from the last one.

### 9.4 Org detail drawer

- Current plan, expiry, who granted it, the note.
- Usage meters: messages (with packs shown separately), storage, agents, KBs, team.
- **Grant history** from `plan_grants` — full audit trail, newest first, with amounts.
- **Payment history** from `billing_cycles` — every 30-day cycle with amount, status, date paid,
  method and reference. This is what you open when a client says *"I already paid for August."*
- `unanswered_messages` count — how much business they lost to the cap, i.e. your upgrade pitch.
- **Other workspaces owned by this client**, with each one's plan and payment state — a Pro client
  with 2 workspaces owes you twice (§3 rule 7), and this is where you see it.

### 9.5 Payments view & the Mark-paid dialog

**Payments tab** — the ledger from `GET /v1/admin/billing/cycles`, grouped by state:

| Group | Shows | Action |
|---|---|---|
| **Overdue** (red, first) | workspace, client, amount, days overdue | Mark paid · Revoke |
| **Pending** | workspace, client, amount, due date | Mark paid |
| **Paid this month** | amount, date, method, reference | — |

**Mark-paid dialog**
- Read-only line: **`Acme Corp · Pro · $99.00 · cycle 28 Sep – 28 Oct`** — the amount comes from
  the plan, never typed, so it cannot disagree with what you charged.
- **Method** (bank transfer / UPI / PayPal / other) · **Reference** (UTR, invoice no.) · **Note**.
- Two buttons:
  - **Mark paid** — records the payment, leaves the expiry alone.
  - **Mark paid & renew** *(primary)* — records it, pushes `plan_expires_at` +30 days, and opens
    the next cycle as pending. **This is the button you press every month.**
- **Waive** is a secondary action for comps, demos and your own workspaces, so free orgs do not
  sit in the overdue list forever.

### 9.6 Access control on the UI

`/admin` is **staff-only**. The existing `require_staff` on the API is the real boundary; the
frontend redirect is convenience only. Keep the existing route-inventory test that asserts **every**
`/v1/admin/*` route 403s for a normal user — and extend it to the new routes.

---

## 10. Customer-facing UI

### 10.1 Pages

| Route | State | Purpose |
|---|---|---|
| `/pricing` | **new**, public | The three plans from `GET /v1/billing/plans`. **CTA is "Contact us", not "Buy"** — `mailto:` / contact form to your work address. No checkout anywhere. |
| `/billing` | **new**, in-app | Current plan, usage meters, message period end, "Need more messages? Contact us". |
| `/billing/upgrade` | **exists — extend** | The three plan cards + the 402 landing target. Keep the route; the trial banner already links here. CTA → contact. |

**The customer never sees billing state (§16.3).** No payment status, no "pending", no amount due,
no expiry countdown — `billing_cycles` appears in **no** customer-facing response. A client who has
already paid you must never see a "payment pending" notice because you haven't clicked the button
yet. The only billing-ish things they see are their **usage meters** and, if their plan lapses or
their messages run out, a neutral banner: *"Contact us to continue."*

### 10.2 Entitlements hook

```ts
// apps/web/src/lib/entitlements.ts
export function useEntitlements() { /* TanStack Query → GET /v1/me/entitlements, 60s stale */ }
export function useFeature(feature: string): {
  allowed: boolean; limit: number | null; used: number; remaining: number | null;
}
```

### 10.3 `PlanGate` — locked, never hidden

Matches the decision already made in docs/18 §2.6 (*"locked (not hidden) states"*). A hidden
feature teaches the user nothing; a locked one sells the upgrade.

```tsx
<PlanGate feature="n8n">
  <N8nPanel />
</PlanGate>
```

Renders children when allowed; otherwise renders them **visually disabled** under an overlay:

> **n8n automations aren't available on Starter.**
> Upgrade to Pro to connect n8n workflows. → **[ See plans ]**

Rules:
- Disabled controls are `aria-disabled` and keyboard-focusable so screen readers announce the
  reason — never `display: none`.
- Countable features show progress: `8 of 10 workflows used`; at 100%: `Workflow limit reached.`
- Storage shows a bar in MB/GB, since that is the hard limit.
- Message meter shows packs separately: `10,240 / 11,500 (1,500 extra)`.

### 10.4 Global 402 handling

In the typed API client, a `402` with `error.code === "plan_limit"` opens an **upgrade modal**
naming `error.details.feature`, instead of the generic error toast. One interceptor covers every
current and future limit.

---

## 11. Enforcement matrix

Same pattern as docs/18 §2.3: the check lives in the **service layer**, raises `plan_limit(...)`,
and there is a test per row proving a blocked org gets 402 and an allowed org gets 200.

| Limit | Enforced in | `details.feature` |
|---|---|---|
| **Workspaces (1/2/5)** | `orgs/service.create_org` — count workspaces the **user owns**, against the `max_workspaces` of their *highest* plan | `workspaces` |
| Agents (3/10/30) | `agents/service.create_agent`, `duplicate_agent` | `agents` |
| Messages / period | `chat/inbound.InboundTurn.events()` via `usage.reserve()` — **already wired**, uses `effective_max_messages` | `messages` |
| Knowledge bases | `knowledge/service.create_knowledge_base` | `knowledge_bases` |
| Documents (soft) | ingestion — warn, block only if storage is also over | `documents` |
| **Storage bytes (hard)** | ingestion, **before** the file is written/embedded; re-check after text extraction | `storage` |
| Channels | `channels/service.set_enabled` + connect flow — `ent.allows_channel(kind)` | `channels` |
| Tool count | `tools/service.create_tool`, MCP create; **runtime** `build_tooling` | `tool_calling` |
| n8n | `tools/service.bind_n8n_workflow`, `list_n8n_workflows` | `n8n` |
| Workflow count | `workflows/service` create/duplicate; **runtime** `_dispatch_run` | `workflows` |
| Webhook endpoints | webhook-endpoint create | `webhooks` |
| Team members | invitation create **and acceptance** (check both, or an org exceeds its cap by pre-inviting) | `team_members` |
| API writes on Starter | API-key auth dependency: `api_access == "read"` ⇒ allow GET, 402 everything else | `api_write` |
| Advanced analytics | analytics router, advanced endpoints | `analytics_advanced` |
| Analytics export | analytics export endpoint | `analytics_export` |
| Widget branding | widget config endpoint — `remove_branding` false ⇒ force branding on, **server-side** | `remove_branding` |

**Two rules that are easy to get wrong**

1. **Count what exists, not what was created.** Limits compare against a live `COUNT(*)` for the
   org, so deleting an agent frees a slot. Guard the races that matter (agents, team members) with
   a locking read or a unique constraint.
2. **A downgrade or revoke must NEVER delete data.** A Business org with 30 agents revoked to
   `plan_expired` keeps all 30 — they become **read-only**, and it cannot create more until it is
   under the cap. Deleting a client's agents because they were late paying is unacceptable and
   unrecoverable. **Put this in a test.**
3. **A new workspace is created on `trial`, never on the creator's plan.** `Organization.plan`
   defaults to `legacy` = **unlimited**, so a Pro client clicking "New workspace" would otherwise
   receive an unlimited workspace for free. `create_org` must set `plan="trial"` explicitly for
   every client-created workspace, and you grant + bill it separately (§3 rule 7).
   **This is the most expensive bug in this spec. Put it in a test.**

---

## 12. Security rules — non-negotiable

1. **Only `is_staff` may grant, change or revoke a plan, or add message packs.** There is no
   self-serve path to a higher plan in this phase. Any route that writes `Organization.plan` lives
   under `/v1/admin` with `require_staff`.
2. **No org-scoped route may ever write `plan`, `plan_expires_at` or `extra_messages`.** Add a test
   that greps the codebase: outside `app/modules/admin/` and `app/billing/`, nothing assigns to
   those attributes.
3. **`is_staff` can never be set by self-signup** — already true (docs/18 §2.4), and already
   pinned by a test. Do not weaken it: staff is the only thing standing between a user and a free
   Business plan.
4. **Every grant/revoke/pack writes both** a `plan_grants` row and a `write_audit` entry, with the
   actor and IP. Append-only.
5. **`note` is mandatory on grant and revoke.** Billing evidence with no reason is not evidence.
6. **The entitlement check is server-side at every mutation.** `/v1/me/entitlements` is a
   rendering convenience; a forged response must change nothing.
7. **Tenant isolation holds here too:** every query in the billing and admin services filters by
   `organization_id`, per `CLAUDE.md` §8.
8. **Fail safe, not open, on staff mistakes:** an unknown plan string still resolves to `legacy`
   (a paying client is never locked out by a typo) — but the admin API **rejects** any plan not in
   `GRANTABLE_PLANS`, so a typo cannot get into the column in the first place.
9. **No secret in a note.** The note field is shown in the admin UI and stored in plain text —
   put an invoice reference in it, never a password or a card number. Say so in the field's helper
   text.

---

## 13. Phases — execute in order, each ends green

`CLAUDE.md` §2 Definition of Done applies to every task: typecheck, lint, tests, Conventional
Commit, env vars documented.

| Phase | Scope | Done when |
|---|---|---|
| **A0** | `plans.py`: extended `PlanSpec` (counts + derived booleans), three paid entries, `FEATURES`, `GRANTABLE_PLANS`, expiry + `extra_messages` in `get_entitlements`, `limit_for`, `allows_channel`, `effective_max_messages`. **No DB, no routers, no UI.** | Unit tests cover every plan × every limit; **every existing test still passes untouched** (proves the compat rule in §4.1 held). |
| **A1** | Migration `0029_paid_plans_admin_grants`: org plan columns, `plan_grants`, **`billing_cycles`**, `org_storage_usage`, `org_message_usage` period + `extra_messages`. `usage.reserve()` rewritten per §6, `usage.start_period()`, `billing.open_cycle()`, storage backfill. | Lazy-rollover test passes with no scheduler; packs raise the cap; `payment_state()` returns pending/overdue/paid correctly; backfill verified against real row counts. |
| **A2** | Admin API: extended `GET /v1/admin/orgs` with filters, org detail, `POST/DELETE /plan`, `POST /messages`, **payment-cycle routes (`/cycles`, `/paid`, `/waive`)**, packs reconciliation. | Every new route 403s for a non-staff user (extend the existing route-inventory test); grant → `get_entitlements` changes on the next request; **Mark paid & renew** extends expiry and opens the next cycle. |
| **A3** | `GET /v1/billing/plans`, `GET /v1/me/entitlements` (§8.1 contract). | Correct payload for trial, expired, each paid plan, and legacy. |
| **A4** | Enforcement for every new limit (§11), including the two easy-to-miss rules. | One 402 test per row + the **"revoke never deletes data"** test. |
| **A5** | Admin panel UI: Billing & Plans tab, **Payments tab**, filters (incl. Pending / Overdue), change-plan dialog with diff preview + payment checkbox, **Mark-paid dialog**, add-messages dialog with live invoice amount, org drawer with grant + payment history. | Playwright: grant Pro to a trial org → that org's n8n panel unlocks **without a redeploy**; an unpaid grant appears under **Payment pending** and clears on **Mark paid**. |
| **A6** | Customer UI: `/pricing`, `/billing`, extended `/billing/upgrade`, `useEntitlements`, `PlanGate`, 402 interceptor, usage meters incl. packs. | Playwright: Starter org sees a locked n8n panel with the upgrade message, not a 404 or a hidden button. |
| **A7** | *Deferred, on request only:* self-serve payment — see `docs/23-STRIPE-SELF-SERVE-BILLING.md`. | — |

---

## 14. Testing

Cases that must exist:

- **Grant:** trial org → Pro ⇒ agents 1→10, n8n unlocked, message window restarted, `plan_grants`
  row + audit entry written.
- **Note missing** ⇒ 400, nothing written.
- **Expiry in the past** ⇒ 400.
- **Plan not in `GRANTABLE_PLANS`** ⇒ 400.
- **Expiry passes** ⇒ org computes `plan_expired` with **no cron running**; bot stops; data intact.
- **Revoke:** Business org with 30 agents ⇒ all 30 survive, read-only, 31st refused with 402.
- **Packs:** org at 10,000/10,000 ⇒ blocked; add 3 packs ⇒ replies again at 10,002/11,500.
- **Packs reset:** new period ⇒ `extra_messages` back to 0.
- **Window rolls** with no scheduler.
- **Storage:** an upload that would exceed the cap is refused **before** the file is persisted.
- **Starter API key:** `GET` 200, `POST` 402.
- **Non-staff** on every `/v1/admin/*` route ⇒ 403 (route-inventory test extended).
- **No org-scoped route writes `plan`** (static check, §12 rule 2).
- **Payment ledger:** granting with `payment_received=false` ⇒ cycle `pending`; after `period_end`
  ⇒ computed `overdue`; **Mark paid** ⇒ `paid` with method/reference/actor recorded.
- **Mark paid & renew** ⇒ `plan_expires_at` +30 days **and** a new `pending` cycle exists.
- **Payment status never gates access** — an `overdue` org inside its expiry window still answers
  visitors (§5.1 rule 1).
- **No customer endpoint leaks `billing_cycles`** — assert the field names appear in no
  customer-facing response schema (§16.3).
- **Workspaces:** a Pro owner may create a 2nd workspace and is refused a 3rd; **the new
  workspace is created on `trial`, not `legacy` and not `pro`** (§11 rule 3).
- **Pricing endpoint matches `PLANS`** — asserts §3 and `plans.py` can never drift.

---

## 15. Env vars

**None required.** This phase adds no third-party integration — that is the point of it. If a
task makes you add a payment-provider key, stop: you are in `docs/23` territory.

Optional:
```bash
BILLING_DEFAULT_GRANT_DAYS=30     # what the "30 days" button in the admin dialog uses
BILLING_CONTACT_EMAIL=aadeshworkplace@gmail.com   # CTA target on /pricing and /billing
```
Add both to `.env.example` and `docs/ENV.md` per `CLAUDE.md` §2.6.

---

## 16. Decisions — settled 2026-09-28, record as an ADR

These were open questions. They are now answered and the spec above reflects them. Do not reopen
them mid-phase; if one turns out wrong, change it here first, then the code.

### 16.1 Revoke lands on `plan_expired` — read-only, never a fresh trial

*Decided for the operator, who had no preference.* A revoke means a client stopped paying;
dropping them onto a fresh `trial` would hand them **500 free messages and another 10 days** as a
reward for that. `plan_expired` keeps every agent, document and conversation intact but read-only,
with the bot silent — so re-granting on payment is instantly reversible and nothing is lost.
Practical upside: a client can be revoked and restored in two clicks with zero data risk.

### 16.2 Default expiry is 30 days

Billing is monthly, so the grant dialog pre-selects **30 days**. A forgotten revoke then costs you
one month, not forever. **No expiry** stays available for your own and demo workspaces.

### 16.3 Billing state is visible to YOU only — plus a payment ledger

The client is **never** shown payment status, amount due, or an expiry countdown, because you may
mark a payment days after it actually arrived and a paid client must never see "payment pending".

This drove the new §5.1: every 30-day cycle is `pending` until **you** mark it paid in the admin
panel, giving you a collections list (**Payment pending** / **Overdue**) — while access itself is
governed purely by `plan_expires_at`, so a slow bank transfer never silently kills a client's bot
mid-conversation.

### 16.4 Workspaces depend on the plan: Starter 1 · Pro 2 · Business 5

Each workspace is **billed separately** and carries its own limits — a Pro client with 2
workspaces pays $99 × 2 (§3 rule 7). The *Workspaces* number is how many they may **own**, not a
shared pool.
**The trap this creates, guarded in §11 rule 3:** `Organization.plan` defaults to `legacy`
(unlimited), so a client clicking "New workspace" would get an **unlimited free workspace** unless
`create_org` explicitly sets `plan="trial"`. There is a test for exactly this.

### 16.5 Your own workspace stays on `legacy`

Unlimited, never gated, never in the overdue list (use **Waive** on any cycle that opens for it).
Same for demo workspaces you show to prospects.

---

## Appendix A — Your monthly operating runbook

The process this system exists to support:

| When | You do |
|---|---|
| **New client agrees a plan** | Client signs up (trial) → they pay you → `/admin` → find workspace → **Change plan** → plan + 30-day expiry + **☑ Payment already received** (method + reference) + note `"<Client> — $99, INV-xxx"` → done. Features live on their next click, cycle opens **paid**. |
| **Client says they'll pay later** | Same, but **untick** *Payment already received*. They get access now; the workspace sits in **Payment pending** until you mark it. |
| **Payment arrives** | **Payments** tab → find them → **Mark paid** (method + reference). |
| **Renewal month (day ~30)** | They appear in **Expiring in 7 days**. Take payment → **Mark paid & renew** → expiry +30 days, next cycle opens. **One click, every month.** |
| **Client hits the message cap** | They appear under **At limit**. Call them, sell packs, take payment → **Add messages** → note the invoice ref. Bot resumes immediately. |
| **Weekly** | Open **Overdue** → chase. These are people using your product without paying. |
| **Weekly** | Open **Near limit (80%+)** → warn those clients before their bot stops. This is the call that prevents an angry email. |
| **Weekly** | Open **Expiring in 7 days** → chase the next month's payment. |
| **Monthly** | Renew paying clients (**Change plan** again with a new expiry) · revoke non-payers · reconcile **Uninvoiced packs**. |
| **Any dispute** | Org drawer → **Grant history**: every plan, pack, amount, date and staff member, append-only. |

---

## Appendix B — Document conventions

- Numbers live in `app/core/plans.py`. This file and §3 must be updated in the same commit as any
  change there; a test asserts the pricing endpoint matches `PLANS`.
- Every decision this phase makes gets an ADR in `docs/DECISIONS.md` (next free number).
- Update `docs/PROGRESS.md` at the end of each phase and tag git, per `CLAUDE.md` §9.
- Add §12 to `docs/SECURITY.md` once A2 is merged.
- Self-serve payment lives in `docs/23-STRIPE-SELF-SERVE-BILLING.md` and shares §3, §4, §6 and §11
  with this file. If you change a plan limit, change it once, here.
