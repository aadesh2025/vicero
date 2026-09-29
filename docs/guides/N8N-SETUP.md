# n8n setup guide

Vicero integrates with a locally-running (or remote) [n8n](https://n8n.io) instance two ways:

1. **Vicero → n8n** — an agent calls an n8n workflow as a **tool** (via the workflow's webhook).
2. **n8n → Vicero** — a workflow calls back into Vicero (signed callback for async tools, or
   the Vicero REST API with an API key) to do real work: create tickets, send email, update a CRM.

This guide covers running n8n and wiring it up. For the ready-made starter workflows and the exact
signed request/response contract, see [`infra/n8n/README.md`](../../infra/n8n/README.md).

## 1. Run n8n

The dev compose file already includes an `n8n` service:

```bash
cd infra && docker compose up -d n8n
# → http://localhost:5678   (basic auth: admin / vicero by default)
```

To point Vicero at an **already-running** n8n instead, just set `N8N_BASE_URL` (below) — the
compose `n8n` service is optional.

## 2. Configure Vicero

In `.env` (see [`ENV.md`](../ENV.md)):

| Variable | Purpose |
|---|---|
| `N8N_BASE_URL` | Where n8n lives (default `http://localhost:5678`; `http://n8n:5678` inside compose). |
| `N8N_API_KEY` | n8n public-API key — lets Vicero **list** your workflows in the Automations UI. Without it, bind a workflow by pasting its webhook URL directly. |
| `N8N_WEBHOOK_SIGNING_SECRET` | HMAC secret Vicero uses to **sign** outbound tool calls (and verify async callbacks). Set it and verify the signature in your workflows for production. |

Create an n8n API key in n8n: **Settings → n8n API → Create an API key**.

## 3. Import a workflow

Import a starter (or your own) and **activate** it so its production webhook registers:

- **UI:** http://localhost:5678 → *Workflows → Import from File* → pick e.g.
  `infra/n8n/vicero-echo.json` → toggle **Active** (top-right).
- **API:** `POST http://localhost:5678/api/v1/workflows` with header `X-N8N-API-KEY`, then
  `POST /api/v1/workflows/{id}/activate`.

A Vicero-callable workflow starts with a **Webhook** node and (for sync tools) ends with a
**Respond to Webhook** node returning JSON.

## 4. Bind it as an agent tool

Dashboard → **Automations** → pick the workflow → **Bind as tool** → choose the agent and mode:

- **sync** — Vicero POSTs to the webhook and feeds the *Respond to Webhook* JSON straight back to
  the model in the same turn.
- **async** — the workflow does long-running work, then POSTs a signed
  `{run_id, output, status}` back to `POST /v1/tools/n8n/callback` to resolve the pending tool run.

No API key? Bind directly by webhook URL (Automations → Bind → paste the URL).

Then enable the agent's **Tools** and chat — when the model decides to call the tool, Vicero
triggers the workflow and uses its response. (This full roundtrip was verified live in Phase 10.)

## 5. Multi-tenant visibility — tag each client's workflows

Vicero is one shared n8n instance behind every org, so **`Automations` scopes what each org
can see by n8n tag**, not by anything n8n itself knows about tenants. Visibility is
**deny-by-default**: a workflow reaches an org only if it is tagged for it.

| Tag on the workflow | Who sees it in Automations |
|---|---|
| the client's **org slug** (e.g. `acme-co`) | only that org |
| `shared-template` | every org — for genuinely reusable starters |
| `internal` / `shared-internal` / `platform-internal` | **nobody**, ever |
| *nothing* | **nobody** — untagged is hidden, not shared |

- **Untagged means invisible.** Tag every client-specific workflow as soon as you create it, or
  the client can't see or bind their own automation. This reverses the original permissive
  default: untagged used to mean "visible to everyone", which meant a brand-new empty org saw
  every other client's automations. Forgetting to tag is now a visible annoyance instead of a
  silent cross-tenant leak.
- `internal` beats everything, including `shared-template`, so mislabelling a platform workflow
  both ways still hides it.
- A workflow whose name starts with `SHARED —` is treated as internal even if untagged — a
  safety net for the platform workflows that predate tagging.
- Binding by `workflow_id` re-checks the same rule server-side (an org can't bind a workflow it
  was never shown just by knowing its id). Binding by pasting a raw webhook URL directly is
  **not** covered — treat webhook URLs for client-specific workflows as secrets.

Set tags in the n8n UI: open the workflow → the tag field near the title. See ADR-042 in
`docs/DECISIONS.md` for the full reasoning.

### Tagging the existing inventory

`scripts/tag-n8n-workflows.mjs` applies a known name → tag mapping in bulk. It is dry-run by
default and safe to re-run — a workflow that already carries its tag is skipped, and existing
tags are preserved rather than replaced:

```bash
node scripts/tag-n8n-workflows.mjs                                  # show what it would do
node scripts/tag-n8n-workflows.mjs --apply                          # write the tags
node scripts/tag-n8n-workflows.mjs --apply --base-url http://localhost:5678
```

It also lists any workflow that is untagged *and* unmapped — i.e. currently hidden from
everyone and waiting for a human to decide who owns it.

> **The API key needs tag scopes.** Reading and writing workflows is not enough: creating a tag
> and attaching it are separate permissions, and a workflow-only key returns **403** on every
> tag call. Mint the key in n8n → Settings → API with tag read/create **and** workflow "update
> tags". Both this script and `scripts/provision-client.mjs` check up front and stop with that
> message rather than tagging half the inventory.

New clients are tagged automatically: `provision-client.mjs` tags each cloned workflow with the
new org's slug *before* binding it as a tool (the bind would otherwise be refused with
`tools.n8n_forbidden`).

### Seeing everything at once

Platform staff get a cross-tenant view at **/admin → Automations**: every workflow, the org its
tags resolve to, whether it's active, and which agents bind it. Rows that are `untagged` or
whose tag matches no organization sort to the top — that list is the tagging backlog.

## 6. n8n → Vicero (the other direction)

To have a workflow act on Vicero, create a Vicero **API key** (Settings → API keys, `bf_`-prefixed)
and call the REST API from an HTTP Request node — e.g. create a conversation, post an inbox message,
or read analytics. See the [API usage guide](API-USAGE.md). Vicero also emits **outbound webhooks**
(`message.created`, `handoff.requested`, …) you can receive with an n8n Webhook node to trigger flows.

## 7. What Vicero sends

Every outbound tool call is a signed POST to the workflow's webhook:

```json
{ "args": { ... }, "mode": "sync|async", "run_id": "<uuid>",
  "conversation_id": "<uuid|null>", "callback_url": "<async only>" }
```

Headers: `X-Vicero-Signature` (HMAC-SHA256 of `"{timestamp}.{body}"` with
`N8N_WEBHOOK_SIGNING_SECRET`) and `X-Vicero-Timestamp`. Verify them for any production workflow.

## Troubleshooting

- **Workflows don't list in Automations** — `N8N_API_KEY` missing/invalid, or `N8N_BASE_URL` wrong.
  Bind by webhook URL as a fallback.
- **Tool call returns an error** — the workflow isn't **Active**, or (sync) has no *Respond to
  Webhook* node. Check the tool run under the agent's Tools tab and the n8n execution log.
- **Callback rejected** — async callback signature/`run_id` mismatch; sign with the same secret.
