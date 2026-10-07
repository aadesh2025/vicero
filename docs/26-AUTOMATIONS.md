# 26 — Client-visible Automations (n8n behind Vicero)

Clients get an **Automations** page: their automations, status, run counts, errors and a 30-day history. They never see
n8n. Platform staff build the workflows in a private n8n and register them to a workspace.

## Model
| Table | Purpose |
|---|---|
| `automation_requests` | What a client asked for (`requested → building → active/paused/rejected`). |
| `automations` | A registered workflow: org, optional agent, `n8n_workflow_id` (unique, so one org per workflow), optional `webhook_path`, status `active/paused`, `config_encrypted` (app key, never returned). |
| `automation_runs` | One row per execution: status, times, duration, **sanitized** `error_summary`, redacted size-limited input/output summaries, `over_cap`. Unique `(automation_id, n8n_execution_id)`. Deleted after 30 days. |

## Tenant safety
* Every tenant query filters by `organization_id`; another org's row is a `404` identical to a missing one.
* The tenant API is read-only apart from "request an automation" (plan-gated). No edit, no n8n link.
* Ingestion (`POST /internal/automations/runs`): HMAC-SHA256 over `<timestamp>.<raw body>` with `AUTOMATION_REPORT_SECRET`
  (constant-time compare, 300 s window, one use per signature, 32 KB max, rate-limited). The `org_id` in the body is a
  **claim**: the workflow must be registered and owned by that org, otherwise a generic `404` and a security log line.
  Unset secret → `503`, nothing recorded. Idempotent by `(automation, execution id)`; a final status is never walked back.
* A pull job (Celery beat, 5 min) fills gaps from the n8n API for **registered workflows only**; pushed rows are not overwritten.
* Sanitizer: credentials, bearer tokens, API keys, JWTs and long opaque strings are removed; sensitive JSON keys are dropped;
  fields are truncated; raw errors are replaced by one plain sentence.
* Agent calls: an n8n tool binds and runs only against an **Active** automation of the same org and agent, and only while the
  monthly run cap allows (`N8N_REQUIRE_REGISTERED_AUTOMATION`, on in production).

## Plans (`app/core/plans.py` only)
| Plan | Automations | Runs / month |
|---|---:|---:|
| Trial, Starter | 0 | 0 |
| Pro | 5 | 2,000 |
| Business | 20 | 10,000 |
| Legacy | unlimited | unlimited |

Over the run cap runs are still recorded and flagged `over_cap`, and the agent is told in plain words that the monthly limit
is reached. A request when no slot is free gets the usual `402 plan_limit` upgrade message.

## n8n side (staff, over SSH or the editor)
* Naming: `ORG-<org_id> | <client name> | <purpose>`; no emails or phone numbers in names or tags.
* A global Error Workflow and every client workflow end with a signed "report run to Vicero" step.
* No Code nodes in client workflows (use the Crypto/HTTP Request nodes); prefer one shared workflow when a capability fits
  several clients. Test with sample data before activating, then register it (`POST /v1/admin/automation-registry`).
* Per-org secrets are **not** sent to n8n per call: n8n stores webhook input in execution data. Use n8n credentials
  (encrypted with `N8N_ENCRYPTION_KEY`). See ADR-111.

Operations: docs/25 §11.
