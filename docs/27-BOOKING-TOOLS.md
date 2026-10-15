# 27. Booking tools: tool calling through n8n (ADR-120)

Three agent tools, built on the existing agentic runtime (docs/17) and the n8n integration (docs/07, `infra/n8n/README.md`, docs/26):

| Tool | Arguments | Returns |
|---|---|---|
| `book_appointment` | `name`, `contact` (email or phone), `start_iso`, optional `notes` | booking id and reference, or `slot_taken` with alternatives, or a field-by-field validation error |
| `check_availability` | `date` (YYYY-MM-DD) | the day's free 30-minute slots (business hours minus existing bookings) |
| `get_customer` | `contact` | name, next and last booking, order status, the customer's own booking notes. **Only the person in the current conversation** |

## How it works

```
customer ── agent (LLM) ── tool call ──► Vicero  ── signed webhook ──► n8n workflow  ── signed call ──► Vicero /internal/tools/*
                                                       (run id + per-run token)   verify, validate,            (data lives here)
                                                                                  optional calendar mirror
```

- **n8n is the orchestration layer.** Each workflow: `Webhook (raw body)` → `Verify Vicero signature` → input check → sign → `POST /internal/tools/<x>` on Vicero → answer. It carries no data and no secret. `book_appointment` has one more step, a Google Calendar node, which **ships disabled** (below).
- **Vicero owns the data** (`bookings` table, migration 0034). A booking row is the source of truth for idempotency and availability; a calendar event is a mirror.
- **Tools run only with the agent's own credentials.** The internal API acts inside the organisation, agent and conversation of the tool run named in the call, proven by the per-run token Vicero issued when it called the workflow (`callback_token`, bound to run and org, valid 10 minutes). A workflow cannot book for another org or agent. Each organisation gets its own workflow copies (`ORG-<org id> | Booking | <tool>`, webhook path `<org slug>-<tool>`) and its own Google credential in n8n.
- **Private.** The workflows listen on n8n's internal webhook URL (compose network). Caddy answers 404 for public `/webhook/*` and `/webhook-test/*`. The internal API is not in the OpenAPI schema and rejects anything without a valid HMAC (`AUTOMATION_REPORT_SECRET`, fail closed when unset).
- **No secrets in the JSON.** The workflows read `AUTOMATION_REPORT_SECRET`, `N8N_WEBHOOK_SIGNING_SECRET` and `VICERO_API_INTERNAL_URL` from the n8n environment. No node carries a credential reference. A test fails if one appears.

## Rules enforced (and where)

| Rule | Enforced by |
|---|---|
| Confirmation goes back on the channel the customer used | `service._confirm` sends through the conversation's channel adapter, once per booking, and stores it in the transcript. The widget has no push channel, so there the tool result tells the model to confirm in its reply (`confirmation_sent: false`). A refused send (e.g. WhatsApp's 24-hour window) never undoes the booking |
| Idempotent booking | key = the tool-run id (unique per organisation in the database). A retried webhook returns the same booking with `duplicate: true` and sends **no** second message. The same customer and start time asked for in a different run is the same booking |
| Validated input | twice: in the n8n workflow (early reject, only whitelisted fields forwarded) and in Vicero (pydantic): length limits, email or phone, control characters stripped, ISO date and time, business hours, 30-minute grid, not in the past, at most 180 days ahead |
| Rate limit per contact | 5 booking attempts and 30 lookups per hour per contact (Redis); over the limit the tool says so and the agent hands over |
| Privacy | `get_customer` answers `found: false` for anyone except the current conversation's customer. Staff notes on the CRM record are never returned (only what the customer wrote on their own bookings) |
| Double booking | a taken slot answers `slot_taken` with up to four alternatives |

Business hours (Mon to Fri 10:00 to 17:30, Sat 10:00 to 13:30 last start, Sunday closed, in `BOOKING_TIMEZONE`, default `Asia/Kolkata`) are constants in `app/modules/bookings/service.py`, matching `docs/demo-knowledge/05-hours-and-booking.md`. A per-agent override is the next step.

## Install for an agent

Prerequisites on the server: n8n running, `N8N_API_KEY` set in the api container, the global error workflow installed (`infra/n8n/build_workflows.py error`, optional).

```
ssh vicero "cd /opt/vicero && git pull -q --ff-only && docker cp infra/n8n/workflows vicero-prod-api-1:/tmp/n8n-workflows"
ssh vicero "docker exec vicero-prod-api-1 python scripts/install_booking_tools.py --agent-id <AGENT_UUID> --dir /tmp/n8n-workflows --publish"
```

It imports and activates the three workflows, registers them in the automations registry for that agent, binds the three typed tools and turns on `tools_enabled` for the agent (published with `--publish`). Re-run it after editing `infra/n8n/workflows/*.json` (edit `build_booking_workflows.py`, run it, commit both).

## Verify

```
ssh vicero "docker exec vicero-prod-api-1 python scripts/test_booking_tools.py --agent-id <AGENT_UUID>"
```

Goes through the real path (signed webhook, n8n, Vicero), with a throwaway contact, then deletes everything it made. Expect `8/8 steps passed`.

## Google Calendar (needs your OAuth credential)

Nothing here can be done by the code: a Google account has to grant access. Until it is done, bookings are stored in Vicero and confirmed to the customer; they simply do not appear in a calendar.

1. n8n editor (`https://<N8N_DOMAIN>`, staff gate) → **Credentials** → New → *Google Calendar OAuth2 API*. The redirect URL it shows must be added to the Google Cloud OAuth client (Calendar API enabled). Name it `ORG-<org id> | Google Calendar`.
2. Open the workflow `ORG-<org id> | Booking | book_appointment` → node **Create Google Calendar event** → choose that credential, set the calendar (default `primary`), switch the node **on** (it ships disabled), save.
3. Run the verify script: the line `info  calendar:` should now say `event <id>`.

The node keeps `On error: continue`, so a Google outage never fails a booking. Not verified against a live Google account in this repo's tests (the n8n side was exercised with a mocked calendar step): check the first real event in the calendar.

**Known limit:** availability comes from Vicero's own bookings. Events created by hand in the Google calendar do not block slots yet.

## Files

`apps/api/app/modules/bookings/` (schemas with the tool JSON Schemas, service, internal router) · `apps/api/app/models/bookings.py` · migration `0034_bookings` · `infra/n8n/build_booking_workflows.py` → `infra/n8n/workflows/*.json` · `apps/api/scripts/install_booking_tools.py`, `scripts/test_booking_tools.py` · tests `test_bookings.py`, `test_booking_workflows.py`.
