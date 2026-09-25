# SECURITY.md — BotForge security checklist

> Verified during **Phase 16.2** (2026-07-19). Each item is marked ✅ met, ⚠️ partial, or
> ❌ unmet with a note. This is a living checklist — re-verify at each release.

## 1. Authentication & sessions
- ✅ **Password hashing**: argon2 (`argon2-cffi`) — see `app/core/security.py`.
- ✅ **JWT access + refresh**: HS256 signed with `SECRET_KEY`. We do **not** use asymmetric/ECDSA
  JWTs (see the `ecdsa` advisory in §8).
- ✅ **Magic links / OAuth**: tokens are single-use / signed; provider secrets read from env only.
- ✅ **Web token storage (refresh) — httpOnly (Phase 20).** The long-lived **refresh token** now
  lives in an `httpOnly`, `SameSite=Lax` (`Secure` in prod) cookie set by a Next BFF
  (`/api/auth/{login,signup,refresh,logout}`, `src/app/api/auth`). JS never touches it, so an XSS
  can no longer exfiltrate the persistent credential; refresh + rotation happen server-side.
- ⚠️ **Web token storage (access) — remains JS-readable, by architecture.** The short-lived access
  token stays in a JS cookie because the browser calls the FastAPI API **cross-origin** with a
  `Bearer` header, streams SSE, and opens the operator-inbox **WebSocket** with the token as a query
  param (`/v1/inbox/ws?token=`). A full httpOnly migration would require proxying **all** API calls,
  SSE, and WebSockets through Next (the API is a separate origin and cross-origin WS handshakes don't
  send the web-origin cookie) — a larger re-architecture than the marginal gain warrants. Residual
  risk is bounded: the access token is short-TTL (~15–30 min) and rotates, CORS is strict, and the
  most damaging credential (refresh) is now out of JS reach. Revisit if the web app and API are ever
  co-located behind one origin.

## 2. Authorization & multi-tenancy
- ✅ **Tenant isolation**: every org-scoped query is filtered by `organization_id`; org resolved via
  `current_org` / `org_context` (`app/modules/orgs/deps.py`).
- ✅ **RBAC matrix**: owner/admin/editor/viewer/operator enforced by `require_permission`
  (`app/core/rbac.py`), on both API and UI. Viewer-denial verified live (Phase 15).
- ✅ **API-key scope enforcement** (Phase 16.2): keys carry `read`/`write`/`admin` scopes. A key's
  effective role = its scope tier **capped by the creating member's role** (least privilege). `admin`
  scope maps to the `admin` role, never `owner`, so a key can never delete the org. Verified live: a
  read-scoped key gets **403 on a write endpoint**. Org-admin routes are JWT-only (path-scoped
  `org_context`), so API keys cannot manage members at all.

## 3. Input validation & injection
- ✅ **Request validation**: Pydantic v2 schemas on every request/response.
- ✅ **SQL injection**: SQLAlchemy 2.0 parametrized queries only; no string-built SQL.
- ✅ **Prompt injection** (Phase 16.1): retrieved RAG chunks and tool output are treated as **data,
  not instructions** — neutralized (`app/chat/guardrails.neutralize_injections`) and wrapped with an
  explicit data directive before reaching the model. Blocked-topics refuse pre-LLM.
- ✅ **Output redaction** (Phase 16.1): secret-looking strings (API keys, cards) are redacted from
  assistant output before it is stored/returned.

## 4. SSRF
- ✅ **Coverage**: the shared guard `app/core/ssrf.py` (rejects loopback / private / link-local / reserved /
  multicast, resolved via DNS) is applied to **all** tenant-controlled outbound fetches: URL knowledge
  ingestion (`rag/loaders`, every redirect hop), the HTTP tool (`tools/http_tool`), the `http_request`
  builtin (`tools/builtins`), outbound webhook delivery (`webhooks/dispatch`), MCP SSE servers
  (`tools/mcp_client`, ADR-085) and tenant-set LLM provider endpoints (`llm/openai_compatible`, ADR-087;
  private hosts only via the operator's `PROVIDER_PRIVATE_HOSTS`). stdio MCP servers are staff-only (ADR-085).
- ⚠️ **Residual**: DNS rebinding between the check and the connection is not closed anywhere (needs the
  resolved IP pinned on the socket).
- ✅ **n8n exemption is deliberate & documented**: `app/integrations/n8n_client` targets the
  operator-configured, trusted `N8N_BASE_URL` (loopback in dev), not arbitrary user input, so it is
  intentionally not SSRF-guarded (noted in the module docstring).

## 5. Rate limiting (public surfaces)
- ✅ **Auth**: signup / login / resend / forgot / magic / reset / verify / OAuth exchange (`app/modules/auth/router.py`),
  per IP, **and** per email for signup, magic-link, forgot, resend (5/h) and login failures (lockout, §10).
- ✅ **Public chat**: `/v1/public/agents/{key}/chat` (60/min).
- ✅ **Channel webhooks** (Phase 16.2): telegram / whatsapp / slack / discord inbound (120/min per IP).
- ✅ **n8n callback** (Phase 16.2): `/v1/tools/n8n/callback` (120/min per IP).
- Backed by Redis fixed-window with an in-memory fallback (`app/core/ratelimit.py`).

## 6. Secrets & data at rest
- ✅ **Provider credentials + channel tokens encrypted at rest** with Fernet (key derived from
  `SECRET_KEY`); masked in API responses, revealed once on create.
- ✅ **API keys** stored as sha256 hashes + a lookup prefix; the full key is shown once.
- ✅ **Webhook signing secrets** encrypted at rest; deliveries HMAC-SHA256 signed over
  `"{timestamp}.{body}"`.
- ✅ **No secrets logged**: structured logging; secrets never included in log events.

## 7. Transport & headers
- ✅ **Security headers middleware** (Phase 16.2, `SecurityHeadersMiddleware`): `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`,
  `Cross-Origin-Resource-Policy`, `Permissions-Policy`, and a strict API CSP
  (`default-src 'none'; frame-ancestors 'none'; base-uri 'none'`). Docs UIs (`/docs`, `/redoc`) are
  exempted from CSP/X-Frame-Options so Swagger/ReDoc render.
- ✅ **HSTS** emitted in production only (`Strict-Transport-Security`, 2 years, includeSubDomains).
- ✅ **CORS**: explicit allow-list in prod; localhost-any only in dev.
- ⚠️ **TLS termination**: handled by the reverse proxy (Nginx/Caddy) at deploy — verify at Phase 20.

## 8. Dependency audit (wired into CI — `security` job)
Advisory (non-blocking) `pip-audit` + `npm audit` run in CI. Results as of 2026-07-19:
- ⚠️ **Python — `ecdsa 0.19.2` (PYSEC-2026-1325, no fix available)**: transitive via `python-jose`.
  **Not exploitable in our config** — we sign JWTs with HS256 (symmetric) and perform no ECDSA
  operations. Follow-up: migrate JWT handling to `PyJWT`/`authlib` to drop `python-jose`/`ecdsa`.
- ✅ **Web — Next.js 14 advisories RESOLVED (Phase 19).** The 5 Next.js-14 advisories (4 high + 1
  moderate: Image-Opt DoS, WS-upgrade SSRF, RSC cache poisoning, i18n middleware bypass, postcss
  stringify XSS) were cleared by the **Next.js 14 → 16 + React 19 + ESLint 9** upgrade, plus pinning
  `postcss ^8.5.10` (direct dep + override) to replace the vulnerable copy bundled under `next`.
- ✅ **Web production deps — 0 vulnerabilities** (`npm audit --omit=dev`). Next 16 ships
  `sharp 0.34.x`, which carries inherited libvips CVEs (GHSA-f88m-g3jw-g9cj: CVE-2026-33327/33328/
  35590/35591, vulnerable `<0.35.0`). npm's suggested remedy is a **downgrade to Next 14**, which
  would undo the Phase 19 upgrade and reintroduce 5 advisories — so instead an `overrides.sharp:
  ^0.35.0` pins the patched version (resolves to 0.35.3). Verified: `next build` passes.
- ⚠️ **Web dev toolchain — 9 high, all in the ESLint chain** (`@eslint/config-array`, `eslintrc`,
  `eslint-plugin-*`, via `brace-expansion` / `minimatch`). **Dev-only — never shipped to users or
  into a production image.** Fixing them requires a breaking ESLint downgrade/upgrade; tracked, not
  blocking. Re-check with `npm audit --omit=dev` for what actually ships.

## 9. Known gaps / follow-ups (tracked in PROGRESS roadmap)
- ~~httpOnly cookie migration for web auth tokens (§1).~~ **Refresh token DONE (Phase 20);** access
  token stays JS-readable by cross-origin+WS architecture (documented in §1).
- ~~Next.js major upgrade to clear the web advisories (§8).~~ **DONE — Phase 19.**
- Realtime hub → Redis pub/sub before multi-node prod (ADR-028).
- Webhook retry beat-sweep for `pending` deliveries past `next_retry_at`.
- Replace `python-jose` to drop the `ecdsa` advisory (§8).

## 10. Self-serve signup & free trial (docs/18, ADR-088, ADR-090) — verified 2026-09-25
- ✅ **Signup can never mint staff or an admin role**: the request schema has no such field and the service never
  sets one (`test_self_signup_can_never_produce_staff`). Every `/v1/admin/*` route is behind `require_staff`;
  `test_self_serve_admin.py` walks the router's own route table, so a route added later is checked automatically
  (403 for a normal user, 401 anonymous, 200 for staff as the control).
- ✅ **Account linking**: a provider identity attaches to an existing user only when that user's email is
  **verified** and the provider vouches for the address; otherwise nothing is created (ADR-090). A provider with
  no verified email (Facebook phone signups) proves a typed address by a single-use emailed link first.
- ✅ **OAuth**: `state` (single-use, 10 min) + PKCE for Google; tokens are stored encrypted (Fernet); the browser
  flow uses a one-time 60-second exchange code and the httpOnly refresh cookie — no token in any URL, the pending
  email token in the URL fragment. ⚠️ **Facebook: no PKCE** (Meta documents none for the server-side code flow).
- ✅ **Login**: one Argon2 verification for every attempt (dummy hash for unknown accounts), identical 401 body for
  unknown email and wrong password, lockout after `LOGIN_LOCKOUT_FAILURES` failures per email (applies to unknown
  emails too, so the lockout is not an oracle), per-IP and per-email limits on signup / magic-link / forgot / resend.
- ⚠️ **Residual — signup enumeration.** `POST /signup` answers `409 auth.email_taken` for a registered address. A
  signup that returns a session cannot be indistinguishable from a refusal, and the invitation UI keys off that
  code. Bounded by the per-IP daily cap, the per-email limit and the global auth limit; login, forgot-password,
  magic-link and resend reveal nothing (`test_self_serve_abuse.py`).
- ⚠️ **Residual — pre-registration.** Someone can register a victim's address with their own password; it stays
  unverified (cannot publish, cannot be OAuth-linked) but if the real owner later signs in by magic link the account
  becomes verified with the squatter's password still valid. Pre-existing behaviour, not widened by this work.
- ✅ **One trial per person**: `users.email_normalized` (lowercase, `+tag` stripped, Gmail dots removed) is checked at
  signup; disposable domains are refused (`BLOCK_DISPOSABLE_EMAILS`); `SIGNUPS_PER_IP_PER_DAY` caps new accounts per
  IP (the API learns the real address through `TRUSTED_PROXIES`, §11). The uniqueness check is app-level, not a DB constraint,
  so pre-existing duplicates can never break a migration.
- ✅ **Plan enforcement is server-side**: 402 `plan_limit` on the API and runtime checks in `build_tooling` and
  workflow execution, so a tool or workflow that somehow exists on a trial org still never runs. The UI only explains.
- ✅ **Silent stop leaks nothing**: the gate sits before any model call or emitted text; tests read the raw JSON and
  SSE bodies and every messaging channel and assert no error, quota or upgrade wording.
- ✅ **Counter is race-safe**: one atomic `UPDATE … WHERE used + 2 <= limit RETURNING`, in its own short transaction
  (`test_concurrent_reservations_never_exceed_the_cap`: 300 concurrent reservations, cap 500 → exactly 250).
- ⚠️ **Not verified against real providers**: Google/Facebook OAuth, real SMTP delivery and Meta's behaviour were
  exercised with mocked provider responses only. Do this by hand once credentials exist (docs/18-SELF-SERVE-PLAN.md §7).
- ⚠️ OAuth `state`, exchange codes and the in-memory rate-limit fallback are process-local (as before); move them to
  Redis before running more than one API process.

## 11. Client IP behind a proxy (ADR-092) and exposed dev ports (ADR-093) — verified 2026-09-25
- ✅ **Real client address.** The API believes `X-Forwarded-For` only when the TCP peer is in `TRUSTED_PROXIES`, reads the
  chain from the right skipping its own proxies, and ignores the header from every other peer. A caller cannot pick its
  own address by prepending or by sending the header directly (`tests/test_client_ip.py`: a different forwarded IP gets
  a separate limit; a spoofed header from an untrusted peer does not lift the limit, on the route limiter and on the
  signup cap). The web BFF forwards the visitor's `X-Forwarded-For` and `User-Agent`; Caddy sets the header itself.
- ⚠️ **Operator responsibility:** `TRUSTED_PROXIES` must cover what sits directly in front of the API and web app and
  nothing public. Too narrow ⇒ one shared bucket per proxy; too wide ⇒ spoofable. With no identifiable client (plain
  local dev) the per-client signup cap steps aside and the session/audit IP is recorded as unknown.
- ✅ **Dev Postgres/Redis are loopback-only** and the Postgres password is required, not defaulted. Verified: the new
  password connects on loopback; the old default (`botforge`) is rejected; connections to the machine's Ethernet and
  hotspot addresses on `5750` are refused, as is Redis on `6379`.
- ⚠️ Still bound to all interfaces in the dev compose file: `api` (8000), `web` (3001), `n8n` (5679), `ollama` (11435).
  Redis still has no password (loopback-only for now). k8s manifests not audited.

