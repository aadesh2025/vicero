# Security verification

Goal: confirm that boundaries which matter *in this project* hold, and that a recent fix is not bypassed by a
sibling path. The method is risk-based: identify the attack surfaces that exist, then verify those. Do not run
a generic checklist against every project. It is not a full penetration test.

## 1. Identify the applicable attack surfaces

Build the list from evidence, not from this document. For each surface note where it lives, who can reach it
(anonymous, authenticated user, tenant admin, operator), and what it can touch.

A surface that looks weaker than expected is not automatically a bug: check for an explicit, documented
security decision before treating a shape in the code as an oversight to fix (`state-and-sources.md`, source-
of-truth hierarchy). An undocumented weak surface is a finding; a deliberately accepted, recorded trade-off is
not — report it as what it is and move on, do not silently "harden" it past what the decision describes.

Look for: HTTP/RPC/WebSocket routes; webhooks and callbacks; file upload and download; server-side fetches of
a configurable URL; command or process execution; template or query construction; deserialization; OAuth and
session handling; API keys and tokens; background jobs and schedulers; message queues; admin or internal
APIs; third-party integrations; data export; cross-tenant queries; caches; client-side rendering of untrusted
data.

Skip the categories the project does not have (a CLI with no server has no CSRF surface). Say which you
skipped and why.

## 2. Authentication and authorization

- Map every entry point to its authentication and its permission check. Prefer generating the list from the
  framework's own route table over reading files by eye.
- Look for: routes without checks; checks placed after the sensitive action; checks that trust a client-
  supplied role, tenant or owner id; privilege that differs between similar endpoints (list vs get vs export).
- Verify with a test as a lower-privileged actor. Expect an explicit refusal, not an empty success.
- Token and session handling: expiry, revocation, refresh, where secrets are stored, what is logged.

## 3. Multi-tenancy and IDOR (only for multi-tenant products)

1. Identify the tenant boundary and where tenant context is derived (token, header, path, subdomain).
   Anything the client can set must be validated against membership.
2. Enumerate every endpoint that accepts a resource identifier. Generate the list from the route table.
3. For each, act as a member of tenant B against real resources of tenant A: read, update, delete, list, nested
   resources, search, export, bulk actions. Also try a spoofed tenant header/id with B's credentials.
4. Include non-HTTP paths: background jobs and their arguments, webhooks, cache keys, queue payloads,
   scheduled tasks, files, search indexes.
5. Accept: refusal (401/403/404) or an empty list. Reject: any 2xx that returns or mutates A's data, and any
   5xx. Confirm A's data is unchanged afterward.
6. A child resource fetched by id alone is a leak unless it is reached through an already-verified parent or
   carries its own tenant filter. Trace the ownership chain for nested resources.
7. Prove the test can fail: temporarily remove one ownership check **in an isolated worktree** and confirm the
   relevant probes go red. Never do this in a shared tree; another process may run against it.
8. Row-level or query-layer guarantees (database policies, scoped repositories) are stronger than convention;
   say which the project has. If documentation claims one and the code does not implement it, that is drift to
   report.

## 4. Server-side request forgery (any server-side fetch of a configurable URL)

Trace, for every such path: **input → validation → storage → URL construction → DNS → connection → redirect
→ final destination.**

- **Find the paths.** Search for every HTTP client and the code that builds its URL. Include SDKs that take a
  base URL, webhook senders, importers, link previewers, media fetchers, model or provider clients, and
  "test connection" buttons. Check background workers use the same guarded path.
- **Who controls the URL?** Untrusted tenant or user configuration, versus explicitly trusted infrastructure
  configuration (environment, operator-only settings). Only the former needs the guard. Do not treat operator
  configuration as hostile, and do not treat tenant input as trusted.
- **Validation.** Scheme allowlist (usually http/https), a host present, no embedded credentials if that
  matters, and no characters that let the caller reshape the request. A query string or fragment on a *base*
  URL can absorb an appended path suffix and give the caller control of the path; check how the client joins
  base and path.
- **Destination policy.** Refuse loopback, `localhost`, private IPv4 ranges, private/unique-local IPv6,
  link-local, multicast, reserved, and cloud metadata addresses, after DNS resolution and including IPv6 and
  IPv4-mapped forms. Treat an unresolvable name as blocked unless the product needs otherwise.
- **Enforcement point.** Check where the connection is made (a request hook or wrapper), not only where the
  URL is saved: stored rows can predate the check, and DNS changes later. Save-time validation is for
  friendly errors; connect-time validation is the boundary.
- **Redirects.** Either disable following, or re-validate every hop and bound the count. Confirm the default
  of each client you find; do not assume.
- **DNS rebinding.** A check followed by a second resolution can be raced. If the check and the connection do
  not share one resolved address, record it as a residual risk unless the connection is pinned to the checked
  IP.
- **Alternate clients.** Grep for every HTTP library in use; a guard on one does not cover another.
- **Response exposure.** What does the caller see? Error bodies, model or list output and connect errors can
  turn a blind request into a read or a port scanner. Trim what is returned.
- **Do not block private addresses blindly.** Find whether private or LAN endpoints are an intended,
  documented feature (self-hosted model servers, internal gateways). If so, keep them working through an
  explicit, privileged mechanism (operator allowlist of named hosts, deployment-level setting), never a
  switch ordinary users can flip. Decide the trust boundary from the code and docs; if it is not knowable,
  stop and ask.
- **Tests.** Public endpoint accepted; each blocked class refused at both save time and connect time; a name
  resolving to a private address refused (stub the resolver); redirect to a private target not followed; the
  privileged allow path works and admits only what it names; a pre-existing stored row is refused.

## 5. Other classes (apply when present)

- **OAuth and third-party sign-in**: redirect URI validation, state and PKCE handling, token storage and
  scope, account linking and takeover paths, what is trusted from the provider's response.
- **Command and process execution**: who can influence the command, arguments or environment; is it gated to
  a trusted role; is the gate re-checked at execution time, not only at registration.
- **Injection**: parameterized queries only; no string-built SQL, shell, template or path from input; safe
  rendering of untrusted content (XSS), including in markdown and rich text.
- **Uploads**: type and size limits, storage path safety, no execution of uploaded content, parser hardening.
- **Webhooks (inbound)**: signature verification, replay protection, constant-time compare, rate limits.
- **Webhooks and callbacks (outbound)**: SSRF guard, timeouts, no secrets in URLs or logs.
- **CSRF/CORS/cookies**: cookie flags, origin allowlists, credentialed CORS, state-changing GETs.
- **Secrets**: none committed, none in logs or error messages, encrypted at rest where claimed, rotation path
  exists. Scan the diff and history you are responsible for.
- **Background jobs**: tenant context preserved, arguments not trusted, idempotency, no privilege escalation
  by scheduling.
- **Dependencies**: run the documented audit command if there is one; report findings, do not upgrade
  unprompted.
- **Rate limiting and resource abuse** on public or expensive endpoints.

## 6. Guarding against bypass of a recent fix

After a security fix, search for the same bug class on other paths: other callers of the same sink, other
writers of the same field, other clients, workers, admin and internal APIs, WebSocket variants. Report what
you find. Fix it only if it is the same small, behavior-preserving change; otherwise present it as a finding.
Also check that stale docs do not claim coverage that the code lacks.

## 7. Designing a fix that changes behavior

- State the finding as a concrete path, who controls each step, and what an attacker gains.
- List the legitimate uses the fix could break and how they would be supported.
- Prefer a control owned by whoever operates the deployment and enforced at the point of use over one keyed
  to "who created the record", which a lower-privileged actor may be able to edit.
- Keep the policy central (one guard module), not scattered `if` checks in each caller.
- Say what behavior changes, for which endpoints and existing data, and how an operator restores a legitimate
  setup. Add regression tests that fail without the fix.

## 8. Reporting a serious finding

Report it as soon as you are confident, separate from any longer report: path, actor, impact, evidence, and
a recommended option. Do not bury it.
