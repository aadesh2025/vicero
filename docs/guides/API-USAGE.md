# API usage guide

The Vicero REST API is served under `/v1`. Interactive docs (OpenAPI) are at
`http://localhost:8000/docs` and `/redoc`. Every response error uses a typed envelope:

```json
{ "error": { "code": "some.code", "message": "Human readable", "details": null } }
```

## Authentication

Two ways to authenticate, both on org-scoped routes:

### 1. Dashboard session (JWT)

```bash
# Sign up (or /v1/auth/login) → returns access + refresh tokens
curl -s -X POST http://localhost:8000/v1/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"me@acme.com","password":"a-strong-password","full_name":"Me"}'
# → { "access_token": "...", "refresh_token": "...", "user": {...} }
```

Send the access token as `Authorization: Bearer <access_token>`. Access tokens are short-lived;
rotate with `POST /v1/auth/refresh` (`{"refresh_token": "..."}`). Org-scoped routes also need an
`X-Org-Id: <org_uuid>` header to pick the tenant (from `GET /v1/orgs`).

### 2. API key (server-to-server)

Create one in **Settings → API keys** (or `POST /v1/apikeys`). Keys are `bf_`-prefixed, shown
once, and carry a **scope** (`read` / `write` / `admin`) capped by the creating member's role.

```bash
# Either header works; the key resolves its org, so no X-Org-Id is needed.
curl -s http://localhost:8000/v1/agents -H 'X-API-Key: bf_live_xxx'
curl -s http://localhost:8000/v1/agents -H 'Authorization: Bearer bf_live_xxx'
```

## Common flows

### Create, configure, publish an agent

```bash
ORG=<org_id>; TOK=<access_token>
H=(-H "Authorization: Bearer $TOK" -H "X-Org-Id: $ORG" -H 'Content-Type: application/json')

# create (starts as a draft, provider defaults to groq)
AID=$(curl -s "${H[@]}" -X POST http://localhost:8000/v1/agents -d '{"name":"Support Bot"}' | jq -r .id)
VER=$(curl -s "${H[@]}" http://localhost:8000/v1/agents/$AID | jq -r .draft_version)

# configure the draft (model config uses the JSON key "model_config")
curl -s "${H[@]}" -X PATCH http://localhost:8000/v1/agents/$AID/versions/$VER \
  -d '{"model_config":{"provider":"groq","model":"openai/gpt-oss-20b"},"system_prompt":"You are helpful."}'

# publish
curl -s "${H[@]}" -X POST http://localhost:8000/v1/agents/$AID/versions/$VER/publish
```

### Chat with an agent (SSE stream or one-shot)

```bash
# One-shot JSON
curl -s "${H[@]}" -X POST http://localhost:8000/v1/agents/$AID/chat \
  -d '{"message":"What are your hours?","stream":false}'
# → { conversation_id, message_id, content, citations, tool_runs, usage, ... }

# Streaming (Server-Sent Events): conversation → token* → message
curl -sN "${H[@]}" -X POST http://localhost:8000/v1/agents/$AID/chat \
  -d '{"message":"Hi","stream":true}'
```

Continue a conversation by passing its `conversation_id` back in the next request.

### Knowledge base + RAG

```bash
KB=$(curl -s "${H[@]}" -X POST http://localhost:8000/v1/knowledge -d '{"name":"Docs"}' | jq -r .id)
# upload a file (multipart; field name is "file")
curl -s -H "Authorization: Bearer $TOK" -H "X-Org-Id: $ORG" \
  -F 'file=@handbook.pdf' http://localhost:8000/v1/knowledge/$KB/documents/upload
# poll GET /v1/knowledge/documents/<id> until status == "ready", then enable RAG on the agent:
curl -s "${H[@]}" -X PATCH http://localhost:8000/v1/agents/$AID/versions/$VER \
  -d "{\"rag_config\":{\"enabled\":true,\"knowledge_base_ids\":[\"$KB\"],\"top_k\":5}}"
```

## Outbound webhooks

Register endpoints (`POST /v1/webhooks`) to receive HMAC-signed events: `message.created`,
`conversation.created`/`closed`, `handoff.requested`/`resolved`, `document.ready`/`failed`,
`tool.run`, `usage.threshold`. Verify the `X-Vicero-Signature` header against your endpoint
secret. Failed deliveries are retried with backoff and a periodic beat sweep.

## Route map

`/v1/auth/*`, `/v1/orgs/*`, `/v1/credentials/*`, `/v1/agents/*` (+ `/playground`, `/chat`,
`/chat/ws`), `/v1/knowledge/*`, `/v1/conversations/*`, `/v1/tools/*` (+ `/n8n/*`),
`/v1/public/agents/{public_key}/*` (widget), `/v1/channels/*`, `/v1/inbox/*`, `/v1/analytics/*`,
`/v1/apikeys/*`, `/v1/webhooks/*`, `/v1/audit`, `/v1/admin/*` (platform staff). Full schemas
live in [`04-API-SPEC.md`](../04-API-SPEC.md) and the live `/docs`.

## Rate limits & errors

Public endpoints (auth, channel webhooks, widget chat, n8n callback) are rate-limited per source.
Exceeding a limit returns `429`. Validation failures return `422` with the offending field in
`error.details`. Tenant isolation is enforced at the query layer — an object from another org
returns `404`, never another tenant's data.
