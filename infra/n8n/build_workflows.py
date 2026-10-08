"""Build the global Error Workflow and (optionally) a TEST sample workflow in n8n through its public API.

Runs INSIDE a Vicero container (reads N8N_API_KEY / N8N_BASE_URL from its environment; never prints them):

  ssh vicero 'docker exec -i vicero-prod-worker-1 python - error' < infra/n8n/build_workflows.py

  error              create or update "Vicero | Report failed run (global)" (idempotent) and activate it
  sample <org_id>    create "ORG-<org_id> | Vicero TEST | echo sample" (active); webhook /webhook/zz-test-echo;
                     POST {"fail": true} to make it fail through the global Error Workflow
  delete-sample      remove every workflow whose name contains "| Vicero TEST |"

Every client workflow ends with the same three steps: build the JSON body, sign it (HMAC-SHA256 over
"<timestamp>.<body>" with AUTOMATION_REPORT_SECRET from the n8n environment), POST it to
http://api:8000/internal/automations/runs. No Code nodes. See docs/26 and docs/25 section 12.
"""
import json
import os
import sys
import uuid

import httpx

BASE = os.environ.get("N8N_BASE_URL", "http://n8n:5678").rstrip("/")
KEY = os.environ["N8N_API_KEY"]
H = {"X-N8N-API-KEY": KEY, "Content-Type": "application/json", "Accept": "application/json"}
API = f"{BASE}/api/v1"
ERROR_NAME = "Vicero | Report failed run (global)"
REPORT_URL = "http://api:8000/internal/automations/runs"


def nid() -> str:
    return str(uuid.uuid4())


def node(name, type_, version, params, pos, **extra):
    return {"id": nid(), "name": name, "type": type_, "typeVersion": version, "position": pos, "parameters": params, **extra}


def set_node(name, assignments, pos):
    return node(
        name,
        "n8n-nodes-base.set",
        3.4,
        {
            "assignments": {
                "assignments": [{"id": nid(), "name": k, "value": v, "type": "string"} for k, v in assignments.items()]
            },
            "includeOtherFields": True,
            "options": {},
        },
        pos,
    )


def sign_node(pos):
    # HMAC-SHA256 over "<timestamp>.<body>" with AUTOMATION_REPORT_SECRET (read from the environment, never typed in).
    return node(
        "Sign report",
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


def post_node(pos, name="Report run to Vicero"):
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.2,
        {
            "method": "POST",
            "url": REPORT_URL,
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
            "options": {"timeout": 10000},
        },
        pos,
    )


ORG_EXPR = "($workflow.name.match(/^ORG-([0-9a-fA-F-]{36})/) || [])[1]"


def error_workflow():
    trig = node("Error Trigger", "n8n-nodes-base.errorTrigger", 1, {}, [0, 0])
    only_org = node(
        "Only client workflows",
        "n8n-nodes-base.if",
        2,
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                "conditions": [
                    {
                        "id": nid(),
                        "leftValue": "={{ $json.workflow.name }}",
                        "rightValue": "ORG-",
                        "operator": {"type": "string", "operation": "startsWith"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        [220, 0],
    )
    build = set_node(
        "Build failure report",
        {
            "ts": "={{ String(Math.floor($now.toSeconds())) }}",
            "body": (
                "={{ JSON.stringify({ workflow_id: String($json.workflow.id),"
                " org_id: ($json.workflow.name.match(/^ORG-([0-9a-fA-F-]{36})/) || [])[1],"
                " execution_id: String($json.execution.id), status: 'failed',"
                " started_at: $now.toISO(), finished_at: $now.toISO(),"
                " error: String(($json.execution.error && $json.execution.error.message) || 'failed') }) }}"
            ),
        },
        [440, 0],
    )
    nodes = [trig, only_org, build, sign_node([660, 0]), post_node([880, 0], "Report failed run to Vicero")]
    conns = {
        "Error Trigger": {"main": [[{"node": "Only client workflows", "type": "main", "index": 0}]]},
        "Only client workflows": {"main": [[{"node": "Build failure report", "type": "main", "index": 0}], []]},
        "Build failure report": {"main": [[{"node": "Sign report", "type": "main", "index": 0}]]},
        "Sign report": {"main": [[{"node": "Report failed run to Vicero", "type": "main", "index": 0}]]},
    }
    return {"name": ERROR_NAME, "nodes": nodes, "connections": conns, "settings": {"executionOrder": "v1"}}


def sample_workflow(org_id: str, error_wf_id: str):
    hook = node(
        "Webhook",
        "n8n-nodes-base.webhook",
        2,
        {"httpMethod": "POST", "path": "zz-test-echo", "responseMode": "lastNode", "options": {}},
        [0, 0],
        webhookId=nid(),
    )
    start = set_node("Mark start", {"started_at": "={{ $now.toISO() }}"}, [220, 0])
    gate = node(
        "Should it fail?",
        "n8n-nodes-base.if",
        2,
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                "conditions": [
                    {
                        "id": nid(),
                        "leftValue": "={{ $('Webhook').item.json.body.fail }}",
                        "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        [440, 0],
    )
    boom = node(
        "Call a service that is down",
        "n8n-nodes-base.httpRequest",
        4.2,
        {"method": "GET", "url": "http://zz-test-service-that-does-not-exist.invalid/", "options": {"timeout": 5000}},
        [660, -120],
    )
    echo = set_node(
        "Echo input",
        {
            "echo": "={{ JSON.stringify($('Webhook').item.json.body) }}",
            "ts": "={{ String(Math.floor($now.toSeconds())) }}",
            "body": (
                "={{ JSON.stringify({ workflow_id: String($workflow.id), org_id: " + ORG_EXPR + ","
                " execution_id: String($execution.id), status: 'success',"
                " started_at: $('Mark start').item.json.started_at, finished_at: $now.toISO(),"
                " input: $('Webhook').item.json.body, output: { echoed: true } }) }}"
            ),
        },
        [660, 120],
    )
    nodes = [hook, start, gate, boom, echo, sign_node([880, 120]), post_node([1100, 120])]
    conns = {
        "Webhook": {"main": [[{"node": "Mark start", "type": "main", "index": 0}]]},
        "Mark start": {"main": [[{"node": "Should it fail?", "type": "main", "index": 0}]]},
        "Should it fail?": {
            "main": [
                [{"node": "Call a service that is down", "type": "main", "index": 0}],
                [{"node": "Echo input", "type": "main", "index": 0}],
            ]
        },
        "Echo input": {"main": [[{"node": "Sign report", "type": "main", "index": 0}]]},
        "Sign report": {"main": [[{"node": "Report run to Vicero", "type": "main", "index": 0}]]},
    }
    return {
        "name": f"ORG-{org_id} | Vicero TEST | echo sample",
        "nodes": nodes,
        "connections": conns,
        "settings": {"executionOrder": "v1", "errorWorkflow": error_wf_id},
    }


def find(c: httpx.Client, name: str):
    r = c.get(f"{API}/workflows", params={"limit": 250})
    r.raise_for_status()
    return next((w for w in r.json().get("data", []) if w.get("name") == name), None)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "error"
    with httpx.Client(headers=H, timeout=30) as c:
        existing = find(c, ERROR_NAME)
        if mode == "error":
            body = error_workflow()
            if existing:
                r = c.put(f"{API}/workflows/{existing['id']}", json=body)
                wid = existing["id"]
            else:
                r = c.post(f"{API}/workflows", json=body)
                wid = r.json().get("id") if r.status_code < 300 else None
            if r.status_code >= 300:
                print("error workflow FAILED", r.status_code, r.text[:300]); sys.exit(1)
            a = c.post(f"{API}/workflows/{wid}/activate")
            print("error workflow id:", wid, "| saved:", r.status_code, "| activate:", a.status_code, a.text[:120] if a.status_code >= 300 else "")
        elif mode == "sample":
            org_id = sys.argv[2]
            assert existing, "build the error workflow first"
            body = sample_workflow(org_id, existing["id"])
            old = find(c, body["name"])
            if old:
                c.post(f"{API}/workflows/{old['id']}/deactivate"); c.delete(f"{API}/workflows/{old['id']}")
            r = c.post(f"{API}/workflows", json=body)
            if r.status_code >= 300:
                print("sample FAILED", r.status_code, r.text[:400]); sys.exit(1)
            wid = r.json()["id"]
            a = c.post(f"{API}/workflows/{wid}/activate")
            print(json.dumps({"sample_workflow_id": wid, "activate": a.status_code, "detail": a.text[:200] if a.status_code >= 300 else ""}))
        elif mode == "delete-sample":
            for w in c.get(f"{API}/workflows", params={"limit": 250}).json().get("data", []):
                if "| Vicero TEST |" in w.get("name", ""):
                    c.post(f"{API}/workflows/{w['id']}/deactivate"); c.delete(f"{API}/workflows/{w['id']}")
                    print("deleted test workflow", w["id"])


main()
