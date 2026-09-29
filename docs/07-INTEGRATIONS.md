# 07 — Integrations (n8n, Channels, Widget, Webhooks, Connectors)

## 1. n8n (local Docker) — first-class automation partner

### Runtime
- n8n runs in Docker at `N8N_BASE_URL` (default `http://n8n:5678` inside compose,
  `http://localhost:5678` from host). Add it to `infra/docker-compose.yml`.
- Auth to n8n's REST API via `N8N_API_KEY` (n8n public API) for listing/reading workflows.
- Assume the user may already run n8n; support pointing at an external instance via env.

### Two-way integration
**Vicero → n8n (agent triggers automations):**
- Bind an n8n workflow (that starts with a **Webhook** node) as a Vicero **n8n tool**.
- When the agent calls the tool, `integrations/n8n_client` POSTs the tool arguments to the
  workflow's webhook URL. Sign the request (HMAC header `X-Vicero-Signature`).
- **Sync mode**: n8n's "Respond to Webhook" node returns JSON → fed back to the model.
- **Async mode**: n8n does long work, then calls back Vicero's callback endpoint with the
  `run_id`; runtime resolves the pending tool call.
- Discovery: `GET /v1/tools/n8n/workflows` proxies n8n's API to list workflows so the user
  can pick one in the UI, then `POST /v1/tools/n8n/bind`.
- **Multi-tenant scoping:** one n8n instance is shared across every org, so discovery filters by
  n8n **tag**: a workflow tagged with an org's slug is visible only to that org; a workflow
  tagged `internal`/`shared-internal`/`platform-internal` is hidden from every org unconditionally
  (platform-owned workflows must never be bindable by a client). Untagged workflows stay visible
  to all orgs — a permissive default, not "properly scoped." See `docs/guides/N8N-SETUP.md §5`
  and ADR-040.

**n8n → Vicero (workflows use Vicero):**
- n8n calls Vicero's REST API using an org **API key** (`bf_...`) — e.g., send a message
  to a conversation, fetch analytics, trigger an agent.
- Vicero emits **outbound webhooks** (see §4) that n8n workflows subscribe to via Webhook
  nodes (e.g., on `handoff.requested`, create a ticket).

### Webhook signature verification — REQUIRED for every n8n workflow Vicero calls
Vicero signs every call to an n8n webhook, but signing only protects a workflow that **checks** the
signature. An unverified webhook can be called by anyone who learns its URL, and that call bypasses the
agent, RBAC, budget limits and any input validation entirely. So:

- **Every workflow bound as an n8n tool must verify the signature before doing anything.** The reference
  implementation is the first three nodes of every JSON in `infra/n8n/`: **Webhook** (option `rawBody: true`)
  → **Verify Vicero signature** (Code) → **Signature valid?** (IF; the false branch is a **Respond to
  Webhook** with HTTP **401**). Do not reinvent it; copy those nodes.
- **Scheme** (`integrations/n8n_client.sign()` / `verify_callback()` — the workflow mirrors the same
  rules): `X-Vicero-Signature` = hex `HMAC-SHA256(secret, "<X-Vicero-Timestamp>." + raw request body)`.
  Compare in constant time, over the **raw bytes** (re-serialising the parsed JSON changes spacing and
  escaping and breaks the MAC), and reject a timestamp more than **300 s** from now (replay window, same as
  the callback verifier).
- **Fail closed.** No secret configured, missing headers, stale timestamp, unavailable raw body or a bad MAC
  all answer 401. A workflow must never fall through to "accept" because something is unset.
- **The n8n container must be given the secret.** `N8N_WEBHOOK_SIGNING_SECRET` (the root `.env` value — one source, never a copy) plus
  `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and `NODE_FUNCTION_ALLOW_BUILTIN=crypto` — n8n 2.x blocks both `$env`
  and `require('crypto')` in Code nodes by default. The dev compose sets all three; **a production n8n
  needs them too** (`docker-compose.prod.yml` has no n8n service — an external n8n must be configured the
  same way). Tradeoff: with env access on, any workflow author on that n8n can read its environment, so the
  instance must be Vicero-operated and its workflows staff-authored.
- **Provisioning enforces it.** `scripts/provision-client.mjs` clones `TEMPLATE — Starter Automation` per
  client and **refuses to clone a template without the `Verify Vicero signature` node**, so a client
  workflow cannot be created unsigned by following the normal path. A workflow built by hand in the n8n UI
  is *not* covered by that check — see RISK-REGISTER R15, which stays open until every workflow bound to a
  tool has been confirmed verified.
- **Callbacks** (async mode, n8n → Vicero) are the mirror image and are already verified server-side
  (`verify_callback`).
- Pinned by `apps/api/tests/test_n8n_workflows_signed.py`: every workflow JSON under `infra/n8n/` must
  carry the verification chain and no secret or runtime state.

### n8n client responsibilities (`integrations/n8n_client.py`)
- `list_workflows()`, `get_workflow(id)`, `trigger_webhook(url, payload, signed=True)`,
  `verify_callback(signature, body)`. Timeouts, retries with backoff, structured logging.

### Provide starter n8n workflows (export JSON in `infra/n8n/`)
- "Create support ticket" (webhook → HTTP/DB node → respond).
- "Send email on handoff" (Vicero webhook → email node).
- `template-starter-automation.json` — **`TEMPLATE — Starter Automation`**, the workflow the
  provisioning script clones per client (Webhook → Set → Respond to Webhook). Deliberately
  thin: its job is to give a new client *a working automation* on day one, which real logic
  then replaces. `handled_by` is `{{ $workflow.name }}`, so each clone identifies itself
  rather than reporting the template's name.
- Document how to import them in `09-DEPLOYMENT.md`.

### One-command client provisioning (`scripts/provision-client.mjs`)

```
node scripts/provision-client.mjs --name "Acme Co" --email owner@acme.com --plan starter
```

Stands a client up end to end: organization → agent (starter persona, Groq, tools enabled) →
**published** so it's live before the client ever logs in → the n8n starter automation cloned,
activated and bound as an agent tool → the client invited as `editor` (the client role: edit,
test and connect their own channels, but never publish).

- **Staff login, not an API key.** Org creation is gated on `User.is_staff`; no key scope
  grants it. Hence `PROVISION_STAFF_EMAIL`/`PROVISION_STAFF_PASSWORD`.
- **Idempotent and resumable.** Every step looks before it creates, so re-running after a
  failure resumes rather than duplicating. Two guarantees worth naming: a *pending* invitation
  suppresses a second email (`create_invitation` only rejects an already-**active** member, so
  the script has to check), and an agent that already carries a system prompt is left strictly
  alone — by then it may hold the client's own persona edits, and provisioning must not
  overwrite their work or push their unreviewed draft live.
- **Per-client webhook path.** Each clone gets `path = {slug}-starter-automation`. Cloning the
  template verbatim would point every client's tool at one shared URL, so whichever workflow
  n8n resolved first would answer everyone — a cross-client leak, not a mix-up.
- **Tool binding goes through `POST /v1/tools/n8n/bind`**, which resolves the production
  webhook URL from the workflow itself, so the URL is derived by the same code the runtime
  uses instead of being reconstructed by the script.
- **Location-agnostic.** It only talks to `VICERO_API_BASE_URL` and `N8N_BASE_URL`, so the
  identical command works over SSH against a VPS: `ssh you@vps "cd own_chatbot && node
  scripts/provision-client.mjs --name ... --email ..."`.

Note for production: `infra/docker-compose.prod.yml` sets `N8N_BASE_URL: http://n8n:5678` but
**defines no `n8n` service**, so provisioning against a prod stack needs that service added
first (internal-only, no published port).

## 2. Channels

Each channel = an inbound webhook (receive user messages) + an outbound sender (deliver bot
replies), mapped to a `channels` row and its agent. All inbound endpoints verify signatures.

### Web widget (default, always available)
See §3.

### Telegram
- Config: `bot_token`. On enable, set the bot's webhook to
  `/v1/channels/telegram/{channelId}/webhook` (with a secret token).
- Inbound: parse update → resolve/create conversation keyed by Telegram chat id → run chat →
  send reply via `sendMessage`. Support typing action, basic markdown.

### WhatsApp (Meta Cloud API; Twilio as alt)
- Config: `phone_number_id`, `access_token`, `verify_token`, `app_secret`.
- `GET` webhook: respond to Meta's verification challenge.
- `POST` webhook: verify `X-Hub-Signature-256`, parse message → chat → reply via Graph API
  `messages` endpoint. Handle 24-hour window / templates note in docs.

### Facebook Messenger + Instagram DMs (Meta Messenger Platform)
Meta unified these two behind one Send API, so Vicero implements them once
(`channels/meta_messaging.py`) with a thin subclass each. They share the WhatsApp app
secret and its `X-Hub-Signature-256` check (`channels/meta_signature.py`).

- Config: `page_access_token`, `verify_token`, `app_secret`, plus `page_id` (Messenger) or
  `ig_user_id` (Instagram).
- `GET` webhook: the same `hub.challenge` handshake as WhatsApp.
- `POST` webhook: verify the signature, then parse `entry[].messaging[]`. A delivery is only
  accepted if its `object` matches the adapter's surface (`page` vs `instagram`), so one Meta
  app feeding several channels never cross-attributes a conversation. Read receipts, delivery
  confirmations, and `is_echo` (our own outbound) are ignored.
- Outbound: `POST /v19.0/me/messages` with the page token as a Bearer header.
- Identity: `fetch_profile` resolves a name + photo via the Graph API — `first_name`,
  `last_name`, `profile_pic` for Messenger; `name`, `username`, `profile_pic` for Instagram
  (needs `instagram_manage_messages`). Called only while the contact is still missing a name
  or avatar, so an active thread costs one lookup, not one per message.
- **Not included:** public post-comment moderation (the "Facebook comments" / "Instagram
  comments" surfaces). Different webhook fields, permissions, and reply semantics — see
  ADR-037.

### Slack
- Config: bot token, signing secret. Verify Slack signature. Handle `event_callback`
  (app_mention / message.im) → chat → `chat.postMessage`. Support Slack markdown.

### Discord
- Config: bot token / application public key. Verify Ed25519 signature on interactions →
  chat → respond. (Gateway/bot mode optional; start with interactions/webhook.)

### Public REST channel
- Any external system posts to `/v1/public/agents/{public_key}/chat` with rate limiting.

### Channel abstraction
Implement a `Channel` interface: `verify(request)`, `parse_inbound(request) -> InboundMsg`,
`send(conversation, text, attachments)`, and an optional
`fetch_profile(channel, external_id) -> ContactProfile | None`. Register per type. Keeps
the chat runtime channel-agnostic.

### Contact identity
Every inbound path — channels *and* the widget — resolves the sender to a `contacts` row keyed
`(organization_id, channel, external_id)` before the turn runs, and points the conversation at
it. Names and avatars refresh when a platform sends something fresher; a blank value never
overwrites a known one. That's what lets the unified inbox show a person instead of a PSID.
Identity is per-channel by design: the same human on Instagram and WhatsApp arrives with two
unrelated ids and no reliable way to link them.

## 3. Embeddable web widget (`packages/widget`)

### Embed
```html
<script src="https://YOUR_HOST/widget.js" data-agent="PUBLIC_KEY" defer></script>
```
- Loads config from `GET /v1/public/agents/{public_key}/config` (theme, colors, position,
  launcher, welcome message, suggested prompts, branding).
- Renders a launcher bubble + panel in a Shadow DOM (style isolation).
- Chats over `WS /v1/public/agents/{public_key}/ws` (fallback SSE), streaming tokens.

### Features (FR-G2)
Typing indicator, quick replies/suggested prompts, markdown rendering (sanitized), file
upload, message history in session, custom colors/logo/position (bottom-right/left),
"powered by" toggle, mobile responsive, keyboard accessible, RTL support.

### Build
Bundled with a small toolchain (esbuild/vite) to a single minified `widget.js` + `widget.css`,
served by the web app (or a CDN path). No heavy framework in the bundle; keep it lightweight.

### Widget SDK (JS API)
Expose `window.Vicero = { open(), close(), sendMessage(text), on(event, cb),
setUser({id, name, email, metadata}) }` so host pages can control it and pass visitor identity.

## 4. Outbound webhooks (Vicero → external / n8n)

- Configurable endpoints (`webhook_endpoints`) subscribe to events.
- Delivery: enqueue → POST signed payload (`X-Vicero-Signature` = HMAC-SHA256 of body with
  endpoint secret, plus timestamp) → retry with exponential backoff, record in
  `webhook_deliveries`.
- Event catalog: `message.created`, `conversation.created`, `conversation.closed`,
  `handoff.requested`, `handoff.resolved`, `document.ready`, `document.failed`, `tool.run`,
  `usage.threshold`.
- Provide an HMAC verification recipe in docs for consumers.

## 5. Connector suggestions & MCP (forward-looking)

- The HTTP-tool + n8n-tool mechanism already lets agents reach Gmail, Slack, Sheets, Stripe,
  HubSpot, Salesforce, etc. **through n8n nodes** — favor that path over bespoke code.
- Optionally expose an **MCP-compatible** tool bridge so agents can use MCP servers later
  (stretch; note in roadmap).

## 6. Security for all integrations
- Every inbound webhook: signature verification + timestamp/replay protection.
- Every outbound call: HMAC signing, TLS, SSRF guards (block private/link-local/metadata IPs),
  timeouts, size limits.
- Channel tokens and n8n keys stored encrypted; masked in API responses.
