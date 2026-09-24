"""Every n8n workflow shipped in `infra/n8n/` must verify BotForge's webhook signature (RISK-REGISTER R15).

BotForge signs each call to an n8n webhook (`integrations/n8n_client.sign`). A workflow that does not
*check* it can be called directly by anyone holding its URL — bypassing the agent, RBAC, budgets and
input validation. This file pins three things:

1. **Structure** — every JSON has Webhook(rawBody) -> Verify -> IF, with the false branch a 401, and the
   automation reachable only through the IF's true branch. A new workflow or template that skips it fails.
2. **Behaviour** — the actual JavaScript in the Verify node is executed under Node against signatures
   produced by BotForge's own Python `sign()`, so the two implementations cannot drift apart (skipped when
   `node` is not installed).
3. **Hygiene** — no secret and no runtime state (`staticData`) in the exported JSON.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings
from app.integrations import n8n_client

N8N_DIR = Path(__file__).resolve().parents[3] / "infra" / "n8n"
WORKFLOWS = sorted(N8N_DIR.rglob("*.json"))
VERIFY_NAME = "Verify BotForge signature"
IF_NAME = "Signature valid?"


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _node(wf: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [n for n in wf["nodes"] if n["name"] == name]
    assert len(matches) == 1, f"expected exactly one node named {name!r}, found {len(matches)}"
    return dict(matches[0])


def _targets(wf: dict[str, Any], source: str, output: int) -> list[str]:
    outputs = wf["connections"].get(source, {}).get("main", [])
    return [c["node"] for c in outputs[output]] if len(outputs) > output else []


def test_the_workflow_directory_is_not_empty() -> None:
    # A wrong path would make every parametrised test below vanish and pass vacuously.
    assert len(WORKFLOWS) >= 7, [p.name for p in WORKFLOWS]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: str(p.relative_to(N8N_DIR)))
def test_workflow_verifies_the_signature_before_doing_anything(path: Path) -> None:
    wf = _load(path)
    hooks = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook"]
    assert len(hooks) == 1
    hook = hooks[0]
    assert hook["parameters"].get("options", {}).get("rawBody") is True, "the MAC is over the raw bytes"
    assert hook["parameters"].get("responseMode") == "responseNode"

    # Webhook feeds ONLY the verifier, the verifier ONLY the IF.
    assert _targets(wf, hook["name"], 0) == [VERIFY_NAME]
    assert _targets(wf, VERIFY_NAME, 0) == [IF_NAME]
    assert len(wf["connections"][hook["name"]]["main"]) == 1

    # False branch answers 401; true branch continues into the automation.
    reject = _targets(wf, IF_NAME, 1)
    assert len(reject) == 1
    reject_node = _node(wf, reject[0])
    assert reject_node["type"] == "n8n-nodes-base.respondToWebhook"
    assert reject_node["parameters"]["options"]["responseCode"] == 401
    assert _targets(wf, IF_NAME, 0), "the true branch must lead to the automation"
    assert reject[0] not in _targets(wf, IF_NAME, 0)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: str(p.relative_to(N8N_DIR)))
def test_the_verifier_is_the_reference_implementation(path: Path) -> None:
    code = _node(_load(path), VERIFY_NAME)["parameters"]["jsCode"]
    for must in (
        "createHmac('sha256'",
        "timingSafeEqual",
        "x-botforge-signature",
        "x-botforge-timestamp",
        "ts + '.'",
        "> 300",
        "$env['N8N_WEBHOOK_SIGNING_SECRET']",
        "getBinaryDataBuffer",
    ):
        assert must in code, f"{path.name}: verifier lost {must!r}"
    assert "if (!secret) return reject" in code, "must fail closed when no secret is configured"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: str(p.relative_to(N8N_DIR)))
def test_export_carries_no_secret_and_no_runtime_state(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    wf = json.loads(raw)
    assert not {"staticData", "pinData", "id", "versionId", "active"} & set(wf), "runtime state leaked into the export"
    for secret in (settings.n8n_webhook_signing_secret, settings.secret_key):
        if secret and len(secret) >= 16:
            assert secret not in raw, "a real secret is committed in a workflow export"


def test_provisioning_refuses_an_unsigned_template() -> None:
    script = (N8N_DIR.parents[1] / "scripts" / "provision-client.mjs").read_text(encoding="utf-8")
    assert VERIFY_NAME in script and "ProvisionError" in script


def test_the_scheme_documented_for_workflows_is_what_sign_produces(monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import hmac

    monkeypatch.setattr(settings, "n8n_webhook_signing_secret", "unit-test-secret")
    ts, sig = n8n_client.sign(b'{"args":{}}', timestamp="1790000000")
    assert ts == "1790000000"
    assert sig == hmac.new(b"unit-test-secret", b'1790000000.{"args":{}}', hashlib.sha256).hexdigest()


# ── behaviour: run the real Verify node code under Node ───────────────────────────────────────
HARNESS = r"""
const crypto = require('crypto');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
let input = '';
process.stdin.on('data', (d) => (input += d));
process.stdin.on('end', async () => {
  const { code, cases } = JSON.parse(input);
  const fn = new AsyncFunction('$env', '$input', 'require', 'Buffer', code);
  const out = [];
  for (const c of cases) {
    const raw = Buffer.from(c.raw, 'utf8');
    const binary = c.hasRaw ? { data: { data: raw.toString('base64') } } : undefined;
    const item = { json: { headers: c.headers }, binary };
    const ctx = { helpers: { getBinaryDataBuffer: async () => raw } };
    const res = await fn.call(ctx, c.env, { first: () => item }, require, Buffer);
    out.push({ name: c.name, verified: res[0].json.verified, reason: res[0].json.reason ?? null });
  }
  console.log(JSON.stringify(out));
});
"""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@needs_node
def test_verifier_javascript_accepts_botforge_signatures_and_rejects_everything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "behaviour-test-secret-0123456789"
    monkeypatch.setattr(settings, "n8n_webhook_signing_secret", secret)
    body = json.dumps({"args": {"note": "héllo ✓", "n": 1}, "mode": "sync"})  # exactly BotForge's serialisation
    assert '"n": 1' in body  # the tampered case below must really change a byte
    ts, sig = n8n_client.sign(body.encode())
    good = {"x-botforge-signature": sig, "x-botforge-timestamp": ts}
    old_ts = str(int(time.time()) - 600)
    _, old_sig = n8n_client.sign(body.encode(), timestamp=old_ts)
    env = {"N8N_WEBHOOK_SIGNING_SECRET": secret}

    def case(name: str, **kw: Any) -> dict[str, Any]:
        return {"name": name, "raw": body, "headers": good, "env": env, "hasRaw": True, **kw}

    cases = [
        case("valid"),
        case("no_headers", headers={}),
        case("bad_mac", headers={**good, "x-botforge-signature": "0" * len(sig)}),
        case("stale", headers={"x-botforge-signature": old_sig, "x-botforge-timestamp": old_ts}),
        case("tampered_body", raw=body.replace("\"n\": 1", "\"n\": 2")),
        case("wrong_secret_in_n8n", env={"N8N_WEBHOOK_SIGNING_SECRET": "some-other-secret-value"}),
        case("no_secret_configured", env={}),
        case("empty_secret_configured", env={"N8N_WEBHOOK_SIGNING_SECRET": ""}),
        case("no_raw_body", hasRaw=False),
    ]
    code = _node(_load(N8N_DIR / "botforge-echo.json"), VERIFY_NAME)["parameters"]["jsCode"]
    run = subprocess.run(
        ["node", "-e", HARNESS], input=json.dumps({"code": code, "cases": cases}),
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert run.returncode == 0, run.stderr
    verdict = {r["name"]: r for r in json.loads(run.stdout)}
    assert verdict["valid"]["verified"] is True, verdict["valid"]
    for name, result in verdict.items():
        if name != "valid":
            assert result["verified"] is False, f"{name} was ACCEPTED: {result}"
    assert "not configured" in verdict["no_secret_configured"]["reason"]
    assert "not configured" in verdict["empty_secret_configured"]["reason"]
