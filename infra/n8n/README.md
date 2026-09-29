# Starter n8n workflows

Importable workflows that Vicero agents can call as **n8n tools** (docs/07 §1).

| File | Webhook path | Mode | What it does |
|---|---|---|---|
| `vicero-echo.json` | `/webhook/vicero-echo` | sync | Echoes the tool arguments back — smoke test for a bound tool. |
| `create-support-ticket.json` | `/webhook/vicero-create-ticket` | sync | Generates a ticket id + status and returns it (a "Respond to Webhook" example). |
| `demo-niches/*.json` | `/webhook/demo-{harbor-reservations,citycare-appointments,luxeglow-bookings,threadline-orders}` | sync | Four **fake-data** demo automations (restaurant, clinic, salon, fabric store) used to test agents end to end. See below. |
| `template-starter-automation.json` | `/webhook/{client-slug}-starter-automation` | sync | **Cloned per client** by `scripts/provision-client.mjs`. Import it once; the script copies it per client, rewriting the webhook path to the client's slug so no two clients share a URL. |

### The provisioning template

`TEMPLATE — Starter Automation` must exist in n8n before `scripts/provision-client.mjs` can
provision anyone — it clones that workflow by name. Import it once (below), leave it
**inactive** (the clones are what get activated), and don't rename it.

Its Set node writes `handled_by: {{ $workflow.name }}`, so each client's copy reports its own
name. Real per-client logic (CRM, Sheets, Slack …) replaces the Set node afterwards; the
template exists so a new client starts with a working webhook rather than an empty n8n.

**Which n8n?** Vicero must own the instance it provisions into — cloning and activating
workflows in an instance that belongs to another project risks that project's automations. If
5678 is taken, run Vicero's own on another port:
`cd infra && N8N_HOST_PORT=5679 docker compose up -d n8n`, and set
`N8N_BASE_URL=http://localhost:5679`.

## How Vicero calls them

When an agent invokes a bound n8n tool, Vicero `POST`s to the workflow's webhook URL with:

```json
{
  "args": { ...the tool arguments the model produced... },
  "mode": "sync" | "async",
  "run_id": "<tool_run uuid>",
  "conversation_id": "<uuid|null>",
  "callback_url": "http://localhost:8000/v1/tools/n8n/callback"   // async only
}
```

Every request is signed: headers `X-Vicero-Signature` (HMAC-SHA256 of `"{timestamp}.{body}"`
using `N8N_WEBHOOK_SIGNING_SECRET`) and `X-Vicero-Timestamp`. **Every JSON in this directory verifies
them** (see below) — a workflow that does not is callable by anyone who has its URL.

**Vicero enforces this when a workflow is bound as a tool.** `POST /v1/tools/n8n/bind` fetches the
workflow from the n8n API and refuses (400 `tools.n8n_unsigned_workflow`) unless every Webhook node has
**Raw Body** on and feeds only a Code node that does the HMAC check (`createHmac`, `x-vicero-signature`,
`timingSafeEqual`) whose result an IF/Switch then tests (`verified`). To pass, start from
`template-starter-automation.json` (or copy its first three nodes). A pasted webhook URL is resolved to its
workflow through the n8n API (matched on the `/webhook/<path>`), so binding by URL needs `N8N_API_KEY`; a
URL that matches no workflow is refused as unverifiable. It is a structural lint, not proof — the real
verifier's behaviour is pinned by `tests/test_n8n_workflows_signed.py`. `N8N_REQUIRE_SIGNATURE_CHECK=false`
turns it off for a deliberately unsigned dev n8n.

**Binds that predate the check** are not re-examined automatically. Staff can list them with
`GET /v1/admin/n8n-signature-audit` (every bound n8n tool across all orgs → `verified` / `unverified` /
`unresolved`, with the reason and org/agent/tool, unverified first). Run it once after deploying the check,
and again after editing any bound workflow in the n8n UI.

- **Sync** workflows must end in a **Respond to Webhook** node returning JSON — that JSON is fed
  straight back to the model.
- **Async** workflows do their work, then `POST` back to `callback_url` with a signed body
  `{ "run_id", "output", "status" }` to resolve the pending tool run.

## Import

**Via the n8n UI:** open http://localhost:5678 (basic auth `admin` / `vicero`) →
*Workflows* → *Import from File* → pick a JSON here → **Activate** the workflow (top-right
toggle) so the production webhook registers.

**Via the public API:**

```bash
KEY=$N8N_API_KEY
curl -s -H "X-N8N-API-KEY: $KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:5678/api/v1/workflows \
  --data-binary @infra/n8n/vicero-echo.json
# then activate it:  POST /api/v1/workflows/{id}/activate
```

## Bind in Vicero

Dashboard → **Automations** → pick the workflow → **Bind as tool** (choose the agent + mode).
Then enable the agent's tools and chat — the agent can call the workflow and use its response.

## Signature verification (built into every JSON here)

Each workflow starts **Webhook** (`rawBody: true`) → **Verify Vicero signature** (Code) → **Signature
valid?** (IF) → the automation, with the false branch answering **401**. The check recomputes
`HMAC-SHA256(secret, "<timestamp>." + raw body)`, compares in constant time, rejects timestamps more than
300 s off, and **fails closed** (no secret / missing header / no raw body ⇒ 401). It is part of the
JSON, so importing a workflow gives you verification — there is nothing to re-add by hand.

What it needs from the **n8n container** (the dev compose already sets these; a production n8n must too,
since `docker-compose.prod.yml` has no n8n service):

| n8n env | Why |
|---|---|
| `N8N_WEBHOOK_SIGNING_SECRET` | the shared secret — the root `.env` value Vicero signs with (one source, see below) |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` | n8n 2.x hides `$env` from Code nodes by default |
| `NODE_FUNCTION_ALLOW_BUILTIN=crypto` | ...and blocks `require('crypto')` |

**Why one file.** The secret lives **only in the root `.env`** — that is where Vicero's own settings loader
reads it, so it is what the API signs with. Compose's `${...}` interpolation ignores the root `.env` by
default and reads `infra/.env`, which used to hold a second copy. Two copies drift silently: rotate one and
n8n rejects every call as "invalid signature" while nothing looks wrong on the Vicero side. So the compose
file has no copy; `make up` passes `--env-file .env` and n8n gets the same value the API signs with. From
`infra/` by hand: `docker compose --env-file .env --env-file ../.env up -d n8n`. Started without it, n8n has
an empty secret and rejects every call (fail closed — safe, but every tool call 401s). After changing it:
`make up` (recreates n8n). If the secret
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
3. Make sure the root `.env` has `N8N_WEBHOOK_SIGNING_SECRET` and start n8n with `make up` (see above).
4. `node scripts/import-n8n-workflows.mjs` — creates or updates every `demo-niches/*.json`, activates it and
   tags it `demo` (`--org acme --dir infra/n8n` for other sets). Idempotent.
5. Restart the API so it picks up the new `N8N_API_KEY`. Tools bound before the wipe keep working (they store
   the webhook URL, which is unchanged); the Automations page needs the key.
