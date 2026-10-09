# ENV.md — Environment Variables

Every variable Vicero reads. Mirror these as placeholders in `.env.example`. "Needs human"
= only the human can create it; if unset, the code must stub the feature, log a loud warning,
and keep building (`CLAUDE.md §7`).

## Writing `.env` — one formatting rule

**A comment must never share a line with a blank value.**

```dotenv
# [HUMAN] Google Gemini free tier      ← correct: comment on its own line
GEMINI_API_KEY=

GEMINI_API_KEY=      # [HUMAN] ...     ← WRONG: the comment becomes the value
```

`KEY=value  # note` is fine and parses to `value` — the loader strips a comment only when
something precedes it. On a blank line the spaces after `=` are eaten as the separator, so the
`#` starts the value and the whole comment is read as the secret. That sent a placeholder to
Google as a real API key and took a live agent down silently (ADR-044).

`Settings` now drops comment-only values defensively, so an existing `.env` in the old shape
still behaves. Do not rely on it: `.env` is also passed to containers via compose's `env_file`,
which parses the file with its own rules that the Python fix cannot reach.

## Core
| Var | Purpose | Required | Default | Needs human |
|---|---|---|---|---|
| `ENV` | dev/test/prod | yes | dev | no |
| `SECRET_KEY` | JWT signing + key encryption | yes | — | yes (generate) |
| `DATABASE_URL` | Postgres async DSN | yes | compose default | no |
| `POSTGRES_HOST_PORT` | host port the bundled Postgres binds (default `5432`); must agree with `DATABASE_URL`. **Compose-only**, same as `N8N_HOST_PORT` — read from the shell or `infra/.env` (machine-specific ports only; secrets live in the root `.env`) | no |
| `REDIS_URL` | Redis DSN. In dev it carries the password: `redis://:<REDIS_PASSWORD>@127.0.0.1:6379/0` (use `127.0.0.1`, not `localhost` — the containers publish on IPv4 loopback only) | yes | compose default | no |
| `REDIS_PASSWORD` | Password for the dev Redis; compose refuses to start without it. Use the same value in `REDIS_URL`. | dev | — | yes |
| `API_BASE_URL` / `WEB_BASE_URL` | absolute URLs | yes | localhost | no |

`POSTGRES_HOST_PORT` exists for the same reason as `N8N_HOST_PORT`: on a machine running more
than one project, 5432 is usually already bound. The tempting shortcut — leaving `DATABASE_URL`
on 5432 and using whatever Postgres answers there — is the dangerous one, because
`alembic upgrade head` would then migrate another project's database. Move Vicero's own
instead (`POSTGRES_HOST_PORT=5433`, `DATABASE_URL=...@localhost:5433/vicero`); the container
port and the `pgdata` volume are unchanged, so no data moves with it.

## LLM providers (free-first)
| Var | Purpose | Needs human |
|---|---|---|
| `GROQ_API_KEY` | default provider | yes (free signup) |
| `GEMINI_API_KEY` | Google Gemini free tier | yes |
| `OPENROUTER_API_KEY` | free models via OpenRouter | yes |
| `OLLAMA_BASE_URL` | local models + embeddings | no (bundled) |
| `PROVIDER_PRIVATE_HOSTS` | comma-separated private/loopback hostnames or IPs a tenant-set provider endpoint (Custom endpoint, or a URL override on Ollama/Mistral/…) may use. Default empty = public endpoints only (ADR-087). Operator-only | no |
| `OPENAI_API_KEY` | paid | yes |
| `ANTHROPIC_API_KEY` | paid | yes |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | default embeddings | no |
| `EMBEDDING_PROBE_ENABLED` | startup reachability check for the above | no (default on) |

## Production compose interpolation (not container env)

⚠️ **These are read by Compose itself, not by the app, and they do NOT come from `.env`.** Compose
resolves `${VAR}` from the shell and from `infra/.env` — never from the `../.env` that
`env_file:` gives the containers. Pass `--env-file ../.env` or put them in `infra/.env`; see
`docs/09` §3, which had this wrong until 2026-08-17.

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD`, `DOMAIN`, `API_DOMAIN`, `ACME_EMAIL`, `NEXT_PUBLIC_API_BASE_URL` | required; compose refuses to start without them |
| `NEXT_PUBLIC_N8N_URL` | public URL of the n8n editor, used by the dashboard's "Open n8n" link on the Automations page (default `http://localhost:5678`). **Baked into the web image at build time**, like `NEXT_PUBLIC_API_BASE_URL` - rebuild `web` after changing it | no |
| `POSTGRES_HOST_PORT`, `N8N_HOST_PORT` | host ports, for machines where 5432/5678 are taken |
| `POSTGRES_MEM_LIMIT` (3g), `WORKER_MEM_LIMIT` (3g), `OLLAMA_MEM_LIMIT` (4g), `API_MEM_LIMIT` (2g), `REDIS_MEM_LIMIT` (1g), `WEB_MEM_LIMIT` (1g), `MIGRATE_MEM_LIMIT` (1g), `BEAT_MEM_LIMIT` (512m), `BACKUP_MEM_LIMIT` (512m), `CADDY_MEM_LIMIT` (256m) | per-service memory ceilings (docs/15 PROD-3) |
| `POSTGRES_MEM_RESERVATION` (1g), `REDIS_MEM_RESERVATION` (256m) | soft floors — what actually protects the datastores under host pressure |

Defaults target a 16 GB VPS. **They are ceilings, not a budget, and sum to more than the box has**
on purpose. ⚠️ **Set one too low and that service crashloops** — check
`docker inspect <container> --format '{{.State.OOMKilled}}'` and raise it.

**Embeddings are the one provider with no fallback**, so two things about them are worth knowing
before a deploy (docs/15 PROD-2):

- **`EMBEDDING_PROVIDER=openai` and `=gemini` do not do what they say.** `build_embedding_provider`
  accepts both names and constructs an *Ollama* client regardless — no adapter for either was ever
  written. Setting one to work around a missing Ollama changes nothing, and there is no symptom
  that distinguishes it from having worked, so startup logs `embedding_provider_has_no_adapter`.
- **`EMBEDDING_MODEL` implies a vector dimension.** `chunks.embedding` is `vector(768)`, which is
  `nomic-embed-text`. A 1536-dimension model needs a migration, not just this variable.
- `EMBEDDING_PROBE_ENABLED` (default on) does one `GET /api/tags` at startup and logs
  `embedding_provider_unreachable` or `embedding_model_missing` — error level under `ENV=prod`,
  warning elsewhere, since a dev machine without Ollama running is normal. It never fails startup
  and is capped at ~2s. The test suite disables it so the suite does no network I/O.
  **`ollama/ollama` starts with no models**, so "the port answers" is not evidence that embedding
  works; that is why the probe checks for the model and not just the endpoint.

## OAuth
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FACEBOOK_CLIENT_ID`, `FACEBOOK_CLIENT_SECRET`,
`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` — all needs-human (create OAuth apps). Unset → that
provider's authorize call answers `501 auth.oauth_not_configured` and the login button reports
"isn't set up on this server yet". Redirect URI for each is
`{OAUTH_REDIRECT_BASE}/v1/auth/oauth/{provider}/callback`. Setup steps: `docs/18-SELF-SERVE-PLAN.md` §7.

## Self-serve signup + free trial (docs/18, ADR-088)
| Variable | Default | Meaning |
|---|---|---|
| `SELF_SERVE_ENABLED` | `true` | Signup provisions one 10-day trial workspace and a non-staff user may own one. `false` = staff-provisioned only (the pre-S1 behaviour) and the kill switch for abuse. Turn it **off** for the keyless E2E API so other specs keep bootstrapping their own tenants. |
| `SIGNUPS_PER_IP_PER_DAY` | `3` | New self-serve accounts per client IP per 24h. `0` disables. Needs the client's real address: see `TRUSTED_PROXIES`. When the client can't be identified (local dev) the cap steps aside rather than pooling everyone. |
| `TRUSTED_PROXIES` | `127.0.0.1,::1` | Comma-separated IPs/CIDRs of reverse proxies whose `X-Forwarded-For` the API believes (ADR-092). The compose files add Docker's private range (`172.16.0.0/12`). Read from the right; ignored from any other peer. **Never a public range.** |
| `BLOCK_DISPOSABLE_EMAILS` | `true` | Refuse signups whose domain is on the throwaway list (a soft signal — email verification is the control; fails open). |
| `DISPOSABLE_LIST_URL` | `…/disposable-email-domains/…/disposable_email_blocklist.conf` | Source of the maintained throwaway-domain list, refreshed weekly by Celery beat into Redis (`disposable.refresh`). The short built-in list is the offline fallback (ADR-091). |
| `DISPOSABLE_MX_CHECK_ENABLED` / `DISPOSABLE_MX_TIMEOUT` | `true` / `2.0` | Look up a new address's mail servers and *record* (never block on) missing or throwaway ones. Time-bounded, fail-open. |
| `LOGIN_LOCKOUT_FAILURES` | `10` | Failed password logins per email before lockout. |
| `LOGIN_LOCKOUT_WINDOW` | `900` | Lockout window, seconds. |
| `ORG_CHAT_RATE_LIMIT` | `30` | Public chat messages per minute per metered organization; excess is dropped silently and not counted. |
| `ORG_CHAT_RATE_WINDOW` | `60` | Window for the above, seconds. |
| `NEXT_PUBLIC_UPGRADE_EMAIL` | *(unset)* | Web app: address on the `/billing/upgrade` page. |
| `NEXT_PUBLIC_UPGRADE_WHATSAPP` | *(unset)* | Web app: WhatsApp number (digits, with country code) on the upgrade page. |

Plan **limits** (10 days, 500 messages, one agent/workspace, locked features) are deliberately
**not** env vars: they live in one table, `apps/api/app/core/plans.py`.

## Channels
`TELEGRAM_BOT_TOKEN`; Slack `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`; Discord
`DISCORD_BOT_TOKEN`, `DISCORD_PUBLIC_KEY`. All needs-human; unset → channel disabled with a
clear message.

### Meta surfaces (WhatsApp, Messenger, Instagram)
All three ride one Meta app, so they share `META_APP_SECRET` — the key behind the
`X-Hub-Signature-256` check every inbound delivery must pass.

| Var | Surface | Needs human |
|---|---|---|
| `META_APP_SECRET` | all three — webhook signature verification, and the `signed_request` check on the Data Deletion Request Callback (`POST /api/meta/data-deletion`; unset → 503) | yes |
| `META_VERIFY_TOKEN` | all three — echoed back during Meta's `hub.challenge` handshake | no (you choose it) |
| `WHATSAPP_PHONE_ID`, `WHATSAPP_TOKEN`, `WHATSAPP_VERIFY_TOKEN` | WhatsApp Cloud API | yes |
| `META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN` | Facebook Messenger DMs + profile lookups | yes |
| `INSTAGRAM_USER_ID`, `INSTAGRAM_PAGE_ACCESS_TOKEN` | Instagram DMs; needs `instagram_manage_messages` | yes |

These are reference values for the human — each channel's real credentials are entered per
agent in **Builder → Channels** and stored encrypted in `channels.config`. Unset → the
adapter logs a loud warning and skips sending/profile lookups, but still accepts and
persists inbound messages (CLAUDE.md §7).

Profile/avatar support differs by platform, and that's the platform's doing, not a gap:
Messenger and Instagram return a name *and* photo via the Graph API; WhatsApp Cloud API
sends a profile name but has no photo endpoint; Telegram carries the name in the update but
serves photos from token-bearing URLs we deliberately never persist (see ADR-036).

## n8n
| Var | Purpose | Needs human |
|---|---|---|
| `N8N_BASE_URL` | n8n REST/webhook base (default `http://n8n:5678`) | no |
| `N8N_HOST_PORT` | host port the bundled n8n binds (default `5678`); must agree with `N8N_BASE_URL`. **Compose-only** — read from the shell or `infra/.env`, not from the root `.env` (which is passed to containers via `env_file` and so never reaches `${...}` interpolation) | no |
| `N8N_API_KEY` | n8n public API auth — needs the workflow read/list/create/update/activate scopes | yes (from n8n UI) |
| `N8N_WEBHOOK_SIGNING_SECRET` | sign Vicero→n8n calls; **n8n workflows verify it** (docs/07 "Webhook signature verification"). **Lives only in the root `.env`** — the n8n container gets it by compose interpolation, so start with `make up` / `--env-file .env --env-file ../.env`, never a copy in `infra/.env`. Without it n8n has an empty secret and rejects every call (fail closed) | generate |
| `N8N_REQUIRE_SIGNATURE_CHECK` | `true` (default): binding an n8n tool is refused unless its workflow verifies Vicero's signature (RISK-REGISTER R15); a pasted webhook URL is resolved via the n8n API, so it needs `N8N_API_KEY`. `false` only for a deliberately unsigned dev n8n. Audit existing binds: `GET /v1/admin/n8n-signature-audit` | no |
| `AUTOMATION_REPORT_SECRET` | HMAC secret n8n signs run reports with (`POST /internal/automations/runs`). **Unset = the endpoint answers 503 and records nothing** (it never falls back to another secret). Production: generated by `ops/n8n-setup.sh` | generate |
| `N8N_REQUIRE_REGISTERED_AUTOMATION` | `true` (default): an n8n tool can be bound to / run a webhook only if it belongs to a registered, **Active** automation of the same org and agent, and only while the plan's monthly run cap allows. `false` only for keyless CI / a dev n8n with no registry | no |
| `AUTOMATION_PULL_ENABLED` | Celery beat job every 5 min that fills gaps in the run log from the n8n API (registered workflows only). Needs `N8N_API_KEY` | no |
| `N8N_DOMAIN` | Public host of the **staff-only** n8n editor (Caddy). `/webhook*` is never served there | prod only |
| `N8N_DB_PASSWORD` | Password of the `n8n` role in the existing Postgres. Generated by `ops/n8n-setup.sh` | generate |
| `N8N_ENCRYPTION_KEY` | Encrypts every credential saved inside n8n. **Losing it makes them unreadable** — keep a copy in a password manager. Generated by `ops/n8n-setup.sh` | generate |
| `N8N_EDITOR_GATE_USER` / `N8N_EDITOR_GATE_HASH` | The second password in front of the n8n editor (Caddy `basic_auth`; the hash is bcrypt, single-quoted in `.env`). Set by `ops/n8n-setup.sh` | you type the password |
| `N8N_VERSION` / `N8N_MEM_LIMIT` | Optional compose overrides: the pinned n8n image tag (default `2.43.1`) and its memory ceiling (default `768m`) | no |

`N8N_HOST_PORT` exists because 5678 is often already taken — on the build machine by an
unrelated n8n belonging to another project. Vicero must not create or activate workflows in
someone else's instance, so point it at its own (`N8N_HOST_PORT=5679`,
`N8N_BASE_URL=http://localhost:5679`). n8n 2.x also binds its editor session cookie to a
`browser-id` header, so minting an API key over `/rest/*` requires sending one; the UI
(Settings → API) is the simpler route.

## Client provisioning (`scripts/provision-client.mjs`)

| Var | Purpose | Needs human |
|---|---|---|
| `VICERO_API_BASE_URL` | which Vicero to provision into (default `http://localhost:8000`) | no |
| `PROVISION_STAFF_EMAIL` | a login with `is_staff=true` | yes |
| `PROVISION_STAFF_PASSWORD` | that account's password | yes |

The script signs in as a **staff user** rather than using a `bf_…` API key: creating an
organization is gated on `User.is_staff` server-side and no key scope grants it. Point
`VICERO_API_BASE_URL`/`N8N_BASE_URL` at localhost today and at the VPS later — that is the
only difference between provisioning on a laptop and provisioning in production.

## Email

Four emails go out: organization invitations, signup verification, password reset, and
magic-link sign-in. All four funnel through one backend chosen by `EMAIL_BACKEND`.

| Var | Purpose | Needs human |
|---|---|---|
| `EMAIL_BACKEND` | `console` (in-memory outbox, dev/test default) or `smtp` (real delivery) | no |
| `SMTP_HOST` | relay hostname, e.g. `smtp.resend.com` | yes |
| `SMTP_PORT` | relay port; blank is read as the default `587` (STARTTLS) | no |
| `SMTP_USER` / `SMTP_PASS` | relay credentials from your provider | yes |
| `SMTP_FROM` | envelope sender, e.g. `Vicero <noreply@yourdomain.com>` | yes |

**Plain SMTP on purpose.** Resend, Postmark, SendGrid, Mailgun and Amazon SES all expose an
SMTP relay with exactly these settings, so changing provider is an env change and never a code
change. Resend or Postmark are the easiest starting points for a solo operator.

**Code alone does not get you delivered mail.** You still have to verify your sending domain
with the provider (SPF/DKIM DNS records — their dashboard walks you through it). Without it the
receiving server has no way to tell your mail from a spoof, and Gmail will bin it.

With `EMAIL_BACKEND=smtp`, sends run on the **Celery worker**, so a slow or dead relay can never
hang or 500 a signup/invite/reset request; failures retry with backoff. `console` sends inline —
there is nothing to protect the request from — which is also what keeps the test outbox
synchronous. If `SMTP_HOST`/`SMTP_FROM` are unset while the backend is `smtp`, a send raises
`email.not_configured` rather than silently dropping the message.

## Private admin area (`/vault`) — ADR-096

A sign-in that is **separate from Vicero accounts** and shows the internal reference plus the
real value of every setting in `.env.example`. Off until all three of the first rows are set.

| Var | Purpose | Needs human |
|---|---|---|
| `VAULT_ADMIN_EMAILS` | comma-separated allow-list; exact addresses only, case-insensitive | yes |
| `VAULT_PASSWORD_HASH` | scrypt hash from `make vault-password` — never the password | yes |
| `VAULT_SESSION_SECRET` | 32+ random characters; signs the 8-hour session cookie | yes |
| `VAULT_ENV_FILE` | optional `KEY=value` file to read values from (see below) | no |

Setup: `make vault-password` (asks for a password twice, hidden, prints two lines) → paste them and
`VAULT_ADMIN_EMAILS` into the root `.env` → restart the web app → sign in at `/vault/login`.

**What it can show depends on what the web process can see.** The dev compose gives the web
container the whole root `.env`, so every value resolves. The production compose passes it only
`API_INTERNAL_URL` and `NODE_ENV`, so almost every entry reads *not set* there. To show them, set
`VAULT_ENV_FILE` to a read-only mount of the root `.env`. That puts every API secret inside the web
container, which is a real widening of what a web-tier compromise exposes — a deliberate choice for
the operator, so the production compose does not do it by default.

The `VAULT_*` names are excluded from the vault's own listing and can never be revealed by it.
Changing `VAULT_SESSION_SECRET` (or removing an address from `VAULT_ADMIN_EMAILS`) ends the
affected sessions on their next request.

## Billing (optional)
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_*` — needs-human; unset → billing
disabled.

### Display currency by visitor country — ADR-106
`TRUST_GEO_HEADERS` (default `false`) and `GEO_COUNTRY_HEADERS` (default
`CF-IPCountry,X-Vercel-IP-Country,CloudFront-Viewer-Country,X-Country-Code`) choose the currency
`GET /v1/billing/plans` *shows* (IN → INR, EU27 → EUR, else USD). **Display only** — never what is
charged or which plan an org gets. Set `TRUST_GEO_HEADERS=true` in production only when (1) the
production proxy forwards exactly one of those headers, and (2) the API is reachable **only**
through that proxy — otherwise any client can send the header itself. `?currency=USD|EUR|INR`
always wins over the header. `XX` / `T1` (Cloudflare unknown / Tor) are treated as unknown → USD.

`GEOIP_DB_PATH` (default empty = off) enables an **offline IP → country fallback** for when no proxy
header supplies a country (e.g. plain Caddy on the VPS, no Cloudflare). Get the free DB-IP "country
lite" file with `python scripts/fetch_geoip_db.py [path]` (no account or key; refresh monthly; CC BY 4.0,
so the pricing page carries the "IP Geolocation by DB-IP" credit). The visitor address comes from
`X-Forwarded-For` only via `TRUSTED_PROXIES` (docs/SECURITY.md §11); loopback/private addresses are
unknown → USD. A missing or unreadable file is logged and ignored. Precedence: `?currency=` → trusted
header → this lookup → USD. Still **display only**.

## Observability
`SENTRY_DSN` (optional), `LOG_LEVEL` (default info).

## Storage & RAG
`STORAGE_BACKEND=local|s3`; if s3: `S3_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY` — needs-human
for prod; local default in dev.

- `UPLOAD_DIR` (default `./var/uploads`) — filesystem path where ingested document files are
  stored under the local backend. Created on demand; gitignored.
- `RAG_CONTEXT_CHAR_BUDGET` (default `8000`) — ceiling on characters of retrieved context
  injected into a prompt (token budgeting; oldest/lowest-ranked citations are trimmed first).
- `RAG_RRF_FTS_WEIGHT` (default `0.05`) — weight of the keyword (Postgres FTS) list in the
  hybrid RRF fusion; the dense list is fixed at 1.0. Textbook RRF weights both equally, which
  assumes comparable retrievers; measured on the frozen eval corpus they are not (dense NDCG@10
  0.898 vs keyword 0.660) and equal weighting put hybrid *below* dense-only. **0.05 is a
  conservative floor, not a fitted optimum** — see ADR-058 for the full sweep and what it does
  not establish. Re-fit with `make eval-retrieval-full` against a real client knowledge base.

### Docling extraction — docs/14 K1

Layout-aware extraction replacing `pypdf`'s flat text stream: real headings, reading order,
table structure and OCR. Runs in the Celery ingest worker, so it **cannot** affect chat latency
— the risk here is a bad extraction, not a slow reply. **Off by default**; roll out per
docs/14 §12, where every step back is a flag flip because `LegacyConverter` is never deleted.

- `DOCLING_ENABLED` (default `false`) — the switch.
- `DOCLING_ENDPOINT` (default empty) — base URL of a `docling-serve` instance. **Internal
  only. Never publish its port** (docs/14 §9): it accepts arbitrary documents and URLs, so a
  public port is SSRF plus resource exhaustion. The dev compose file sets this on the worker as
  `http://docling:5001` and gives the service `expose:` rather than `ports:`.
  **Enabled with this empty warns at startup** and silently takes the legacy path.
- `DOCLING_DO_OCR` (default `true`) — turns `scanned PDF → LoaderError: no extractable text`
  into a working document.
- `DOCLING_DO_TABLE_STRUCTURE` (default `true`) — reconstructs row/column relationships. Without
  it a pricing table becomes word soup and numbers lose their row, which is the content shape
  that produces confidently wrong price answers.
- `DOCLING_TIMEOUT_SECONDS` (default `120`) — Docling's own guidance is 90–120s. On timeout the
  document falls back to the legacy extractor rather than failing.
- `DOCLING_CHUNK_MAX_TOKENS` (default `512`) — token budget per chunk on the structural
  chunking path. **Tokens, not characters**: 1000 characters is ~250 tokens of English and
  ~800 of Tamil, and only one of those fits an embedding window predictably. Sweepable without
  re-converting anything — `scripts/eval_retrieval.py --chunk-max-tokens` — which is the whole
  point of persisting the `DoclingDocument`.
- `MAX_PDF_PAGES` (default `800`, `0` disables) — refuses a PDF longer than this at ingest, with
  the page count *and* the limit in the failure message. A 500-page PDF otherwise fails slowly
  rather than cleanly: it holds the worker for the whole `DOCLING_TIMEOUT_SECONDS`, times out,
  falls back to the legacy extractor, and the client gets a structureless document minutes later.
  Checked on the file, URL and re-ingest paths alike, because they converge on one function.
- `DOCLING_CHUNK_HEADING_MODE` (default `embed`) — `embed` puts the chunk's heading path in the
  embedding input only and leaves the stored chunk raw (docs/14 §4.2's rule); `inline` also
  prefixes it to the stored chunk. **Leave this on `embed`.** It used to be a real trade —
  the FTS index was built over `chunks.content` alone, so `embed` hid the heading from the keyword
  half and cost it NDCG@10 (ADR-065), which is what `inline` bought back. Migration 0021 indexes
  `coalesce(heading,'') || ' ' || content` instead (K2-6, ADR-067), so the keyword half sees the
  heading either way and the two modes score identically — `inline` now only adds the heading to
  the text a visitor is shown in a citation. It is kept so the comparison stays runnable.

**URL ingest deliberately does not go through Docling.** The SSRF controls in
`loaders.load_url` are what stand between a user-supplied URL and the internal network, and
handing the URL to `docling-serve` to fetch would route around them — docs/14 §9's rule that a
new code path must not bypass an existing control.

### Reranking (stage 4) — ADR-063

A cross-encoder reorders the fused RRF candidates before the top-k slice. **Off by default
platform-wide and per agent** (`rag_config.rerank`); both must be on. It spends a network round
trip *before* generation starts, straight out of the NFR-1 p50 417 ms first-token budget, so
enabling it is a latency decision. Every failure — timeout, bad response, HTTP error — degrades
to RRF ordering, logged, never an error and never an empty result set.

- `RERANK_ENABLED` (default `false`) — the platform-wide switch.
- `RERANK_ENDPOINT` (default empty) — base URL of a `/rerank` service. Self-hosted
  text-embeddings-inference is the recommended shape: client knowledge-base text never leaves
  the deployment, and `bge-reranker-v2-m3` is trained multilingual, which matters where Tamil is
  a first language (docs/11 §9.2a). A hosted API of the same shape works too.
  **Enabled with this empty warns at startup** — a reranker that is off looks exactly like one
  that ran and agreed.
- `RERANK_MODEL` (default `BAAI/bge-reranker-v2-m3`) — ignored by TEI (it serves one model),
  required by a hosted API.
- `RERANK_CANDIDATE_K` (default `50`) — how many fused candidates the cross-encoder sees. This
  also widens the retrieval fetch: a reranker cannot rescue a chunk that was never fetched.
- `RERANK_TIMEOUT_MS` (default `800`) — availability budget.
- `RERANK_API_KEY` (default unset) — **the platform's credential, never an org's** (ADR-063,
  the rule ADR-055 set for guard models). A self-hosted container needs none.
- `CELERY_TASK_ALWAYS_EAGER` (default `false`) — when true, Celery tasks (document ingestion)
  run inline in-process instead of via the worker/broker. Handy for dev/tests; never in prod.
- `LLM_FORCE_FAKE` (default `false`) — when true, every chat and embedding call is forced onto
  the deterministic Fake provider regardless of the agent's configured provider. Used by the
  Playwright E2E suite so CI needs no paid keys and no local model pulls. **Never set in prod.**

## Chat runtime, memory & tools
- `MEMORY_WINDOW_MESSAGES` (default `12`) — how many recent turns stay verbatim in the prompt.
- `MEMORY_SUMMARY_THRESHOLD` (default `24`) — once a conversation exceeds this many messages,
  older turns are folded into `conversation.memory_summary`.
- `RUN_LIVE_LLM_TESTS` (test-only, unset by default) — `1` lets `tests/test_default_model.py` call Groq's `/models` and a real
  chat to prove the platform default, summary and guard models still exist. Needs a real `GROQ_API_KEY`.
- `SUMMARY_PROVIDER` / `SUMMARY_MODEL` (default `groq` / `openai/gpt-oss-20b`) — the small,
  fast model used for background memory summaries. Deliberately independent of the agent's own
  model so a heavy local model (e.g. qwen3:14b) is never used for summaries. Falls back to the
  fake provider when the provider has no key (CLAUDE §7).
- `TOOL_MAX_ITERATIONS` (default `4`) — max tool-call iterations per turn before the runtime
  forces a final answer.
- `TOOL_TIMEOUT_SECONDS` (default `15`) — per-tool execution timeout (HTTP / built-in tools).

## Agentic runtime & workflows (docs/17)
- `AGENTIC_LOOP_ENABLED` (default `false`) — platform-wide gate for the chat agentic loop and
  MCP tools. Off at BOTH this level and per-org (`Organization.agentic_loop_enabled`) by
  default — new attack surface and new cost exposure, so nothing turns on with a single flip.
- `AGENTIC_MAX_STEPS` / `AGENTIC_MAX_TOOL_CALLS` / `AGENTIC_MAX_RUNTIME_S` /
  `AGENTIC_MAX_COST_USD` (defaults `5` / `5` / `30` / `0.05`) — the four-dimensional
  `AgentBudget` ceiling shared by both the chat agentic loop and every workflow run (docs/17
  §2 rule 3: nested calls decrement the SAME budget, never a fresh one). A workflow's `loop`
  node is hard-capped by `AGENTIC_MAX_STEPS` too — every loop iteration is a node visit like
  any other.
- `MCP_TOOL_TIMEOUT_SECONDS` (default `20`) — wider than `TOOL_TIMEOUT_SECONDS`: an MCP call
  involves a subprocess spawn or a third-party network round trip.
- `WORKFLOW_MAX_CALL_DEPTH` (default `5`) — how deep a chain of workflow `sub_agent` nodes may
  nest before `AgentBudget.call_depth` trips it. Bounds a direct self-call and an indirect
  cycle (A→B→A) the same way, since depth doesn't distinguish which.
- `WORKFLOW_MAX_DELAY_SECONDS` (default `86400`, 24h) — longest a `delay` node may schedule a
  real wait for. A `delay` node pauses the run and schedules a Celery task at that `eta`
  (`app.worker.tasks.resume_delayed_workflow_task`) rather than blocking a worker; rejected
  loudly above this cap, never silently clamped.

## Guardrails (docs/11)
- `MAX_USER_MESSAGE_CHARS` (default `8000`) — hard ceiling on a single visitor message. Also
  the OWASP **LLM10** unbounded-consumption control: without it one caller can push an
  arbitrarily large prompt through a paid provider.
- `GUARD_INPUT_ENABLED` (default `true`) — the L1 static pre-filter on the visitor's own
  message (direct prompt injection, **LLM01**). With it off, Vicero still defends retrieved
  documents and tool output but not the person typing — the asymmetry docs/11 §1.1 documents.
  Switchable because a false positive costs a real customer a real answer; the benign fixture
  corpus in `tests/fixtures/redteam/benign.yaml` is what keeps that rate at zero.
- `GUARD_OUTPUT_ENABLED` (default `true`) — the L5 output guardrail: system-prompt leakage and
  persona breaks. Secret redaction runs regardless of this flag.
- `GUARD_OUTPUT_LEAK_THRESHOLD` (default `0.35`) — fraction of a reply's normalised 8-grams
  that may also appear in the assembled system prompt before the reply is treated as a leak
  and replaced. Lower is stricter. Raise it if legitimate answers that reuse the agent's own
  wording are being suppressed; the knowledge-base content is deliberately **not** part of the
  comparison, so quoting retrieved documents never counts.

- `GUARD_PII_EGRESS_ENABLED` (default `true`) — redact emails and phone numbers from a reply
  unless the org has allowlisted them (docs/11 Phase B, ADR-053). Secret redaction runs
  regardless of this flag. The allowlist is `Organization.public_contacts`, **not** an env var:
  it is per-tenant, and an empty one means the agent shares no contact details at all — safe,
  but not useful, so provisioning should seed it with the client's real support address.
- `GUARD_PII_PHONE_REGIONS` (default `IN,US,GB`) — ISO country codes used to read phone numbers
  written *without* a country code, most likely first. Numbers written with an explicit `+CC`
  are found regardless. Add a region when a client's customers write local-format numbers from
  somewhere else; each one is an extra scan pass, so keep the list short.
- `GUARD_PII_REDACT_ADDRESSES` (default `false`) — street addresses are counted in a document's
  `pii_flags` but not redacted from replies. Detection precision is materially worse than
  email/phone ("12 Month Plan" reads as a house number), so this stays off until the
  false-positive rate is measured on real traffic.

> The Playground bypasses the output guardrail entirely, the same way it withholds an agent's
> fallback message and re-raises provider errors: an operator testing an agent has to see what
> the model actually said. Guardrail behaviour is therefore visible on the widget, channels and
> dashboard chat, but not in the builder's test pane.

- `GUARD_INJECTION_ENABLED` (default `true`) — the L2 model-backed injection classifier
  (docs/11 §4-L2). This is what covers **paraphrase and non-English attacks**; L1 scores 0/6 on
  both by design. Per-org override lives on `Organization.guard_injection_enabled`, where `NULL`
  follows this default and only an explicit `false` opts a client out.
- `GUARD_INJECTION_MODEL` (default `meta-llama/llama-prompt-guard-2-86m`) — priced in
  `llm/catalog.GUARD_MODELS`. Deliberately not in `PROVIDERS`: it answers with a bare
  probability, so an agent pointed at it would reply `0.0004` to every question. Groq deprecated
  `llama-guard-4-12b` in Feb 2026 — check the deprecations page before changing this.
- `GUARD_INJECTION_THRESHOLD` (default `0.5`) — score at or above which a message is refused.
  Measured on this deployment: benign traffic scores **< 0.005** and attacks **> 0.998**, so the
  default sits in empty space and is not a delicate number.
- `GUARD_INJECTION_TIMEOUT_MS` (default `300`) — on timeout the turn proceeds **unguarded**
  (fail open, ADR-051) with a `guard_l2_unavailable` warning and a metric.
- `GUARD_INJECTION_CACHE_TTL_SECONDS` (default `3600`) — Redis cache keyed on
  `sha256(normalised text)`, so an attacker retrying one payload costs a single call.

> **The guard runs on the platform `GROQ_API_KEY`, never a client's** (ADR-055). An org may run
> its agent on any of 13 providers and hold no Groq key; resolving through the normal credential
> chain would silently switch safety off for exactly those clients, and because it fails open,
> nothing would say so. A missing platform key logs `guard_l2_disabled` at startup **and** shows
> in the admin console health card — `vicero_guard_calls_total{outcome="error"}` and
> `{outcome="unavailable"}` are the metrics to alert on, since a rising rate there means traffic
> is running unguarded rather than that nothing is being attempted.

- `GUARD_POLICY_ENABLED` / `GUARD_POLICY_MODEL` / `GUARD_POLICY_TIMEOUT_MS` — the L3 policy and
  distress classifier (docs/11 §4-L3). Grades every customer message against the markdown in
  `apps/api/app/chat/policies/`, which is **editable and hot-reloadable**: that wording *is* the
  taxonomy, so tuning it needs no deploy. Runs on the platform key (ADR-055).
- `GUARD_DISTRESS_ENABLED` (default `true`) — turns distress grading off entirely. Use it for a
  client who has **not agreed to watch the attention queue**: routing a person in real distress
  to a queue nobody monitors is worse than not detecting them (docs/11 §9).

> **On `elevated` the bot keeps answering.** Only `crisis` suppresses the reply, and it emits a
> fixed written holding message before handing to a human — never silence. `attention` is a
> separate axis from `status` (ADR-057), so a crisis a human has taken over is still visibly a
> crisis. Alert on `vicero_policy_calls_total{outcome="error"}`: L3 fails open, so an outage
> looks exactly like "nobody is in distress today".

- `WEB_SEARCH_ENABLED` (default **false**) — scoped web access (docs/11 §L7). Off platform-wide
  *and* per-agent (`features.web_search_enabled`). `WEB_SEARCH_API_KEY` is needs-human.
- `WEB_SEARCH_ENDPOINT` / `WEB_SEARCH_TIMEOUT_SECONDS` / `WEB_SEARCH_MONTHLY_QUOTA` — provider
  endpoint (anything returning `{results:[{url,title,content}]}`), request budget, and the
  per-org monthly ceiling (OWASP LLM10).

> **An empty domain allowlist means nothing is searchable**, not "search anything". The
> per-agent list (`features.web_search_allowed_domains`) ships empty and un-seeded on purpose:
> a support agent that can search the whole web will confidently quote a competitor's pricing,
> and deciding which domains a client's agent may cite is the client's call.

> **Finding what is already in a knowledge base:** `make audit-kb-pii` scans every KB across
> every org and reports counts per document (never the values). It exits non-zero when anything
> is found. `--apply` backfills `documents.pii_flags` for documents ingested before migration
> 0015; it never edits document text — removing PII from a client's corpus is an operator
> decision (ADR-054).
