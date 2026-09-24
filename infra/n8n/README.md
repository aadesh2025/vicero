# Starter n8n workflows

Importable workflows that BotForge agents can call as **n8n tools** (docs/07 §1).

| File | Webhook path | Mode | What it does |
|---|---|---|---|
| `botforge-echo.json` | `/webhook/botforge-echo` | sync | Echoes the tool arguments back — smoke test for a bound tool. |
| `create-support-ticket.json` | `/webhook/botforge-create-ticket` | sync | Generates a ticket id + status and returns it (a "Respond to Webhook" example). |
| `demo-niches/*.json` | `/webhook/demo-{harbor-reservations,citycare-appointments,luxeglow-bookings,threadline-orders}` | sync | Four **fake-data** demo automations (restaurant, clinic, salon, fabric store) used to test agents end to end. See below. |
| `template-starter-automation.json` | `/webhook/{client-slug}-starter-automation` | sync | **Cloned per client** by `scripts/provision-client.mjs`. Import it once; the script copies it per client, rewriting the webhook path to the client's slug so no two clients share a URL. |

### The provisioning template

`TEMPLATE — Starter Automation` must exist in n8n before `scripts/provision-client.mjs` can
provision anyone — it clones that workflow by name. Import it once (below), leave it
**inactive** (the clones are what get activated), and don't rename it.

Its Set node writes `handled_by: {{ $workflow.name }}`, so each client's copy reports its own
name. Real per-client logic (CRM, Sheets, Slack …) replaces the Set node afterwards; the
template exists so a new client starts with a working webhook rather than an empty n8n.

**Which n8n?** BotForge must own the instance it provisions into — cloning and activating
workflows in an instance that belongs to another project risks that project's automations. If
5678 is taken, run BotForge's own on another port:
`cd infra && N8N_HOST_PORT=5679 docker compose up -d n8n`, and set
`N8N_BASE_URL=http://localhost:5679`.

## How BotForge calls them

When an agent invokes a bound n8n tool, BotForge `POST`s to the workflow's webhook URL with:

```json
{
  "args": { ...the tool arguments the model produced... },
  "mode": "sync" | "async",
  "run_id": "<tool_run uuid>",
  "conversation_id": "<uuid|null>",
  "callback_url": "http://localhost:8000/v1/tools/n8n/callback"   // async only
}
```

Every request is signed: headers `X-BotForge-Signature` (HMAC-SHA256 of `"{timestamp}.{body}"`
using `N8N_WEBHOOK_SIGNING_SECRET`) and `X-BotForge-Timestamp`. **Every JSON in this directory verifies
them** (see below) — a workflow that does not is callable by anyone who has its URL.

- **Sync** workflows must end in a **Respond to Webhook** node returning JSON — that JSON is fed
  straight back to the model.
- **Async** workflows do their work, then `POST` back to `callback_url` with a signed body
  `{ "run_id", "output", "status" }` to resolve the pending tool run.

## Import

**Via the n8n UI:** open http://localhost:5678 (basic auth `admin` / `botforge`) →
*Workflows* → *Import from File* → pick a JSON here → **Activate** the workflow (top-right
toggle) so the production webhook registers.

**Via the public API:**

```bash
KEY=$N8N_API_KEY
curl -s -H "X-N8N-API-KEY: $KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:5678/api/v1/workflows \
  --data-binary @infra/n8n/botforge-echo.json
# then activate it:  POST /api/v1/workflows/{id}/activate
```

## Bind in BotForge

Dashboard → **Automations** → pick the workflow → **Bind as tool** (choose the agent + mode).
Then enable the agent's tools and chat — the agent can call the workflow and use its response.

## Signature verification (built into every JSON here)

Each workflow starts **Webhook** (`rawBody: true`) → **Verify BotForge signature** (Code) → **Signature
valid?** (IF) → the automation, with the false branch answering **401**. The check recomputes
`HMAC-SHA256(secret, "<timestamp>." + raw body)`, compares in constant time, rejects timestamps more than
300 s off, and **fails closed** (no secret / missing header / no raw body ⇒ 401). It is part of the
JSON, so importing a workflow gives you verification — there is nothing to re-add by hand.

What it needs from the **n8n container** (the dev compose already sets these; a production n8n must too,
since `docker-compose.prod.yml` has no n8n service):

| n8n env | Why |
|---|---|
| `N8N_WEBHOOK_SIGNING_SECRET` | the shared secret — **must equal** the root `.env` value BotForge signs with |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` | n8n 2.x hides `$env` from Code nodes by default |
| `NODE_FUNCTION_ALLOW_BUILTIN=crypto` | ...and blocks `require('crypto')` |

Compose interpolates from the shell or `infra/.env` — **not** the root `.env` — so the secret has to be in
`infra/.env` as well (gitignored). After changing it: `cd infra && docker compose up -d n8n`. If the secret
is missing or different, every call is rejected with 401 rather than accepted — that is the failure mode
to look for when a tool "stopped working".

## Demo niche workflows (`demo-niches/`) — fixture data

Four workflows, each `Webhook → Verify → Code (router on args.action) → Respond`, for exercising agents
without a real backend: Harbor Table Bistro (restaurant), CityCare Clinic, Luxe Glow Salon & Spa, Threadline
Fabrics. **Every name, doctor, price, order id and slot is fictional demo data**, embedded in the Code node
and marked `// DEMO FIXTURE` at its top. Their *runtime* state (bookings, counters) lives in n8n workflow
static data and is deliberately **not** exported — a re-import starts clean.

## Re-importing after n8n loses its database

n8n's data lives in the `n8ndata` volume; if it is wiped (it has been) you have no owner, no API key and no
workflows. Rebuild in this order:

1. `cd infra && docker compose up -d n8n`, open http://localhost:5679 and create the owner account.
2. n8n → Settings → **n8n API** → create a key with workflow create/read/list/update/activate, tag
   create/list/read/update and workflowTags scopes; put it in the root `.env` as `N8N_API_KEY`.
3. Make sure `infra/.env` has `N8N_WEBHOOK_SIGNING_SECRET` (same as the root `.env`) and recreate the
   container if you had to add it.
4. `node scripts/import-n8n-workflows.mjs` — creates or updates every `demo-niches/*.json`, activates it and
   tags it `demo` (`--org acme --dir infra/n8n` for other sets). Idempotent.
5. Restart the API so it picks up the new `N8N_API_KEY`. Tools bound before the wipe keep working (they store
   the webhook URL, which is unchanged); the Automations page needs the key.
