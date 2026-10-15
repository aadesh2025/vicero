"""Generate the three booking workflows (infra/n8n/workflows/*.json). ADR-120.

    python infra/n8n/build_booking_workflows.py          # rewrites infra/n8n/workflows/

Each is `Webhook (raw body) -> Verify Vicero signature -> Signature valid? -> Prepare (validate) -> Input valid? ->
Sign -> POST /internal/tools/<x> on Vicero -> Respond`. The shipped verify chain is copied from
`template-starter-automation.json`, so the signature lint (`app.integrations.n8n_signature`) passes.

`book_appointment` adds an optional calendar mirror after the booking: a Google Calendar node that is **disabled** until
someone attaches a Google credential to it in the n8n editor (no secret is ever in this JSON). With it off, the booking
is still written to Vicero's `bookings` table and confirmed to the customer; with it on, the event id is reported back.

Secrets stay in the n8n environment (`AUTOMATION_REPORT_SECRET`, `N8N_WEBHOOK_SIGNING_SECRET`); the Vicero URL is
`VICERO_API_INTERNAL_URL` (compose network only). Node ids are derived from names so a rebuild is a no-diff.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "workflows"
TEMPLATE = json.loads((HERE / "template-starter-automation.json").read_text(encoding="utf-8"))
_NS = uuid.UUID("5d1c6c7e-0b0c-4b7e-9a5e-000000000120")

URL_EXPR = "={{ ($env.VICERO_API_INTERNAL_URL || 'http://api:8000') + '/internal/tools/%s' }}"


def _id(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def node(name: str, type_: str, version: float, params: dict[str, Any], pos: list[int], **extra: Any) -> dict[str, Any]:
    return {"id": _id(name), "name": name, "type": type_, "typeVersion": version, "position": pos, "parameters": params, **extra}


def _copy(name: str, pos: list[int]) -> dict[str, Any]:
    src = next(n for n in TEMPLATE["nodes"] if n["name"] == name)
    out = json.loads(json.dumps(src))
    out["id"], out["position"] = _id(name), pos
    return out


def webhook(path: str) -> dict[str, Any]:
    return node(
        "Webhook",
        "n8n-nodes-base.webhook",
        2,
        {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {"rawBody": True}},
        [0, 0],
        webhookId=path,
    )


def prepare(js: str, pos: list[int], name: str = "Prepare request") -> dict[str, Any]:
    return node(name, "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": js}, pos)


def valid_if(pos: list[int]) -> dict[str, Any]:
    return node(
        "Input valid?",
        "n8n-nodes-base.if",
        2.2,
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [
                    {
                        "id": "v1",
                        "leftValue": "={{ $json.valid }}",
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        pos,
    )


def sign(name: str, pos: list[int]) -> dict[str, Any]:
    # HMAC-SHA256 over "<timestamp>.<body>" with AUTOMATION_REPORT_SECRET, read from the n8n environment.
    return node(
        name,
        "n8n-nodes-base.crypto",
        1,
        {
            "action": "hmac",
            "type": "SHA256",
            "value": "={{ $json.ts }}.{{ $json.body }}",
            "dataPropertyName": "signature",
            "secret": "={{ $env.AUTOMATION_REPORT_SECRET }}",
            "encoding": "hex",
        },
        pos,
    )


def post(name: str, endpoint: str, pos: list[int]) -> dict[str, Any]:
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.2,
        {
            "method": "POST",
            "url": URL_EXPR % endpoint,
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "X-Vicero-Timestamp", "value": "={{ $json.ts }}"},
                    {"name": "X-Vicero-Signature", "value": "={{ $json.signature }}"},
                ]
            },
            "sendBody": True,
            "contentType": "raw",
            "rawContentType": "application/json",
            "body": "={{ $json.body }}",
            # Vicero answers validation problems as 200 {ok:false}; a 4xx here is an auth problem, still passed on as JSON.
            # `neverError` only: adding `responseFormat: json` next to it crashed n8n 2.36/2.43 (V8 fatal) in testing.
            "options": {"timeout": 15000, "response": {"response": {"neverError": True}}},
        },
        pos,
    )


def respond(name: str, expr: str, pos: list[int], code: int | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {"responseCode": code} if code else {}
    return node(name, "n8n-nodes-base.respondToWebhook", 1.1, {"respondWith": "json", "responseBody": expr, "options": options}, pos)


def link(*targets: str | None) -> dict[str, Any]:
    return {"main": [[{"node": t, "type": "main", "index": 0}] if t else [] for t in targets]}


# Code run in n8n before anything is sent: reject early, forward only the whitelisted fields, add the timestamp.
_HEAD = """// The call Vicero made: { args, run_id, callback_token, mode, conversation_id }. No secrets are read here.
const call = $input.first().json.body || {};
const a = (call.args && typeof call.args === 'object') ? call.args : {};
const errors = {};
const text = (v, max) => (typeof v === 'string' ? v.replace(/[\\u0000-\\u0008\\u000b-\\u001f\\u007f]/g, '').trim() : '');
const EMAIL = /^[^@\\s]{1,64}@[^@\\s]{1,255}\\.[^@\\s]{2,}$/;
const PHONE = /^\\+?\\d{7,15}$/;
if (typeof call.run_id !== 'string' || typeof call.callback_token !== 'string') errors.run = 'missing run context';
"""
_TAIL = """
const valid = Object.keys(errors).length === 0;
const ts = String(Math.floor(Date.now() / 1000));
const body = JSON.stringify({ run_id: call.run_id, callback_token: call.callback_token, args: clean });
return [{ json: { valid, errors, ts, body } }];
"""

BOOK_JS = _HEAD + """
const name = text(a.name, 120);
const contact = text(a.contact, 255);
const startIso = text(a.start_iso, 40);
const notes = text(a.notes, 500);
if (!name || name.length > 120) errors.name = 'required, at most 120 characters';
if (!(EMAIL.test(contact) || PHONE.test(contact.replace(/[\\s().-]/g, '')))) errors.contact = 'must be an email address or a phone number';
if (!startIso || Number.isNaN(Date.parse(startIso))) errors.start_iso = 'must be an ISO 8601 date and time, e.g. 2026-10-20T15:00:00+05:30';
if (notes.length > 500) errors.notes = 'at most 500 characters';
const clean = { name, contact, start_iso: startIso, notes: notes || undefined };
""" + _TAIL

AVAILABILITY_JS = _HEAD + """
const date = text(a.date, 10);
if (!/^\\d{4}-\\d{2}-\\d{2}$/.test(date) || Number.isNaN(Date.parse(date))) errors.date = 'must be YYYY-MM-DD';
const clean = { date };
""" + _TAIL

CUSTOMER_JS = _HEAD + """
const contact = text(a.contact, 255);
if (!contact) errors.contact = 'required';
const clean = { contact };
""" + _TAIL

ATTACH_JS = """// Report the calendar event this workflow just created back to Vicero (so the row can be mirrored/cancelled later).
const call = $('Webhook').first().json.body || {};
const booked = $('Book in Vicero').first().json;
const ts = String(Math.floor(Date.now() / 1000));
const body = JSON.stringify({ run_id: call.run_id, callback_token: call.callback_token, booking_id: booked.booking_id, event_id: String($json.id) });
return [{ json: { ts, body } }];
"""

INVALID_EXPR = "={{ { ok: false, error: 'invalid_input', fields: $json.errors } }}"
VICERO_EXPR = "={{ $('%s').first().json }}"


def simple(slug: str, endpoint: str, js: str) -> dict[str, Any]:
    nodes = [
        webhook(f"template-{slug}"),
        _copy("Verify Vicero signature", [260, 0]),
        _copy("Signature valid?", [520, 0]),
        _copy("Reject 401", [780, 360]),
        prepare(js, [780, 0]),
        valid_if([1040, 0]),
        respond("Respond invalid input", INVALID_EXPR, [1300, 200]),
        sign("Sign request", [1300, 0]),
        post("Call Vicero", endpoint, [1560, 0]),
        respond("Respond", "={{ $json }}", [1820, 0]),
    ]
    conns = {
        "Webhook": link("Verify Vicero signature"),
        "Verify Vicero signature": link("Signature valid?"),
        "Signature valid?": link("Prepare request", "Reject 401"),
        "Prepare request": link("Input valid?"),
        "Input valid?": link("Sign request", "Respond invalid input"),
        "Sign request": link("Call Vicero"),
        "Call Vicero": link("Respond"),
    }
    return {"nodes": nodes, "connections": conns}


def book() -> dict[str, Any]:
    nodes = [
        webhook("template-book-appointment"),
        _copy("Verify Vicero signature", [260, 0]),
        _copy("Signature valid?", [520, 0]),
        _copy("Reject 401", [780, 420]),
        prepare(BOOK_JS, [780, 0]),
        valid_if([1040, 0]),
        respond("Respond invalid input", INVALID_EXPR, [1300, 200]),
        sign("Sign request", [1300, 0]),
        post("Book in Vicero", "book", [1560, 0]),
        node(
            "New booking?",
            "n8n-nodes-base.if",
            2.2,
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                    "conditions": [
                        {
                            "id": "n1",
                            "leftValue": "={{ $json.ok === true && $json.duplicate === false }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            [1820, 0],
        ),
        # Off by default: enable it and pick a Google Calendar credential in the n8n editor. Until then the booking
        # lives in Vicero only. A failure here never fails the booking (continueRegularOutput).
        node(
            "Create Google Calendar event",
            "n8n-nodes-base.googleCalendar",
            1.3,
            {
                "calendar": {"__rl": True, "value": "primary", "mode": "id"},
                "start": "={{ $('Book in Vicero').first().json.start_iso }}",
                "end": "={{ new Date(new Date($('Book in Vicero').first().json.start_iso).getTime() + $('Book in Vicero').first().json.duration_minutes * 60000).toISOString() }}",
                "additionalFields": {
                    "summary": "={{ 'Appointment: ' + $('Webhook').first().json.body.args.name }}",
                    "description": "={{ 'Contact: ' + $('Webhook').first().json.body.args.contact + ' | Ref ' + $('Book in Vicero').first().json.reference + ' | ' + ($('Webhook').first().json.body.args.notes || '') }}",
                },
            },
            [2080, -120],
            disabled=True,
            onError="continueRegularOutput",
        ),
        node(
            "Event created?",
            "n8n-nodes-base.if",
            2.2,
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                    "conditions": [
                        {
                            "id": "e1",
                            "leftValue": "={{ typeof $json.id === 'string' && $json.id.length > 0 && $json.booking_id === undefined }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            [2340, -120],
        ),
        prepare(ATTACH_JS, [2600, -240], "Prepare event report"),
        sign("Sign event report", [2860, -240]),
        post("Attach event in Vicero", "attach-event", [3120, -240]),
        respond("Respond", VICERO_EXPR % "Book in Vicero", [3380, 0]),
    ]
    conns = {
        "Webhook": link("Verify Vicero signature"),
        "Verify Vicero signature": link("Signature valid?"),
        "Signature valid?": link("Prepare request", "Reject 401"),
        "Prepare request": link("Input valid?"),
        "Input valid?": link("Sign request", "Respond invalid input"),
        "Sign request": link("Book in Vicero"),
        "Book in Vicero": link("New booking?"),
        "New booking?": link("Create Google Calendar event", "Respond"),
        "Create Google Calendar event": link("Event created?"),
        "Event created?": link("Prepare event report", "Respond"),
        "Prepare event report": link("Sign event report"),
        "Sign event report": link("Attach event in Vicero"),
        "Attach event in Vicero": link("Respond"),
    }
    return {"nodes": nodes, "connections": conns}


WORKFLOWS: dict[str, dict[str, Any]] = {
    "book-appointment": {"title": "book_appointment", **book()},
    "check-availability": {"title": "check_availability", **simple("check-availability", "availability", AVAILABILITY_JS)},
    "get-customer": {"title": "get_customer", **simple("get-customer", "customer", CUSTOMER_JS)},
}


def build() -> dict[str, dict[str, Any]]:
    out = {}
    for slug, wf in WORKFLOWS.items():
        out[slug] = {
            "name": f"TEMPLATE | Booking | {wf['title']}",
            "nodes": wf["nodes"],
            "connections": wf["connections"],
            "settings": {"executionOrder": "v1"},
        }
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for slug, wf in build().items():
        (OUT / f"{slug}.json").write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote workflows/{slug}.json ({len(wf['nodes'])} nodes)")


if __name__ == "__main__":
    main()
