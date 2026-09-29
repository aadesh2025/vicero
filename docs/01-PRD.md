# 01 — Product Requirements Document (PRD)

## 1. Vision

Vicero lets non-developers and developers alike build, deploy, and operate AI chatbots
("agents") that answer questions from a private knowledge base, take actions via tools and
automations, and run across web and messaging channels — all from a single web dashboard.
It is inspired by Botpress' capabilities but is an original product with its own
architecture, UI, API, and data model.

**North-star:** "From signup to a live, knowledge-grounded chatbot embedded on my site in
under 15 minutes — free-tier LLMs, no code required — with a clear path to add channels,
tools, automations, and team members."

## 2. Target users / personas

1. **Solo builder / indie hacker** — wants a cheap (free-LLM) chatbot on their site fast.
2. **SMB owner** — customer-support bot grounded in their docs, handed off to a human when
   stuck, connected to WhatsApp.
3. **Automation engineer** — wires the bot to n8n workflows to do real work (create tickets,
   send emails, update CRMs).
4. **Agency (AUROZEN-style)** — manages many client bots under separate organizations, with
   team members and role-based access. *(This is a general capability, unrelated to the
   user's separate AUROZEN AI chatbot project.)*
5. **Platform admin** — operates Vicero itself: sees all orgs, usage, health.

## 3. Core concepts / domain glossary

- **Organization (tenant)** — top-level isolation boundary. Everything belongs to an org.
- **Membership / Role** — a user's role within an org (owner, admin, editor, viewer, agent/human-operator).
- **Agent (Bot)** — a configured chatbot: persona, model, tools, knowledge, channels.
- **Persona** — system prompt + name + character/role + tone + guardrails.
- **Model config** — provider + model + temperature, top_p, max_tokens, penalties, etc.
- **Knowledge base** — a collection of documents attached to an agent (or shared).
- **Document / Chunk / Embedding** — uploaded file → split into chunks → vectorized.
- **Conversation / Message** — a chat session between an end-user and an agent.
- **Tool** — a callable function (built-in, HTTP, or n8n workflow) the agent can invoke.
- **Flow** — optional visual/logical workflow controlling agent behavior (stretch: full builder).
- **Channel** — where the agent is reachable (web widget, WhatsApp, Telegram, Slack, Discord, API).
- **Widget** — the embeddable web chat UI + JS SDK.
- **Inbox / Handoff** — human takeover of a live conversation.
- **API key** — programmatic access to Vicero's API, scoped to an org.
- **Automation** — an n8n workflow triggered by the agent or by events.

## 4. Functional requirements (numbered — used as acceptance anchors)

### FR-A: Auth & accounts
- FR-A1 Email/password signup + login (argon2 hashing).
- FR-A2 OAuth login: Google, GitHub.
- FR-A3 Magic-link (passwordless) login.
- FR-A4 JWT access + refresh tokens; refresh rotation; logout/revoke.
- FR-A5 Password reset, email verification.
- FR-A6 Sessions list + revoke.

### FR-B: Organizations, teams, RBAC
- FR-B1 Create/switch organizations; each user can belong to many.
- FR-B2 Invite members by email; accept/decline.
- FR-B3 Roles: `owner`, `admin`, `editor`, `viewer`, `operator`. Permission matrix in `02-ARCHITECTURE.md`.
- FR-B4 Every resource is scoped to an org; strict tenant isolation.
- FR-B5 Remove members, change roles, transfer ownership.

### FR-C: Agent (bot) builder
- FR-C1 Create/edit/delete/duplicate agents.
- FR-C2 Configure persona: display name, avatar, system prompt, character/role, tone.
- FR-C3 Configure model: provider, model name, temperature, top_p, max_tokens, frequency/
  presence penalty, stop sequences.
- FR-C4 Provider choices: Groq, Gemini(free), Ollama(local), OpenRouter, OpenAI, Anthropic,
  **custom OpenAI-compatible endpoint**.
- FR-C5 Per-agent or per-org **BYO API keys** (user supplies their own provider key).
- FR-C6 Welcome message, suggested prompts, fallback message, guardrails (blocked topics).
- FR-C7 Enable/disable RAG, tools, memory, human handoff per agent.
- FR-C8 Versioning: save drafts, publish, roll back.
- FR-C9 Test/playground panel to chat with the draft before publishing.

### FR-D: Knowledge base & RAG
- FR-D1 Upload PDF, DOCX, TXT, CSV, Markdown; also add URLs and raw text.
- FR-D2 Automatic parsing → chunking → embedding → store in pgvector.
- FR-D3 Show ingestion status per document (queued/processing/ready/failed) with progress.
- FR-D4 Re-index, delete, and view chunks of a document.
- FR-D5 Retrieval: top-k semantic search with score threshold, optional hybrid (keyword+vector).
- FR-D6 Citations: responses cite which chunks/documents were used.
- FR-D7 Configurable chunk size/overlap, top-k, and embedding model per knowledge base.

### FR-E: Chat runtime
- FR-E1 Streaming token responses (SSE/WebSocket).
- FR-E2 Conversation memory (short-term window + optional long-term summary memory).
- FR-E3 Full conversation history stored and browsable.
- FR-E4 Tool calling: agent can call built-in tools, HTTP tools, and n8n workflows.
- FR-E5 Multi-turn context management with token budgeting.
- FR-E6 Guardrails / content filtering hooks.

### FR-F: Tools & automations
- FR-F1 Built-in tools (e.g., web search stub, calculator, date/time, knowledge search).
- FR-F2 HTTP tool builder (define method, URL, headers, body schema, auth).
- FR-F3 n8n integration: list workflows, bind a workflow as a callable tool, invoke via
  webhook, pass structured args, receive result.
- FR-F4 Tool execution logs with inputs/outputs/latency/errors.
- FR-F5 Webhooks: Vicero emits events (message.created, handoff.requested, etc.) to n8n.

### FR-G: Channels & widget
- FR-G1 Embeddable web widget: single `<script>` snippet + JS SDK; themeable.
- FR-G2 Widget features: launcher bubble, typing indicator, quick replies, file upload,
  markdown rendering, branding, custom colors, position.
- FR-G3 WhatsApp (Meta Cloud API / Twilio), Telegram, Slack, Discord channel connectors.
- FR-G4 Public chat API for headless integrations.
- FR-G5 Per-channel config and enable/disable.

### FR-H: Human inbox & handoff
- FR-H1 Live inbox of active conversations across channels.
- FR-H2 Agent (bot) → human handoff trigger (keyword, intent, or explicit tool).
- FR-H3 Human operator can take over, reply, and hand back to the bot.
- FR-H4 Assignment, status (open/pending/closed), notes, tags.

### FR-I: Analytics
- FR-I1 Per-agent metrics: conversations, messages, users, resolution rate, handoff rate.
- FR-I2 Token/cost usage per provider, per agent, per org, over time.
- FR-I3 Latency, error rate, top questions, unanswered questions.
- FR-I4 Exportable (CSV) and date-range filtered dashboards.

### FR-J: Platform, keys, audit, billing
- FR-J1 API key management (create, scope, revoke, last-used).
- FR-J2 Audit log of sensitive actions (who/what/when).
- FR-J3 Usage metering + quota/limits per org (free tier, credits).
- FR-J4 Billing hooks (Stripe) — subscriptions, usage-based, invoices. *(Stretch/optional.)*
- FR-J5 Admin console: all orgs, users, usage, health, feature flags.

## 5. Non-functional requirements

- **NFR-1 Performance:** first token < 1.5s p50 on Groq; API p95 < 300ms for non-LLM
  endpoints; ingestion of a 20-page PDF < 30s.
- **NFR-2 Scalability:** stateless API, horizontal scale; background work via Celery;
  connection pooling; pagination on all list endpoints.
- **NFR-3 Security:** OWASP Top 10 addressed; tenant isolation; encrypted provider keys;
  rate limiting; CSRF/XSS/SQLi protections; secrets never logged. See `02 §Security`.
- **NFR-4 Reliability:** graceful provider fallback (if Groq fails → next configured
  provider); retries with backoff; idempotent webhooks; health/readiness endpoints.
- **NFR-5 Observability:** structured JSON logs, request IDs, metrics, error tracking hooks.
- **NFR-6 Accessibility:** WCAG 2.1 AA for dashboard and widget; keyboard nav; ARIA.
- **NFR-7 i18n-ready:** copy externalized; widget supports RTL and locale strings.
- **NFR-8 Privacy:** data export + delete per org; PII handling documented.
- **NFR-9 Portability:** entire stack runs via `docker compose up` on one machine.
- **NFR-10 Maintainability:** typed end-to-end, >70% test coverage on core services.

## 6. Acceptance criteria (product-level gates)

The product is "MVP-complete" when:
1. A new user can sign up, create an org, create an agent, pick **Groq**, and chat with it.
2. They can upload a PDF, see it become "ready", and the agent answers from it **with citations**.
3. They can embed the widget on a plain HTML page and chat through it.
4. They can connect **Telegram** and chat with the same agent there.
5. They can bind an **n8n workflow** as a tool and the agent successfully triggers it.
6. A teammate can be invited with a role and sees only that org's data.
7. Analytics shows conversation + token usage for the agent.
8. `docker compose up` brings up the whole stack (web, api, db, redis, n8n, ollama).
9. CI is green; Playwright E2E covers the flows in 1–5.

Per-phase acceptance gates live in `08-PHASES.md` and `10-TESTING.md`.

## 7. Out of scope for v1 (record as future roadmap)

Full visual drag-and-drop flow builder (ship a minimal version), voice/telephony, mobile
native apps, marketplace of community bots, on-prem SSO/SAML, fine-tuning UI. Billing is
optional/stretch. These go in `docs/PROGRESS.md` roadmap section.
