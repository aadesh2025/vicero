# Phase S1 — Self-Serve Signup, 10-Day Free Trial & Plan Limits

> Paste into Claude Code: "Read docs/18-SELF-SERVE-SIGNUP-AND-FREE-TRIAL-PROMPT.md and implement it."

---

## 0. How to work (read first)

- Read `CLAUDE.md`, `docs/PROGRESS.md`, `docs/DECISIONS.md`, `docs/SECURITY.md` and load the `session-log` skill.
- Use the `engineering-verification` skill in **add-features-safely** mode.
- **Investigate before editing.** Find the real auth flow, org/workspace creation, admin routes, chat pipeline and channel entry points.
- Write an impact plan to `docs/18-SELF-SERVE-PLAN.md` (files touched, migrations, risks) **before** coding.
- Reuse what exists. Do not create parallel tables/services unless you justify it in `docs/DECISIONS.md`.
- Enforce every rule **on the server**. Hiding things in the UI is only UX, never security.
- Build in the order of section 12. Run tests after each step.

---

## 1. What already exists (verify, don't assume)

- Backend: FastAPI `apps/api`, Postgres + Alembic, Celery. Frontend: Next.js `apps/web`.
- `app/models/identity.py`:
  - `User` (has `is_staff`, `password_hash` nullable for OAuth-only, `email_verified_at`)
  - `OAuthAccount` (provider currently `google|github`)
  - `MagicLinkToken`, `PasswordResetToken`, `EmailVerificationToken`, `Session`
  - `Organization` (= workspace, has `plan` = `free|pro|enterprise`)
  - `Membership` (role `owner|admin|editor|viewer|operator`)
- `app/models/platform.py`: `UsageRecord`, `Quota`, `Subscription`, `FeatureFlag`.

---

## 2. Goal

- Today Vicero access is given manually. Change it to **self-serve**:
  - Anyone signs up → gets **1 workspace** → gets a **10-day free trial** with limits.
  - After the trial (or limit reached) their bots **silently stop replying** and the dashboard asks them to upgrade.
- Paid plans/pricing are **NOT** part of this phase. Only leave a clean hook for them.

---

## 3. Signup & login

### Methods (all four)
- Google OAuth
- Facebook OAuth
- Email magic link (passwordless)
- Email + password

### Rules
- OAuth: use `state` + PKCE, validate redirect URIs, store tokens encrypted (existing pattern).
- Add `facebook` as an `OAuthAccount` provider.
- Facebook may not return an email → ask the user for an email and verify it before continuing.
- **Account linking:** same verified email across providers = same `User`. Never auto-link to an **unverified** email (account-takeover risk).
- Email+password signups must verify email. Until verified: user can build/test, but **cannot publish an agent to live channels**.
- Password rules: min 8 chars, hashed with the existing hasher, check against a small breached/common list if cheap.
- Login/signup errors must not reveal whether an email exists (no account enumeration).
- Rate limit: signup, login, magic-link, password-reset (per IP + per email). Lockout after repeated failures.
- Self-signup can **never** set `is_staff=True` or any admin role.

### On first signup (one DB transaction)
- Create `User`.
- Create exactly **one** `Organization` (workspace) + `Membership(role="owner")`.
- Set `plan="trial"`, `trial_started_at=now()`, `trial_ends_at=now()+10 days` (UTC).
- Create the usage counter row (section 6).
- Redirect to onboarding → "Create your first agent".

---

## 4. Admin section hidden

- Find every admin/staff/platform route and page (backend + frontend).
- Only `is_staff=True` users can see or call them.
  - Backend: dependency that returns **403** for non-staff.
  - Frontend: hide admin nav + block the route (redirect).
- Add tests proving a normal signed-up user gets 403 on every admin endpoint.

---

## 5. One workspace per user

- Non-staff users can **own max 1 workspace**.
- Block the create-workspace API (return `plan_limit` error) and hide the "New workspace" button.
- Staff and existing (pre-migration) accounts are not affected.

---

## 6. Plans & entitlements (single source of truth)

Create one config module (e.g. `app/core/plans.py`, or extend the existing place if one exists):

| Entitlement | `trial` (new signups) | `trial_expired` | `legacy` (existing clients/staff) |
|---|---|---|---|
| Duration | 10 days | — | unlimited |
| Workspaces owned | 1 | 1 | unlimited |
| Agents | 1 | 1 (read-only) | unlimited |
| Messages (total) | 500 | 0 | unlimited |
| Workflows | ❌ | ❌ | ✅ |
| n8n connection | ❌ | ❌ | ✅ |
| Tool calling (custom tools, MCP servers) | ❌ | ❌ | ✅ |
| Bot replies to visitors | ✅ | ❌ | ✅ |

- Code must **only** read limits through one function, e.g. `get_entitlements(org)`. No hard-coded numbers anywhere else.
- Adding a paid plan later = add one entry here + a payment webhook. Nothing else.
- `trial_expired` is computed: `plan == "trial" and (now >= trial_ends_at or messages_used >= limit)`. Do not rely only on a cron to flip it.

### Migration
- Alembic: add `trial_started_at`, `trial_ends_at` to `Organization`; extend `plan` values.
- **Backfill all existing organizations to `legacy`** so current clients are never blocked.
- Migration must be reversible.

---

## 7. Message counting (messages, NOT tokens)

- **1 message = 1 visitor message OR 1 AI reply.** One question + one answer = **2**.
- Do **not** count:
  - Human operator replies from the inbox (no LLM cost).
  - Dashboard test/playground messages → instead cap playground at 50 messages/day on trial.
  - System/event messages.
- Storage: per-org counter (reuse `Quota`/`UsageRecord` if they fit; otherwise a small `org_message_usage` table — justify in DECISIONS.md).
- **Atomic, race-safe** (two visitors at the same time must not push past 500):
  - Before calling the LLM, reserve 2 with a single SQL
    `UPDATE ... SET used = used + 2 WHERE org_id = :id AND used + 2 <= :limit RETURNING used`.
  - No row returned → blocked (section 8).
  - If the LLM call fails and no reply is sent → refund 1.
- Enforce at **one choke point** in the chat pipeline (`app/chat`) that every channel goes through (widget, WhatsApp, Instagram, Messenger, API, etc.). Verify each channel really hits it.

---

## 8. When the trial ends or messages run out

### Visitor side (the end-customer chatting with the bot)
- The bot **silently does not reply.**
- **Never** show "free trial ended", "quota", "upgrade" or any error text in the widget or any channel.
- Widget typing indicator must stop cleanly (no infinite "typing…").
- Visitor messages are **still saved** in the inbox, so the owner sees them.
- Owner/operators can still reply manually from the inbox.

### Owner side (dashboard)
- Persistent banner on every page: **"Your free trial has ended. Upgrade to continue."** + Upgrade button.
- During the trial show a usage meter: `312 / 500 messages · 4 days left`.
- After expiry show: `X visitor messages were not answered` (strong upgrade reason).
- Upgrade button → `/billing/upgrade` placeholder page ("Plans coming soon — contact us" with email/WhatsApp). Pricing will be added later.
- **Never delete data** on expiry.

### Emails (Celery beat, use existing email service)
- Day 7 (3 days left), Day 9 (1 day left), trial ended.
- 80% messages used, 100% messages used.
- Each email sent once only (idempotent — store "sent" flags).

---

## 9. Feature gating (trial + expired)

- Blocked: create/edit workflows, connect n8n, tool calling (custom tools + MCP servers), creating a 2nd agent.
- API: return **HTTP 402** with body `{ "code": "plan_limit", "feature": "<name>", "message": "..." }`.
- UI: show these features as **locked with an upgrade tooltip** (don't fully hide — it's an upsell).
- **Runtime check too:** even if an agent somehow has tools/workflows attached, the agent runtime must not execute them on a trial org.
- Knowledge base / RAG: allowed on trial, keep existing limits (flag in plan if none exist).

---

## 10. Abuse & cost protection

- One trial per verified email. Normalize emails (lowercase; Gmail dots and `+tag` removed) for the uniqueness check.
- Max signups per IP per day (config, default 3).
- Optional disposable-email blocklist (config flag, default on).
- Per-org widget rate limit so one bot can't burn 500 messages in seconds.
- Audit-log: signup, login, plan change, trial expiry, limit hit.

---

## 11. Frontend pages

- `/signup`, `/login` — 4 methods, clear errors, loading states, accessible (labels, keyboard, contrast).
- `/verify-email`, `/forgot-password`, `/reset-password`, magic-link landing page.
- Onboarding after first login → create first agent.
- Trial banner, usage meter, locked-feature states, `/billing/upgrade` placeholder.
- Hide admin nav and "New workspace" for non-staff.
- Match the existing design system. Mobile responsive.

---

## 12. Build order

1. Investigate + write `docs/18-SELF-SERVE-PLAN.md`.
2. Plans/entitlements module + Alembic migration + backfill to `legacy`.
3. Signup/login (email+password, magic link, Google, Facebook) + auto workspace + trial.
4. Admin lockdown + 1-workspace rule.
5. Message counter + choke-point enforcement + silent stop.
6. Feature gating (API + runtime + UI).
7. Dashboard banner, usage meter, upgrade placeholder.
8. Emails (Celery beat).
9. Abuse protections.
10. Tests, docs, final report.

---

## 13. Tests (must pass)

- Signup via each method creates exactly 1 user + 1 workspace + trial dates.
- Same email via Google then password → one user (only when verified).
- Non-staff → 403 on all admin endpoints.
- 2nd workspace / 2nd agent / workflow / n8n / tool → 402 `plan_limit`.
- Counter: 1 Q + 1 A = 2; stops at exactly 500; 20 concurrent requests never exceed 500.
- LLM failure refunds the reserved reply.
- After day 10 (freeze time) bot does not reply; widget gets no error text; message saved in inbox.
- Existing orgs (`legacy`) unaffected.
- Emails sent once each.

---

## 14. Done = report back to me in plain English

- What was built, what files changed.
- Any decision you made that I should know about.
- **Exact setup steps I must do myself:**
  - Google Cloud Console OAuth client (redirect URIs).
  - Meta for Developers app for Facebook Login (email permission, app review, privacy-policy URL).
  - New env vars → add to `docs/ENV.md` and `.env.example`.
- Update `docs/PROGRESS.md`, `docs/DECISIONS.md` (ADR for plans/entitlements), `docs/SECURITY.md`.

## Out of scope (do NOT build now)

- Payments (Razorpay/Stripe), paid plan pricing, invoices.
- Team invites for trial users, multiple workspaces for paid users.
