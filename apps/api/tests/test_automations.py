"""Client-visible automations (docs/26): tenant isolation, signed ingestion, plan gating, caps, no leaks.

Two orgs (A and B) are created through the real API. Staff register workflows; "n8n" reports runs through the
real `/internal/automations/runs` endpoint with a real HMAC signature.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.core.config import settings
from app.integrations.n8n_client import N8nClient
from app.models import Organization, User
from app.models.automations import Automation, AutomationRun
from app.modules.automations import service as svc

SECRET = "test-report-secret-0123456789"
CONFIG_SECRET = "CONFIG-SECRET-VALUE-DO-NOT-LEAK"


@pytest.fixture(autouse=True)
def _report_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "automation_report_secret", SECRET)


# ── helpers ───────────────────────────────────────────────────────────────────────
async def _org(client: AsyncClient, db: AsyncSession, email: str, *, plan: str = "pro", staff: bool = False):
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": f"Org {email}"}, headers={"Authorization": f"Bearer {token}"})
    org_id = uuid.UUID(org.json()["id"])
    row = await db.get(Organization, org_id)
    assert row is not None
    row.plan = plan
    if staff:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.is_staff = True
    await db.flush()
    return {"Authorization": f"Bearer {token}", "X-Org-Id": str(org_id)}, org_id


async def _register(
    client: AsyncClient, staff: dict[str, str], org_id: uuid.UUID, workflow_id: str, **extra: Any
) -> dict[str, Any]:
    payload = {"organization_id": str(org_id), "n8n_workflow_id": workflow_id, "name": f"ORG-{org_id} | Test | demo"}
    payload.update(extra)
    resp = await client.post("/v1/admin/automation-registry", json=payload, headers=staff)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _report(workflow_id: str, org_id: uuid.UUID, execution_id: str, **extra: Any) -> dict[str, Any]:
    now = dt.datetime.now(tz=dt.UTC)
    body = {
        "workflow_id": workflow_id,
        "org_id": str(org_id),
        "execution_id": execution_id,
        "status": "success",
        "started_at": (now - dt.timedelta(seconds=2)).isoformat(),
        "finished_at": now.isoformat(),
    }
    body.update(extra)
    return body


async def _push(
    client: AsyncClient,
    payload: dict[str, Any],
    *,
    secret: str = SECRET,
    timestamp: int | None = None,
    raw: bytes | None = None,
):
    body = raw if raw is not None else json.dumps(payload).encode()
    ts = str(timestamp if timestamp is not None else int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return await client.post(
        "/internal/automations/runs",
        content=body,
        headers={"X-Vicero-Signature": sig, "X-Vicero-Timestamp": ts, "Content-Type": "application/json"},
    )


async def _runs(db: AsyncSession, automation_id: str) -> list[AutomationRun]:
    await db.flush()
    return list(
        (await db.execute(select(AutomationRun).where(AutomationRun.automation_id == uuid.UUID(automation_id))))
        .scalars()
        .all()
    )


# ── tenant isolation ──────────────────────────────────────────────────────────────
async def test_org_a_can_never_see_org_b(client: AsyncClient, db_session: AsyncSession) -> None:
    staff, staff_org = await _org(client, db_session, "iso-staff@example.com", staff=True)
    a, org_a = await _org(client, db_session, "iso-a@example.com")
    b, org_b = await _org(client, db_session, "iso-b@example.com")
    auto_a = await _register(client, staff, org_a, "wf-iso-a")
    auto_b = await _register(client, staff, org_b, "wf-iso-b")
    assert (await _push(client, _report("wf-iso-b", org_b, "e1", status="failed", error="boom 401"))).status_code == 200
    assert (await _push(client, _report("wf-iso-a", org_a, "e1"))).status_code == 200

    # list: only my own
    mine = (await client.get("/v1/automations", headers=a)).json()
    assert [x["id"] for x in mine] == [auto_a["id"]]

    # read / runs / detail of B's automation as A: 404, same as a random id
    for path in ("", "/runs"):
        foreign = await client.get(f"/v1/automations/{auto_b['id']}{path}", headers=a)
        random_id = await client.get(f"/v1/automations/{uuid.uuid4()}{path}", headers=a)
        assert foreign.status_code == random_id.status_code == 404
        assert foreign.json() == random_id.json()
    # B's data does not leak into A's counts either
    detail_a = (await client.get(f"/v1/automations/{auto_a['id']}", headers=a)).json()
    assert detail_a["runs_total"] == 1 and detail_a["failed_this_month"] == 0 and detail_a["last_failure"] is None
    # ... and B still sees their own failure
    detail_b = (await client.get(f"/v1/automations/{auto_b['id']}", headers=b)).json()
    assert detail_b["failed_this_month"] == 1 and detail_b["last_failure"]["status"] == "failed"
    # A cannot borrow B's identity by sending B's org header with A's token
    spoof = await client.get("/v1/automations", headers={**a, "X-Org-Id": str(org_b)})
    assert spoof.status_code in (403, 404)
    assert staff_org


async def test_client_responses_never_expose_n8n(client: AsyncClient, db_session: AsyncSession) -> None:
    staff, _ = await _org(client, db_session, "leak-staff@example.com", staff=True)
    a, org_a = await _org(client, db_session, "leak-a@example.com")
    auto = await _register(
        client,
        staff,
        org_a,
        "wf-secret-id-777",
        webhook_path="/webhook/secret-path-xyz",
        config={"api_key": CONFIG_SECRET},
    )
    await _push(
        client,
        _report(
            "wf-secret-id-777",
            org_a,
            "e1",
            status="failed",
            error="Authorization: Bearer abcdefghij0123456789 rejected",
        ),
    )
    seen = [
        (await client.get("/v1/automations", headers=a)).text,
        (await client.get(f"/v1/automations/{auto['id']}", headers=a)).text,
        (await client.get(f"/v1/automations/{auto['id']}/runs", headers=a)).text,
        (await client.get("/v1/automations/usage", headers=a)).text,
        (await client.get("/v1/automations/requests", headers=a)).text,
    ]
    for text in seen:
        for needle in ("wf-secret-id-777", "secret-path-xyz", CONFIG_SECRET, "abcdefghij0123456789", "n8n_workflow_id"):
            assert needle not in text, needle
    # staff view: the config is never returned even to staff, only whether one exists
    staff_view = (await client.get("/v1/admin/automation-registry", headers=staff)).text
    assert CONFIG_SECRET not in staff_view and '"has_config":true' in staff_view.replace(" ", "")


async def test_a_client_cannot_edit_anything_or_reach_staff_routes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff, _ = await _org(client, db_session, "ro-staff@example.com", staff=True)
    a, org_a = await _org(client, db_session, "ro-a@example.com")
    auto = await _register(client, staff, org_a, "wf-ro")
    base = f"/v1/automations/{auto['id']}"
    for method in ("patch", "put", "delete"):
        assert (await client.request(method.upper(), base, headers=a, json={"status": "paused"})).status_code == 405
    assert (await client.post(f"{base}/runs", headers=a, json={})).status_code == 405
    for path in ("/v1/admin/automation-registry", "/v1/admin/automation-requests"):
        assert (await client.get(path, headers=a)).status_code == 403
    assert (
        await client.post(
            "/v1/admin/automation-registry",
            headers=a,
            json={"organization_id": str(org_a), "n8n_workflow_id": "wf-x", "name": "x"},
        )
    ).status_code == 403
    assert (
        await client.patch(f"/v1/admin/automation-registry/{auto['id']}", headers=a, json={"status": "paused"})
    ).status_code == 403
    still = (await client.get(base, headers=a)).json()
    assert still["status"] == "active"


# ── signed ingestion ──────────────────────────────────────────────────────────────
async def test_a_signed_push_records_a_run(client: AsyncClient, db_session: AsyncSession) -> None:
    staff, _ = await _org(client, db_session, "push-staff@example.com", staff=True)
    a, org_a = await _org(client, db_session, "push-a@example.com")
    auto = await _register(client, staff, org_a, "wf-push")
    resp = await _push(client, _report("wf-push", org_a, "exec-1", input={"name": "Asha", "api_key": "k"}))
    assert resp.status_code == 200 and resp.json() == {"ok": True, "created": True}
    runs = await _runs(db_session, auto["id"])
    assert len(runs) == 1 and runs[0].status == "success" and runs[0].duration_ms == 2000
    assert "k" not in (runs[0].input_summary or "").replace("Asha", "").replace("name", "").replace("api_key", "")
    page = (await client.get(f"/v1/automations/{auto['id']}/runs", headers=a)).json()
    assert page["total"] == 1 and page["items"][0]["message"] == "Completed"


async def test_a_push_for_org_a_cannot_write_into_org_b(client: AsyncClient, db_session: AsyncSession) -> None:
    staff, _ = await _org(client, db_session, "x-staff@example.com", staff=True)
    _, org_a = await _org(client, db_session, "x-a@example.com")
    _, org_b = await _org(client, db_session, "x-b@example.com")
    auto_a = await _register(client, staff, org_a, "wf-x-a")
    auto_b = await _register(client, staff, org_b, "wf-x-b")

    with capture_logs() as logs:
        # B's workflow reported with A's org id, A's workflow reported with B's org id
        r1 = await _push(client, _report("wf-x-b", org_a, "e1"))
        r2 = await _push(client, _report("wf-x-a", org_b, "e2"))
    assert r1.status_code == r2.status_code == 404
    assert await _runs(db_session, auto_a["id"]) == [] and await _runs(db_session, auto_b["id"]) == []
    events = [e for e in logs if e["event"] == "automation_report_rejected"]
    assert {e["reason"] for e in events} == {"org_mismatch"} and len(events) == 2
    # the refusal is the same as for an unknown workflow: it reveals nothing about who owns what
    unknown = await _push(client, _report("wf-nobody", org_a, "e3"))
    assert unknown.status_code == 404 and unknown.json() == r1.json()


async def test_bad_signature_replay_stale_and_oversize_are_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff, _ = await _org(client, db_session, "bad-staff@example.com", staff=True)
    _, org_a = await _org(client, db_session, "bad-a@example.com")
    auto = await _register(client, staff, org_a, "wf-bad")
    good = _report("wf-bad", org_a, "e1")

    assert (await _push(client, good, secret="wrong-secret")).status_code == 401
    assert (await _push(client, good, timestamp=int(time.time()) - 3600)).status_code == 401
    assert (await _push(client, good, timestamp=int(time.time()) + 3600)).status_code == 401
    body = json.dumps(good).encode()
    # a signature made for different bytes is refused
    sig_for_other_body = hmac.new(SECRET.encode(), f"{int(time.time())}.".encode() + b"{}", hashlib.sha256).hexdigest()
    forged = await client.post(
        "/internal/automations/runs",
        content=body,
        headers={"X-Vicero-Signature": sig_for_other_body, "X-Vicero-Timestamp": str(int(time.time()))},
    )
    assert forged.status_code == 401
    assert await _runs(db_session, auto["id"]) == []

    fixed_ts = int(time.time())
    message = _report("wf-bad", org_a, "e-replay")  # built once: a replay is the SAME bytes sent again
    first = await _push(client, message, timestamp=fixed_ts)
    again = await _push(client, message, timestamp=fixed_ts)
    assert first.status_code == 200 and again.status_code == 409
    assert again.json()["error"]["code"] == "automations.replay"

    huge = {**good, "execution_id": "big", "output": {"blob": "x" * 40_000}}
    assert (await _push(client, huge)).status_code == 413


async def test_reporting_is_off_until_a_secret_is_configured(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "automation_report_secret", None)
    resp = await _push(client, _report("wf", uuid.uuid4(), "e1"), secret="")
    assert resp.status_code == 503


async def test_an_execution_is_recorded_once_and_a_final_status_sticks(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff, _ = await _org(client, db_session, "idem-staff@example.com", staff=True)
    _, org_a = await _org(client, db_session, "idem-a@example.com")
    auto = await _register(client, staff, org_a, "wf-idem")
    r1 = await _push(
        client, _report("wf-idem", org_a, "e1", status="running", finished_at=None), timestamp=int(time.time()) - 1
    )
    r2 = await _push(client, _report("wf-idem", org_a, "e1", status="success"))
    r3 = await _push(
        client, _report("wf-idem", org_a, "e1", status="running", finished_at=None), timestamp=int(time.time()) - 2
    )
    assert [r.json()["created"] for r in (r1, r2, r3)] == [True, False, False]
    runs = await _runs(db_session, auto["id"])
    assert len(runs) == 1 and runs[0].status == "success"  # a late "running" never walks a final status back


async def test_secrets_and_raw_errors_never_reach_storage_or_logs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff, _ = await _org(client, db_session, "san-staff@example.com", staff=True)
    a, org_a = await _org(client, db_session, "san-a@example.com")
    auto = await _register(client, staff, org_a, "wf-san")
    raw = "Request failed 401: Authorization: Bearer sk-live-ABCDEFGHIJKLMNOP1234 password=hunter2hunter2"
    with capture_logs() as logs:
        resp = await _push(
            client,
            _report(
                "wf-san", org_a, "e1", status="failed", error=raw, output={"token": "tok_abcdefghijklmnop", "ok": 1}
            ),
        )
    assert resp.status_code == 200
    (run,) = await _runs(db_session, auto["id"])
    stored = f"{run.error_summary} {run.input_summary} {run.output_summary}"
    for needle in ("sk-live-ABCDEFGHIJKLMNOP1234", "hunter2hunter2", "tok_abcdefghijklmnop", "Request failed 401"):
        assert needle not in stored and needle not in str(logs)
    assert run.error_summary == "A connected account refused the login. Our team will reconnect it."
    page = (await client.get(f"/v1/automations/{auto['id']}/runs", headers=a)).json()
    assert page["items"][0]["message"] == run.error_summary


# ── plan gating and caps ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("plan", ["trial", "starter"])
async def test_plans_without_automations_get_an_upgrade_message(
    client: AsyncClient, db_session: AsyncSession, plan: str
) -> None:
    a, _ = await _org(client, db_session, f"gate-{plan}@example.com", plan=plan)
    usage = (await client.get("/v1/automations/usage", headers=a)).json()
    assert usage["included"] is False and usage["can_request"] is False and usage["automations_limit"] == 0
    resp = await client.post("/v1/automations/requests", json={"description": "Send me a daily summary"}, headers=a)
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "plan_limit" and resp.json()["error"]["details"]["feature"] == "automations"
    assert "Upgrade" in resp.json()["error"]["message"]


async def test_pro_allows_five_and_the_sixth_request_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    a, _ = await _org(client, db_session, "pro@example.com", plan="pro")
    for i in range(5):
        ok = await client.post(
            "/v1/automations/requests", json={"description": f"Automation number {i} please"}, headers=a
        )
        assert ok.status_code == 201, ok.text
    sixth = await client.post("/v1/automations/requests", json={"description": "One automation too many"}, headers=a)
    assert sixth.status_code == 402 and "allows up to 5" in sixth.json()["error"]["message"]
    usage = (await client.get("/v1/automations/usage", headers=a)).json()
    assert usage["automations_used"] == 5 and usage["automations_limit"] == 5 and usage["can_request"] is False
    assert usage["runs_limit"] == 2000


async def test_a_request_for_another_orgs_agent_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    a, _ = await _org(client, db_session, "ag-a@example.com")
    b, _ = await _org(client, db_session, "ag-b@example.com")
    agent_b = (await client.post("/v1/agents", json={"name": "B bot"}, headers=b)).json()["id"]
    resp = await client.post(
        "/v1/automations/requests", json={"description": "Do a thing for me", "agent_id": agent_b}, headers=a
    )
    assert resp.status_code == 404


async def test_runs_over_the_monthly_cap_are_counted_flagged_and_block_the_agent(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import plans

    staff, _ = await _org(client, db_session, "cap-staff@example.com", staff=True)
    _, org_a = await _org(client, db_session, "cap-a@example.com", plan="pro")
    auto = await _register(client, staff, org_a, "wf-cap", webhook_path="/webhook/cap")
    # Shrink the cap for this test only (PlanSpec is a frozen dataclass, so replace it).
    import dataclasses

    small = dataclasses.replace(plans.PLANS["pro"], max_automation_runs=2)
    monkeypatch.setitem(plans.PLANS, "pro", small)

    for i in range(4):
        r = await _push(client, _report("wf-cap", org_a, f"e{i}"), timestamp=int(time.time()) - i)
        assert r.status_code == 200
    runs = sorted(await _runs(db_session, auto["id"]), key=lambda x: x.created_at)
    assert len(runs) == 4  # nothing is dropped
    assert sum(1 for r in runs if r.over_cap) == 2  # the 3rd and 4th were past the cap
    blocked = await svc.check_agent_call(db_session, org_a, uuid.uuid4(), "http://n8n/webhook/cap")
    assert blocked == svc.RUN_LIMIT_MESSAGE and "monthly run limit" in blocked


async def test_old_runs_are_deleted_after_30_days(client: AsyncClient, db_session: AsyncSession) -> None:
    staff, _ = await _org(client, db_session, "ret-staff@example.com", staff=True)
    _, org_a = await _org(client, db_session, "ret-a@example.com")
    auto = await _register(client, staff, org_a, "wf-ret")
    old = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=31)
    new = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=29)
    for eid, when in (("old", old), ("new", new)):
        await _push(
            client,
            _report(
                "wf-ret",
                org_a,
                eid,
                started_at=when.isoformat(),
                finished_at=(when + dt.timedelta(seconds=1)).isoformat(),
            ),
            timestamp=int(time.time()) - (0 if eid == "old" else 1),
        )
    removed = await svc.purge_old_runs(db_session)
    assert removed == 1
    assert [r.n8n_execution_id for r in await _runs(db_session, auto["id"])] == ["new"]


# ── agent path ────────────────────────────────────────────────────────────────────
async def test_an_agent_can_only_use_its_own_active_registered_automation(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "n8n_require_registered_automation", True)
    monkeypatch.setattr(settings, "n8n_require_signature_check", False)
    staff, _ = await _org(client, db_session, "ag2-staff@example.com", staff=True)
    a, org_a = await _org(client, db_session, "ag2-a@example.com")
    b, org_b = await _org(client, db_session, "ag2-b@example.com")
    agent_a = (await client.post("/v1/agents", json={"name": "A bot"}, headers=a)).json()["id"]
    other_agent_a = (await client.post("/v1/agents", json={"name": "A bot 2"}, headers=a)).json()["id"]
    auto = await _register(client, staff, org_a, "wf-ag", webhook_path="/webhook/ag-flow", agent_id=agent_a)

    def bind(headers: dict[str, str], agent_id: str):
        return client.post(
            "/v1/tools/n8n/bind",
            json={"name": "flow", "webhook_url": "http://n8n/webhook/ag-flow", "mode": "sync", "agent_id": agent_id},
            headers=headers,
        )

    # another org, even with its own agent, cannot bind it; neither can the owner's other agent
    agent_b = (await client.post("/v1/agents", json={"name": "B bot"}, headers=b)).json()["id"]
    assert (await bind(b, agent_b)).status_code == 403
    assert (await bind(a, other_agent_a)).status_code == 403
    ok = await bind(a, agent_a)
    assert ok.status_code == 201, ok.text

    # a paused automation stops being callable
    assert (
        await client.patch(f"/v1/admin/automation-registry/{auto['id']}", json={"status": "paused"}, headers=staff)
    ).status_code == 200
    blocked = await svc.check_agent_call(db_session, org_a, uuid.UUID(agent_a), "http://n8n/webhook/ag-flow")
    assert blocked == "This automation is not available."
    assert (
        await client.patch(f"/v1/admin/automation-registry/{auto['id']}", json={"status": "active"}, headers=staff)
    ).status_code == 200
    assert await svc.check_agent_call(db_session, org_a, uuid.UUID(agent_a), "http://n8n/webhook/ag-flow") is None
    # ... and never for another agent or another org
    assert (
        await svc.check_agent_call(db_session, org_a, uuid.UUID(other_agent_a), "http://n8n/webhook/ag-flow")
        is not None
    )
    assert await svc.check_agent_call(db_session, org_b, uuid.UUID(agent_b), "http://n8n/webhook/ag-flow") is not None


# ── pull (backup) ─────────────────────────────────────────────────────────────────
class _FakeN8n:
    def __init__(self, by_workflow: dict[str, list[dict[str, Any]]]) -> None:
        self.by_workflow = by_workflow
        self.asked: list[str] = []

    async def list_executions(self, workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.asked.append(workflow_id)
        return self.by_workflow.get(workflow_id, [])


async def test_the_pull_job_fills_gaps_for_registered_workflows_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff, _ = await _org(client, db_session, "pull-staff@example.com", staff=True)
    _, org_a = await _org(client, db_session, "pull-a@example.com")
    auto = await _register(client, staff, org_a, "wf-pull")
    await _push(client, _report("wf-pull", org_a, "pushed-1", output={"rich": "detail"}))
    now = dt.datetime.now(tz=dt.UTC)
    stamp = lambda s: (now - dt.timedelta(seconds=s)).isoformat()  # noqa: E731
    fake = _FakeN8n(
        {
            "wf-pull": [
                {"id": "pushed-1", "status": "error", "startedAt": stamp(30), "stoppedAt": stamp(29)},  # already pushed
                {"id": "missed-2", "status": "error", "startedAt": stamp(20), "stoppedAt": stamp(19)},
                {"id": "missed-3", "status": "success", "startedAt": stamp(10), "stoppedAt": stamp(9)},
                {"id": "junk", "status": "weird", "startedAt": stamp(5)},
            ],
            "wf-not-registered": [{"id": "x", "status": "success", "startedAt": stamp(5)}],
        }
    )
    await db_session.flush()
    added = await svc.sync_from_n8n(db_session, fake)
    assert added == 2 and fake.asked.count("wf-not-registered") == 0
    runs = {r.n8n_execution_id: r for r in await _runs(db_session, auto["id"])}
    assert set(runs) == {"pushed-1", "missed-2", "missed-3"}
    assert runs["pushed-1"].status == "success" and "rich" in (runs["pushed-1"].output_summary or "")  # push wins
    assert runs["missed-2"].status == "failed" and runs["missed-2"].error_summary
    total = (
        await db_session.execute(select(Automation).where(Automation.n8n_workflow_id == "wf-not-registered"))
    ).all()
    assert total == []


async def test_n8n_client_lists_executions_without_data() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "1", "status": "success"}], "nextCursor": None})

    client = N8nClient("http://n8n:5678", "key", transport=httpx.MockTransport(handler))
    out = await client.list_executions("wf1", 5)
    assert out == [{"id": "1", "status": "success"}]
    assert seen[0].url.params["workflowId"] == "wf1" and seen[0].url.params["includeData"] == "false"


def test_n8n_connection_errors_read_as_unreachable() -> None:
    """The text n8n's HTTP node gives for a host that is down (seen on the live sample workflow)."""
    from app.modules.automations import sanitize

    raw = "The connection cannot be established, this usually occurs due to an incorrect host(domain) value"
    assert sanitize.friendly_error(raw) == "A connected service could not be reached."
