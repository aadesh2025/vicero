# Phase S1 — Impact plan (written before any code)

> Spec: `docs/18-SELF-SERVE-SIGNUP-AND-FREE-TRIAL-PROMPT.md`. Mode: engineering-verification
> **feature safety**. Everything below was read from the code, not assumed. FACT = read in the
> repo; DECISION = a choice this phase makes (each is recorded in ADR-088 / ADR-090).

## 0. Preflight

- FACT: branch `master`, HEAD `189792e`. **Eight files are already modified and uncommitted**
  (`chat/output_guard.py`, `chat/pii.py`, `chat/runtime.py`, `llm/fake.py`, four PII tests) — not
  mine. This phase does not touch them and never stages them; every commit is path-scoped.
- FACT: backend `apps/api` (FastAPI, async SQLAlchemy, Alembic head **0027_workflow_tests**), web
  `apps/web` (Next 16 / React 19, BFF cookies), widget `packages/widget`.
- FACT: `ruff`, `mypy`, `pytest` (backend), `tsc`, `eslint`, `vitest`, Playwright (web). Commands
  from CLAUDE.md §2 / §12 and the `Makefile`.

## 1. What exists today (verified)

| Area | Reality |
|---|---|
| Signup | `auth/service.signup` creates a `User` only — **no org**. Returns tokens immediately. 409 `auth.email_taken` if the email exists (enumeration). |
| Org creation | `orgs/service.create_org`: **staff only** unless the test switch `allow_self_serve_orgs` is on. A prior decision deliberately removed self-serve (`e2e/24-no-self-serve-signup.spec.ts`, `tests/test_org_creation_gate.py`). This phase **reverses that on purpose**, at the user's request. |
| Login providers | Google + GitHub only. **No frontend OAuth flow exists at all**: the API callback returns JSON to the browser. State store is a process-local dict. |
| OAuth linking | `oauth_login` links to an existing user **by email with no check that the existing account's email is verified** → account-takeover risk (§3 of the spec). GitHub falls back to a fake `id@users.noreply.github.com`. |
| Magic link | Creates a `User` for an unknown email; verifies on click. Fine. Email links point at `/verify`, `/reset`, `/magic` — **none of those pages exist**. |
| Admin | All 7 `/v1/admin/*` routes already use `require_staff` (403 for non-staff). Frontend hides the nav item for non-staff (`sidebar-nav`), but `/admin` is only guarded by the auth cookie. |
| Chat choke point | `chat/inbound.InboundTurn.events()` is used by **every** visitor path: widget HTTP/SSE/WS (`public/service`) and all channels (`channels/service.process_inbound`). Dashboard paths (Playground, agent tests, workflow LLM nodes, `chat_events`) call `run_turn` directly and are separate. |
| Tools at runtime | `tools/service.build_tooling` (org_id + agent in hand) is the one place tools are attached to a turn. Workflows execute through `workflows/service._dispatch_run`. |
| Plan | `Organization.plan` string `free|pro|enterprise`, **read by nothing** (only echoed in `OrgOut`). `Quota`/`UsageRecord` are token/request-shaped and not per-org-unique. |
| Email | `queue_email()` (console inline / SMTP via Celery). Celery beat exists with one job. |
| Audit | `write_audit` needs an `organization_id` (NOT NULL) → signup audit is written after the org exists. |

## 2. Design (one source of truth)

### 2.1 Plans / entitlements — `app/core/plans.py` (new)
- `PLANS: dict[str, PlanSpec]` = `trial`, `trial_expired`, `legacy`. Nothing else in the codebase
  contains a limit; every reader calls `get_entitlements(org, messages_used, now)`.
- `trial_expired` is **computed**, never stored: `plan == "trial" and (now >= trial_ends_at or
  messages_used >= limit)`. No cron flips it. (Plus: a trial with fewer than 2 messages left is
  treated as exhausted, because a reply is reserved as a pair — DECISION, ADR-088.)
- **Fail-safe default:** `Organization.plan` defaults to **`legacy`** (unlimited). Only the
  self-serve signup path writes `trial`. A code path that forgets to set a plan can therefore
  never lock a paying client out.
- `plan_limit(feature)` → `AppError("plan_limit", …, 402, details={"feature": …})`. Body follows the
  project envelope `{"error": {"code": "plan_limit", "message", "details": {"feature"}}}` so the
  existing typed API client works unchanged (DECISION; spec's flat shape is the same data).

### 2.2 Message counter — `app/billing/usage.py` (new) + table `org_message_usage`
- New small table (justification: `Quota` has no per-org uniqueness and its columns are
  token/request-shaped; adding a row shape there would change `Quota` semantics for everything
  reading it). Columns: `organization_id` PK, `messages_used`, `unanswered_messages`,
  `emails_sent` (JSONB idempotency flags), timestamps.
- `reserve(session, org_id, n=2, limit)` = one statement
  `UPDATE … SET messages_used = messages_used + :n WHERE organization_id = :id AND
  messages_used + :n <= :limit RETURNING messages_used`. `refund(n=1)` on provider failure.
- Playground cap: 50/day on trial via the existing Redis fixed-window limiter (no new table).

### 2.3 Enforcement points
| Rule | Where | Server-side check |
|---|---|---|
| Silent stop + counter | `chat/inbound.InboundTurn.events()` — after the visitor message is persisted (so the inbox still gets it) | early read check + atomic reserve just before the provider call; blocked ⇒ no tokens, no error text, **and the conversation is parked in the Inbox handoff queue** (`reason = plan_limit`, see §8) |
| 1 agent | `agents/service.create_agent`, `duplicate_agent` | `plan_limit("agents")` |
| Agents read-only when expired | agent write paths + Playground | `plan_limit("agents")` |
| Workflows | `workflows/service` create/update/version/run/test-run/publish | `plan_limit("workflows")`; **runtime**: `_dispatch_run` refuses on a non-entitled org |
| n8n | `tools/service.bind_n8n_workflow` + `list_n8n_workflows` | `plan_limit("n8n")` |
| Tool calling | `create_tool`, `update_tool`, MCP create/test; **runtime**: `build_tooling` returns `([], None)` | `plan_limit("tool_calling")` |
| Publish / enable channel needs verified email | `agents.publish_version`, `channels.set_enabled` | trial plan only (legacy users may be unverified) |
| 1 workspace | `orgs/service.create_org` | `plan_limit("workspaces")` for non-staff who already own one |
| Admin | unchanged `require_staff`; add a route-inventory test asserting **every** `/v1/admin` route 403s for a normal user and that no admin route lacks the dependency | |

### 2.4 Signup / login
- `auth/service.signup`, `magic_link` (new user), `oauth_login` (new user) all call one
  `provision_self_serve_workspace(session, user)` inside the **same transaction**: one org, one
  `owner` membership, `plan="trial"`, `trial_started_at/ends_at` (UTC, +10 d), one usage row, audit.
  It is a no-op when self-serve is disabled, when the user already owns a workspace, or when the
  email has a **pending invitation** (an invited teammate must not spawn a stray trial org).
- Account linking: link OAuth → existing user **only if that user's email is verified**; otherwise
  refuse with a generic error and send the owner of the address a verification/magic link. The
  provider email must itself be verified (`email_verified` for Google; Facebook returns only
  confirmed emails).
- Facebook: new provider. No email returned ⇒ short-lived signed pending token → user enters an
  email → we mail a single-use signed link → clicking it proves the address → then normal link/create.
- OAuth end-to-end for the browser: API callback → one-time exchange code → redirect to
  `/oauth/callback?code=…` → BFF exchanges it and sets the httpOnly refresh cookie (tokens never
  travel in a URL). State store stays in-process (already documented; unchanged).
- Password rules: ≥ 8, small common-password list. Signup errors are generic; login timing is
  equalised with a dummy hash; per-IP **and** per-email limits; lockout after repeated failures.
- Abuse: normalised email (lowercase, Gmail dots and `+tag`) stored in `users.email_normalized`
  (indexed, checked at signup; not UNIQUE, so legacy rows can never break the migration),
  per-IP signups/day, disposable-domain blocklist (flag), per-org widget rate limit.
- `is_staff` can never be set by self-signup (signup never passes it; a test pins it).

### 2.5 Emails (Celery beat, hourly sweep)
`trial_day7`, `trial_day9`, `trial_ended`, `messages_80`, `messages_100` — flags in
`org_message_usage.emails_sent`, set in the same transaction as the send decision so a re-run is a no-op.

### 2.6 Frontend
`/signup`, `/login` (4 methods), `/verify-email`, `/forgot-password`, `/reset-password`, `/magic`,
`/oauth/callback`, `/onboarding`, `/billing/upgrade`; trial banner + usage meter in the app shell;
locked (not hidden) states for Automations/tools/MCP/second agent; `/admin` redirect for non-staff;
hide "New workspace" for non-staff (already staff-only in `org-switcher`, kept). Widget: an empty
reply removes the typing bubble instead of leaving "…".

## 3. Files touched

**New:** `core/plans.py`, `billing/usage.py`, `modules/auth/policy.py`, migration `0028_self_serve_trial`,
`OrgMessageUsage` model, trial email templates + beat task, ~10 test files, the pages listed above.
**Modified (backend):** `models/identity.py` (`plan` default, `trial_*`, `users.email_normalized`),
`models/platform.py`, `models/__init__.py`, `core/config.py`, `core/ratelimit.py` (`peek`),
`core/email_templates.py`, `modules/auth/{service,router,oauth,schemas}.py`,
`modules/orgs/{service,router,schemas}.py`, `modules/agents/service.py`, `tools/service.py`,
`workflows/service.py`, `channels/service.py`, `chat/inbound.py`, `worker/{tasks,celery_app}.py`,
`tests/conftest.py`, `tests/test_org_creation_gate.py`.
**Modified (web/widget):** signup/login pages, BFF routes, `proxy.ts`, app layout, sidebar/nav,
`automations` + agent tabs, `packages/widget/src/widget.js` (+ rebuild), `e2e/24-…`.
**Docs:** `PROGRESS.md`, `DECISIONS.md` (ADR-088/090), `SECURITY.md`, `ENV.md`, `.env.example`.

**Deliberately NOT changed:** `chat/runtime.py`, `chat/output_guard.py`, `chat/pii.py`, `llm/*`
(someone else's uncommitted work; the choke point is `inbound.py`); RBAC roles; billing/payments.

## 4. Migration `0028_self_serve_trial` (reversible)
- `organizations`: add `trial_started_at`, `trial_ends_at` (nullable tz), `plan` server default
  `legacy`; **backfill every existing row to `legacy`**.
- `users`: add `email_normalized` (indexed) + backfill from `email`.
- `org_message_usage` table.
- `downgrade()` drops all of it and restores the `free` default.

## 5. Risks and how each is contained

| Risk | Containment |
|---|---|
| Locking out an existing client | default `legacy`, backfill, test that legacy orgs are untouched by every gate |
| ~1000 existing tests bootstrap orgs via `signup` + `POST /v1/orgs` | new switch `SELF_SERVE_ENABLED` (prod default **on**); conftest turns it **off** the same way it already sets `allow_self_serve_orgs`, new tests turn it on. Also gives the operator a kill switch. |
| Counter race | single atomic `UPDATE … RETURNING`; a test drives 20 concurrent connections against a real committed row |
| Silent stop leaks text | enforced in `InboundTurn`, before any token is emitted; test reads the raw SSE and every channel reply |
| Account takeover via OAuth email match | link only to verified accounts; test with an unverified password account |
| Enumeration | generic signup/login/forgot/magic messages; residual (a signup that returns a session must differ from a refusal) documented in SECURITY.md |
| Facebook PKCE | Meta documents no PKCE for the server-side code flow; state + client secret used, flag left in the provider table (ADR-090). Not verifiable without real credentials — listed in the human setup steps. |
| Real OAuth/SMTP/Meta cannot be exercised here | provider HTTP is mocked in tests (existing pattern: `fetch_oauth_user` monkeypatch); reported as **not run against real providers** |

## 6. Order of work (spec §12) and gates
2 plans+migration → 3 signup/login → 4 admin+1 workspace → 5 counter+silent stop → 6 gating →
7 dashboard UI → 8 emails → 9 abuse → 10 tests/docs/report. After each step: `ruff`, `mypy`,
focused `pytest`, and the full suite at the end (≈18 min on this machine — budget for it).

## 7. Setup only the human can do

**Google sign-in**
1. Google Cloud Console → *APIs & Services → OAuth consent screen*: External, app name, support email,
   authorised domain, and the scopes `openid`, `email`, `profile`. While it is in *Testing*, add your own
   address under *Test users*; publish it to let anyone sign in.
2. *Credentials → Create credentials → OAuth client ID → Web application*.
   Authorised redirect URI: `{OAUTH_REDIRECT_BASE}/v1/auth/oauth/google/callback`
   (dev: `http://localhost:8000/v1/auth/oauth/google/callback`; prod: your API origin, `https://` only).
3. Put the client ID/secret in `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`; set `OAUTH_REDIRECT_BASE` to the
   API origin the redirect URI above uses, and `WEB_BASE_URL` to the web origin.

**Facebook sign-in**
1. developers.facebook.com → *Create app* → use case *Authenticate and request data from users with Facebook
   Login*. Add the **Facebook Login** product → *Web*.
2. *Facebook Login → Settings → Valid OAuth Redirect URIs*: `{OAUTH_REDIRECT_BASE}/v1/auth/oauth/facebook/callback`.
   Keep *Client OAuth login* and *Web OAuth login* on; leave *Use Strict Mode for redirect URIs* on.
3. *App settings → Basic*: set the **Privacy Policy URL** and **Terms** URL (required to go Live), an app icon,
   category, and a contact email. Copy **App ID** → `FACEBOOK_CLIENT_ID`, **App secret** → `FACEBOOK_CLIENT_SECRET`.
4. Permissions: `public_profile` and `email` need **no App Review** for Facebook Login of your own users, but the
   app must be switched from *Development* to **Live** for anyone other than app roles (admins/developers/testers).
   Users who registered with a phone number have no email — the sign-in flow then asks for one and verifies it.
5. Not verifiable without a real app: whether Meta tolerates PKCE parameters on this flow (the code sends none —
   ADR-090) and the exact fields returned for your app's API version (`v19.0` is pinned in `modules/auth/oauth.py`).

**Email** — magic links, verification and the trial emails need a real relay: `EMAIL_BACKEND=smtp` plus
`SMTP_HOST/PORT/USER/PASS/FROM` (see `docs/ENV.md`). Run **Celery beat** alongside the worker
(`celery -A app.worker.celery_app beat`), or the hourly `trial.sweep` never fires.

**Upgrade page** — set `NEXT_PUBLIC_UPGRADE_EMAIL` / `NEXT_PUBLIC_UPGRADE_WHATSAPP` and rebuild the web app.

**Deploy order** — run `alembic upgrade head` (0028) **before** starting the new API; the migration backfills every
existing organization to `legacy`, so no current client is metered.

## 8. As built — where it differs from §2–§6

- The DB-touching half of the counter lives in **`app/billing/usage.py`**, not `core/`: `tests/test_architecture.py`
  forbids `core` importing models. The pure table stays in `core/plans.py`.
- `normalize_email` lives in **`app/db/base.py`** (re-exported by `modules/auth/policy.py`) because the `User` model
  fills `email_normalized` itself and models may import only `db.base`.
- `auth.service` imports `orgs.service` lazily (function level) to avoid a new package cycle.
- Signup with an already-registered address **still returns 409 `auth.email_taken`**. A signup that hands back a
  session cannot be made indistinguishable from a refusal, and the invitation flow keys off that code. Mitigated by
  per-IP and per-email limits; every other auth endpoint is non-enumerating. Recorded in `docs/SECURITY.md`.
- The playground cap uses the shared Redis fixed-window limiter (24h window from first hit), not a table.
- E2E: the keyless E2E API runs `SELF_SERVE_ENABLED=false`; trial UI states are checked with a stubbed plan endpoint
  (`e2e/30-trial-ui.spec.ts`) and enforcement by pytest.
- **Silenced conversations go to the Inbox handoff queue (added after the first verification pass).** The spec
  said the message is "saved in the inbox"; on dev the visitor message was saved but the Inbox page (which lists
  handoffs) showed nothing, so the owner had no signal. Now, when a visitor message goes unanswered because of the
  plan (trial over or messages spent), `InboundTurn._stay_silent` also calls `trigger_handoff(requested_by="system",
  reason="plan_limit")`: the conversation appears in the Inbox as a normal handoff, the owner gets the usual
  real-time `handoff.requested` push and webhook, and the Inbox shows a **"plan limit"** badge (tooltip and detail
  header: "agent couldn't reply (plan limit)") so it is not mistaken for a visitor asking for a person.
  Behaviour worth knowing: (1) further visitor messages in that conversation are still saved and still counted
  in `unanswered_messages` — only while the handoff is `open`; once the owner takes over, their replies are the
  answer and the count stops; (2) it is one handoff per conversation, not one per message; (3) the per-org burst
  drop is **not** routed (a flood is not a plan problem); (4) `legacy` orgs never get one; (5) the conversation is
  in `handoff` status, so the bot stays paused in it **after an upgrade until the owner hands it back** — the same
  as any other handoff. The visitor still sees nothing.

