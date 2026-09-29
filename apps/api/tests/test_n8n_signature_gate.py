"""RISK-REGISTER R15: an n8n workflow may only be bound as a tool if it verifies Vicero's
webhook signature, and the audit finds pre-existing binds that don't."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.integrations.n8n_client import N8nClient
from app.integrations.n8n_signature import unverified_reason
from app.models import Tool, User
from app.modules.admin import service as admin_service
from app.tools import service as tools_service

N8N_DIR = Path(__file__).resolve().parents[3] / "infra" / "n8n"
SHIPPED = sorted(N8N_DIR.rglob("*.json"))
VERIFY = "Verify Vicero signature"


def _signed(path: str = "gate-ok", wf_id: str = "1", tag: str = "gateco") -> dict[str, Any]:
    wf = copy.deepcopy(json.loads((N8N_DIR / "vicero-echo.json").read_text(encoding="utf-8")))
    wf["id"], wf["name"], wf["tags"] = wf_id, f"Flow {wf_id}", [{"name": tag}]
    wf["nodes"][0]["parameters"]["path"] = path
    return wf


def _unsigned(path: str = "gate-bad", wf_id: str = "2", tag: str = "gateco") -> dict[str, Any]:
    """The echo workflow with the verify chain cut out: Webhook -> Respond directly."""
    wf = _signed(path, wf_id, tag)
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in (VERIFY, "Signature valid?", "Reject 401")]
    wf["connections"] = {"Webhook": {"main": [[{"node": "Respond to Webhook", "type": "main", "index": 0}]]}}
    return wf


def _n8n(workflows: list[dict[str, Any]], api_key: str = "k") -> N8nClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/workflows":
            return httpx.Response(200, json={"data": workflows})
        wid = request.url.path.rsplit("/", 1)[-1]
        for w in workflows:
            if w["id"] == wid:
                return httpx.Response(200, json=w)
        return httpx.Response(404, json={})

    return N8nClient("http://n8n", api_key, transport=httpx.MockTransport(handler))


# ── the structural lint ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_every_shipped_workflow_passes(path: Path) -> None:
    assert unverified_reason(json.loads(path.read_text(encoding="utf-8"))) is None


def test_bypassed_verifier_is_rejected() -> None:
    reason = unverified_reason(_unsigned())
    assert reason is not None and "directly" in reason


def test_missing_raw_body_is_rejected() -> None:
    wf = _signed()
    wf["nodes"][0]["parameters"]["options"] = {}
    reason = unverified_reason(wf)
    assert reason is not None and "Raw Body" in reason


def test_verifier_that_is_never_consulted_is_rejected() -> None:
    wf = _signed()
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] != "Signature valid?"]
    wf["connections"][VERIFY] = {"main": [[{"node": "Respond to Webhook", "type": "main", "index": 0}]]}
    reason = unverified_reason(wf)
    assert reason is not None and "verified" in reason


def test_a_node_merely_named_like_the_verifier_is_rejected() -> None:
    wf = _unsigned()
    decoy = {"name": VERIFY, "type": "n8n-nodes-base.code", "parameters": {"jsCode": "return $input.all();"}}
    wf["nodes"].append(decoy)
    wf["connections"] = {"Webhook": {"main": [[{"node": VERIFY, "type": "main", "index": 0}]]}}
    assert unverified_reason(wf) is not None


def test_workflow_without_webhook_is_rejected() -> None:
    wf = _signed()
    wf["nodes"] = wf["nodes"][1:]
    assert "no Webhook" in (unverified_reason(wf) or "")


# ── the gate itself ─────────────────────────────────────────────────────────────────
async def test_gate_rejects_unsigned_workflow_fetched_by_id() -> None:
    wf = _unsigned()
    with pytest.raises(AppError) as exc:
        await tools_service._require_signed_workflow(_n8n([wf]), wf, "http://n8n/webhook/gate-bad")
    assert exc.value.code == "tools.n8n_unsigned_workflow"
    assert "infra/n8n/README.md" in exc.value.message
    assert exc.value.details["workflow_id"] == "2"


async def test_gate_accepts_signed_workflow() -> None:
    wf = _signed()
    await tools_service._require_signed_workflow(_n8n([wf]), wf, "http://n8n/webhook/gate-ok")


async def test_gate_resolves_a_pasted_url_on_another_hostname() -> None:
    """The public hostname differs from N8N_BASE_URL: matched on the /webhook/<path> tail."""
    with pytest.raises(AppError) as exc:
        await tools_service._require_signed_workflow(
            _n8n([_unsigned()]), None, "https://n8n.example.com/webhook/gate-bad"
        )
    assert exc.value.code == "tools.n8n_unsigned_workflow"
    await tools_service._require_signed_workflow(
        _n8n([_signed()]), None, "https://n8n.example.com/webhook/gate-ok"
    )


async def test_gate_refuses_a_url_that_matches_no_workflow() -> None:
    with pytest.raises(AppError) as exc:
        await tools_service._require_signed_workflow(_n8n([_signed()]), None, "http://n8n/webhook/nope")
    assert exc.value.code == "tools.n8n_unverifiable"


async def test_gate_refuses_a_test_url() -> None:
    """`/webhook-test/` is the editor's URL, not a production endpoint — never matched."""
    with pytest.raises(AppError) as exc:
        await tools_service._require_signed_workflow(_n8n([_signed()]), None, "http://n8n/webhook-test/gate-ok")
    assert exc.value.code == "tools.n8n_unverifiable"


async def test_gate_refuses_when_n8n_api_is_not_configured() -> None:
    with pytest.raises(AppError) as exc:
        await tools_service._require_signed_workflow(
            _n8n([_signed()], api_key=""), None, "http://n8n/webhook/gate-ok"
        )
    assert exc.value.code == "tools.n8n_unverifiable"


# ── through the real bind endpoint ──────────────────────────────────────────────────
async def _org_headers(client: AsyncClient, email: str) -> tuple[dict[str, str], str]:
    su = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = su.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "GateCo"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}, org.json()["slug"]


async def test_bind_endpoint_rejects_unsigned_and_accepts_signed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, slug = await _org_headers(client, "gate@example.com")
    workflows = [_signed(tag=slug), _unsigned(tag=slug)]
    monkeypatch.setattr(tools_service, "get_client", lambda **_k: _n8n(workflows))

    bad_id = await client.post("/v1/tools/n8n/bind", json={"name": "bad_id", "workflow_id": "2"}, headers=headers)
    assert bad_id.status_code == 400, bad_id.text
    assert bad_id.json()["error"]["code"] == "tools.n8n_unsigned_workflow"

    bad_url = await client.post(
        "/v1/tools/n8n/bind", json={"name": "bad_url", "webhook_url": "http://n8n/webhook/gate-bad"}, headers=headers
    )
    assert bad_url.status_code == 400, bad_url.text

    # A verified workflow_id must not launder an unverified URL: the URL is what gets called.
    mixed = await client.post(
        "/v1/tools/n8n/bind",
        json={"name": "mixed", "workflow_id": "1", "webhook_url": "http://n8n/webhook/gate-bad"},
        headers=headers,
    )
    assert mixed.status_code == 400, mixed.text

    ok_id = await client.post("/v1/tools/n8n/bind", json={"name": "ok_id", "workflow_id": "1"}, headers=headers)
    assert ok_id.status_code == 201, ok_id.text
    ok_url = await client.post(
        "/v1/tools/n8n/bind", json={"name": "ok_url", "webhook_url": "http://n8n/webhook/gate-ok"}, headers=headers
    )
    assert ok_url.status_code == 201, ok_url.text


async def test_bind_endpoint_gate_can_be_turned_off_for_a_dev_n8n(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "n8n_require_signature_check", False)
    headers, _slug = await _org_headers(client, "gateoff@example.com")
    r = await client.post(
        "/v1/tools/n8n/bind", json={"name": "dev_tool", "webhook_url": "http://n8n/webhook/anything"}, headers=headers
    )
    assert r.status_code == 201, r.text


# ── the audit ───────────────────────────────────────────────────────────────────────
async def _staff(client: AsyncClient, db_session: AsyncSession, email: str) -> tuple[dict[str, str], uuid.UUID, str]:
    headers, slug = await _org_headers(client, email)
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.is_staff = True
    await db_session.flush()
    return headers, uuid.UUID(headers["X-Org-Id"]), slug


async def test_audit_flags_a_preexisting_unverified_bind(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, org_id, slug = await _staff(client, db_session, "auditor@example.com")
    # Rows inserted directly = binds that predate the gate (it would refuse them now).
    for name, wf_id, path in (("old_bad", "2", "gate-bad"), ("old_ok", "1", "gate-ok"), ("old_gone", "9", "vanished")):
        db_session.add(
            Tool(
                organization_id=org_id,
                name=name,
                type="n8n",
                enabled=True,
                config={"workflow_id": wf_id, "webhook_url": f"http://n8n/webhook/{path}", "mode": "sync"},
                input_schema={},
            )
        )
    await db_session.flush()
    monkeypatch.setattr(admin_service, "get_n8n_client", lambda **_k: _n8n([_signed(), _unsigned()]))

    r = await client.get("/v1/admin/n8n-signature-audit", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["verified"], body["unverified"], body["unresolved"]) == (1, 1, 1)
    assert [f["tool_name"] for f in body["findings"]] == ["old_bad", "old_gone"]  # unverified first
    bad = body["findings"][0]
    assert bad["status"] == "unverified" and bad["organization_slug"] == slug
    assert "directly" in bad["reason"] and bad["workflow_id"] == "2"
    assert "infra/n8n/README.md" in body["fix_hint"]


async def test_audit_reports_n8n_being_down(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _org, _slug = await _staff(client, db_session, "auditor2@example.com")
    monkeypatch.setattr(admin_service, "get_n8n_client", lambda **_k: _n8n([], api_key=""))
    r = await client.get("/v1/admin/n8n-signature-audit", headers=headers)
    assert r.status_code == 200 and r.json()["error"]


async def test_audit_is_staff_only(client: AsyncClient) -> None:
    headers, _slug = await _org_headers(client, "plain2@example.com")
    r = await client.get("/v1/admin/n8n-signature-audit", headers=headers)
    assert r.status_code == 403
