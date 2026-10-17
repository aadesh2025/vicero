# 04 — REST API Specification

Base URL: `/v1`. JSON everywhere. Auth via `Authorization: Bearer <access_token>` (dashboard)
or `Authorization: Bearer <api_key>` (programmatic, prefix `bf_`). All list endpoints:
cursor or page/limit pagination, `?limit=&cursor=` returning `{data:[], next_cursor}`.
FastAPI auto-generates OpenAPI at `/openapi.json`; the frontend generates its typed client
from it. Error model per `02 §8`.

## Conventions
- Timestamps ISO-8601 UTC. IDs are UUID strings.
- Org context: resolved from token; org-scoped routes are `/v1/orgs/{org_id}/...` OR the
  current org is taken from an `X-Org-Id` header validated against membership. Pick one
  (recommend `X-Org-Id` header + membership check) and be consistent.
- Standard responses include `id, created_at, updated_at`.
- Write endpoints validate RBAC per `02 §6`.

## Auth  `/v1/auth`
- `POST /signup` `{email, password, full_name}` → user + tokens.
- `POST /login` `{email, password}` → `{access_token, refresh_token, user}`.
- `POST /refresh` `{refresh_token}` → new pair (rotates).
- `POST /logout` → revoke current refresh/session.
- `GET  /me` → current user + memberships.
- `POST /verify-email` `{token}` / `POST /verify-email/resend`.
- `POST /password/forgot` `{email}` / `POST /password/reset` `{token, password}`.
- `POST /magic-link` `{email}` / `POST /magic-link/verify` `{token}`.
- OAuth: `GET /oauth/{provider}/authorize` → redirect URL; `GET /oauth/{provider}/callback`.
- `GET /sessions` / `DELETE /sessions/{id}`.

## Organizations  `/v1/orgs`
- `POST /` create org `{name}`.
- `GET /` list my orgs.  `GET /{id}` / `PATCH /{id}` / `DELETE /{id}`.
- Members: `GET /{id}/members`, `PATCH /{id}/members/{userId}` (role), `DELETE /{id}/members/{userId}`.
- Invites: `POST /{id}/invitations` `{email, role}`, `GET /{id}/invitations`,
  `POST /invitations/{token}/accept`, `DELETE /{id}/invitations/{invId}`.
- `POST /{id}/transfer-ownership` `{userId}`.

## Agents  `/v1/agents`
- `POST /` create `{name, description}` → agent (draft v1).
- `GET /` list.  `GET /{id}` / `PATCH /{id}` / `DELETE /{id}` / `POST /{id}/duplicate`.
- Versions: `GET /{id}/versions`, `POST /{id}/versions` (new draft),
  `PATCH /{id}/versions/{v}` (persona, model_config, rag_config, features, prompts),
  `POST /{id}/versions/{v}/publish`, `POST /{id}/rollback` `{version}`.
- Playground: `POST /{id}/playground/chat` (streams; uses draft version).
- Provider creds: `GET/POST/DELETE /{id}/credentials` (BYO keys, per agent).

## Provider credentials (org-level)  `/v1/credentials`
- `GET /` list (keys masked).  `POST /` `{provider, label, api_key, base_url?, is_default?}`.
- `PATCH /{id}` / `DELETE /{id}`.  `POST /{id}/test` → validates key against provider.
- `GET /providers` → list supported providers + their available models (Groq/Gemini/Ollama
  discovered dynamically where possible).

## Knowledge base  `/v1/knowledge`
- `POST /` create KB `{name, embedding_provider, embedding_model, chunk_size, chunk_overlap}`.
- `GET /` / `GET /{id}` / `PATCH /{id}` / `DELETE /{id}`.
- Documents: `POST /{id}/documents` (multipart file OR `{source_type:url|text, ...}`),
  `GET /{id}/documents`, `GET /documents/{docId}` (status/progress),
  `POST /documents/{docId}/reindex`, `DELETE /documents/{docId}`,
  `GET /documents/{docId}/chunks`.
- Retrieval test: `POST /{id}/search` `{query, top_k}` → chunks + scores.

## Chat  `/v1/agents/{id}/chat`  (dashboard + API)
- `POST /agents/{id}/chat` `{conversation_id?, message, stream?}` →
  SSE stream of `{type: conversation|citations|token|tool_call|tool_result|message|error|done, ...}` or JSON if not streamed.
- WebSocket `WS /v1/agents/{id}/chat/ws` — bidirectional streaming.
- Conversations: `GET /v1/conversations?agent_id=`, `GET /v1/conversations/{cid}`,
  `GET /v1/conversations/{cid}/messages`, `PATCH /v1/conversations/{cid}` (title/status),
  `DELETE /v1/conversations/{cid}`.

## Public chat (widget & channels)  `/v1/public`
- `GET /public/agents/{public_key}/config` → widget theme, welcome, suggested prompts.
- `POST /public/agents/{public_key}/chat` (rate-limited, no dashboard auth; optional
  visitor token) → same streaming contract.
- `WS /public/agents/{public_key}/ws`.
- **Widget config additions (docs/27, all additive, `null` = unchanged).** `PATCH /agents/{id}/widget-config`
  accepts `widgetLayout` (`classic|glass|hairline|paper|pebble|console|concierge|command|drawer|block`),
  `cornerRadius` (int 0-28), `density` (`compact|comfortable|roomy`) and `interactiveBlocks`
  (`{enabled, options, cards, form, datetime, counter, rating, steps, handoff: bool}`); anything else is
  `widget.invalid_config` (400). The public config returns `widget_layout`, `corner_radius`, `density`
  (all `null` for an untouched agent) and `interactive_blocks` (`null` unless the master switch is on).
- **Interactive blocks.** The chat stream gains `{type: "block", block}` (after the text, before `done`; only for a
  turn the output guard passed; `replace`d, failed or crisis turns carry none). `POST .../chat` accepts
  `block_reply: {block_id, value}` beside the normal `message`; the tap is an ordinary turn. `stream:false`
  adds `blocks` to the JSON only when there are some. `GET /public/agents/{public_key}/conversations/{id}/messages?visitor_id=`
  returns `[{role, text, blocks}]` (last 40; blocks-enabled agents only; the visitor who started the
  conversation only, else 404). A block: `{id, type, ...fields, fallback_text, resolved?}`; the server assigns
  `id` and `fallback_text`. Caps: options <= 8 items / label <= 60; cards <= 6 (https images); form <= 5 fields
  of `text|email|tel`; datetime <= 14 days / <= 12 slots; counter max <= 20; steps <= 8; <= 2 blocks per reply.
  Persisted in `messages.blocks` (migration 0037); `content` carries the fallback text.

## Tools  `/v1/tools`
- `GET /builtin` → available built-in tools.
- `GET /` / `POST /` `{name, type, description, config, input_schema, agent_id?}`.
- `GET /{id}` / `PATCH /{id}` / `DELETE /{id}` / `POST /{id}/test` `{input}`.
- Runs: `GET /runs?conversation_id=` → tool_runs log.
- n8n: `GET /n8n/workflows` (proxy list from n8n), `POST /n8n/bind` `{workflow_id, name,
  mode}` → creates an n8n tool.

## Channels  `/v1/channels`
- `GET /` / `POST /` `{agent_id, type, name, config}` / `GET /{id}` / `PATCH /{id}` / `DELETE /{id}`.
- `POST /{id}/enable` / `POST /{id}/disable`.
- Widget snippet: `GET /{id}/embed` → `<script>` snippet + instructions.
- Inbound webhooks (public, signature-verified):
  `POST /channels/telegram/{channelId}/webhook`,
  `POST /channels/whatsapp/{channelId}/webhook` (+ `GET` for Meta verify challenge),
  `POST /channels/facebook/{channelId}/webhook` (+ `GET` verify challenge),
  `POST /channels/instagram/{channelId}/webhook` (+ `GET` verify challenge),
  `POST /channels/slack/{channelId}/events`,
  `POST /channels/discord/{channelId}/interactions`.

## Inbox / handoff  `/v1/inbox`
- `GET /conversations?status=&channel=` (status: active/handoff/closed; channel: widget/
  telegram/whatsapp/instagram/facebook/slack/discord), `GET /conversations/{cid}`.
- Both carry a nested `contact: {id, display_name, avatar_url} | null` — the resolved identity
  behind `channel_user_id`. Null only for conversations that predate contact resolution.
- `POST /conversations/{cid}/takeover`, `POST /conversations/{cid}/handback`,
  `POST /conversations/{cid}/messages` (operator reply),
  `POST /conversations/{cid}/assign` `{userId}`, `POST /conversations/{cid}/close`,
  `POST /conversations/{cid}/notes`, `POST /conversations/{cid}/tags`.

## Analytics  `/v1/analytics`
- `GET /overview?agent_id=&from=&to=&channel=` → conversations, messages, users,
  resolution/handoff rate, plus `by_channel[]` — one bucket per channel with its own
  conversations/messages/tokens/cost/rates.
- `GET /usage?agent_id=&from=&to=&channel=&group_by=day|provider|model|channel` → tokens + cost.
- `GET /latency`, `GET /top-questions`, `GET /unanswered` — all take `channel=` too.
- `GET /agents?from=&to=` → per-teammate inbox workload (handoffs handled, avg first-response,
  avg resolution, conversations closed). Durations are null when there's nothing to average.
- `GET /export?type=usage|conversations|channels` → CSV.
- **Channel coverage:** `by_channel` and `group_by=channel` include every channel the org
  has an *enabled* `Channel` row for (plus `widget`, which every agent has inherently),
  even with zero conversations — a freshly-connected channel is a reportable zero, not a
  missing row. Rates are `0.0` on zero conversations (no divide-by-zero). `channel=` is
  free text, not an enum: `conversations.channel` also holds `dashboard`/`api`/`web`, and
  an unknown value returns an empty result rather than a 422.
- `GET /export?type=usage|conversations&from=&to=` → CSV.

## API keys  `/v1/apikeys`
- `GET /` / `POST /` `{name, scopes, expires_at?}` → returns full key **once**.
- `POST /{id}/revoke` / `DELETE /{id}`.

## Webhooks (outbound)  `/v1/webhooks`
- `GET /` / `POST /` `{url, events}` / `PATCH /{id}` / `DELETE /{id}` / `POST /{id}/test`.
- `GET /{id}/deliveries` → delivery log.
- Event catalog: `message.created`, `conversation.created`, `handoff.requested`,
  `handoff.resolved`, `document.ready`, `document.failed`, `tool.run`, `usage.threshold`.

## Admin (platform staff, `is_staff`)  `/v1/admin`
- `GET /orgs`, `GET /users`, `GET /usage`, `GET /health`, feature flags CRUD.

## Billing (optional)  `/v1/billing`
- `GET /subscription`, `POST /checkout`, `POST /portal`, `POST /webhook` (Stripe).

## System
- `GET /healthz`, `GET /readyz`, `GET /metrics`, `GET /version`.

## Streaming event contract (SSE/WS)
Each event: `{ "type": "...", ... }`
- `token` `{delta}` — a text chunk.
- `tool_call` `{tool, input, run_id}` — model requested a tool.
- `tool_result` `{run_id, output, status}`.
- `citations` `{citations: [{document_id, chunk_id, knowledge_base_id, ordinal, content, score,
  metadata}]}` — emitted once, before the model's first token (not one event per source).
- `message` `{message_id}` — assistant message persisted.
- `error` `{code, message}`.
- `done` `{usage: {tokens_prompt, tokens_completion, cost_micros}, provider, model}`.

## Rate limiting
Public chat + auth endpoints: Redis token bucket per IP + per public_key/api_key. Return
`429` with `Retry-After`. Limits configurable via env.
