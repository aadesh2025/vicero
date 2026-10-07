"""R2: n8n callback binding and tool ownership.

One n8n serves every org and one shared HMAC secret signs every callback, so on its own that secret cannot
stop one workflow from resolving another org's tool run. These tests pin the controls added on top:
a per-call token bound to (run, org), pending-only / single-use / 10-minute callbacks, one org per webhook
path, a locked n8n tool target, a destination that is always Vicero's own n8n, and an ownership re-check.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.core.config import settings
from app.integrations.n8n_client import N8nClient, callback_token, sign
from app.llm.types import ToolCall
from app.models import Tool, ToolRun
from app.tools import service as tools_service
from app.tools.base import ToolContext

SECRET_MARKER = "never-in-a-response"


async def _org(client: AsyncClient, email: str) -> tuple[dict[str, str], uuid.UUID]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": f"Org {email}"}, headers={"Authorization": f"Bearer {token}"})
    org_id = uuid.UUID(org.json()["id"])
    return {"Authorization": f"Bearer {token}", "X-Org-Id": str(org_id)}, org_id


async def _pending_run(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    tool_type: str = "n8n",
    age: dt.timedelta = dt.timedelta(0),
    webhook: str | None = None,
) -> ToolRun:
    tool = Tool(
        organization_id=org_id,
        name=f"t_{uuid.uuid4().hex[:8]}",
        type=tool_type,
        enabled=True,
        config={"webhook_url": webhook or f"http://n8n/webhook/{uuid.uuid4().hex}", "mode": "async"},
        input_schema={},
    )
    db.add(tool)
    await db.flush()
    run = ToolRun(
        organization_id=org_id,
        tool_id=tool.id,
        input={},
        status="pending",
        created_at=dt.datetime.now(tz=dt.UTC) - age,
    )
    db.add(run)
    await db.flush()
    return run


async def _callback(
    client: AsyncClient,
    run_id: uuid.UUID,
    token: str | None,
    *,
    output: dict[str, object] | None = None,
    sign_ok: bool = True,
):
    payload: dict[str, object] = {"run_id": str(run_id), "output": output or {"done": True}, "status": "success"}
    if token is not None:
        payload["callback_token"] = token
    body = json.dumps(payload).encode()
    ts, sig = sign(body)
    return await client.post(
        "/v1/tools/n8n/callback",
        content=body,
        headers={
            "X-Vicero-Signature": sig if sign_ok else "0" * 64,
            "X-Vicero-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )


# ── callback binding ──────────────────────────────────────────────────────────────
async def test_callback_with_the_right_token_resolves_the_run(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb1@example.com")
    run = await _pending_run(db_session, org_id)
    resp = await _callback(client, run.id, callback_token(run.id, org_id))
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    await db_session.flush()
    await db_session.refresh(run)
    assert run.status == "success" and run.output == {"done": True}


async def test_callback_without_a_token_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb2@example.com")
    run = await _pending_run(db_session, org_id)
    resp = await _callback(client, run.id, None)
    assert resp.status_code == 422
    await db_session.flush()
    await db_session.refresh(run)
    assert run.status == "pending"


async def test_another_orgs_token_cannot_resolve_my_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """Org B's workflow holds a validly signed token for ITS run; it must be useless against org A's run."""
    _, org_a = await _org(client, "cb3a@example.com")
    _, org_b = await _org(client, "cb3b@example.com")
    run_a = await _pending_run(db_session, org_a)
    run_b = await _pending_run(db_session, org_b)

    forged = [
        callback_token(run_b.id, org_b),  # org B's own token for org B's run
        callback_token(run_a.id, org_b),  # right run, wrong org
        callback_token(run_b.id, org_a),  # right org, wrong run
    ]
    for token in forged:
        resp = await _callback(client, run_a.id, token, output={"owned": True})
        assert resp.status_code == 404, token
        assert resp.json()["error"]["code"] == "n8n.callback_rejected"
    await db_session.flush()
    await db_session.refresh(run_a)
    assert run_a.status == "pending" and not run_a.output


async def test_a_run_resolves_only_once(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb4@example.com")
    run = await _pending_run(db_session, org_id)
    token = callback_token(run.id, org_id)
    assert (await _callback(client, run.id, token, output={"first": True})).status_code == 200
    replay = await _callback(client, run.id, token, output={"second": True})
    assert replay.status_code == 404
    await db_session.flush()
    await db_session.refresh(run)
    assert run.output == {"first": True}


async def test_a_stale_run_cannot_be_resolved(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb5@example.com")
    run = await _pending_run(db_session, org_id, age=dt.timedelta(minutes=11))
    assert (await _callback(client, run.id, callback_token(run.id, org_id))).status_code == 404
    await db_session.flush()
    await db_session.refresh(run)
    assert run.status == "pending"


async def test_an_oversized_output_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb6@example.com")
    run = await _pending_run(db_session, org_id)
    big = {"blob": "x" * (70 * 1024)}
    assert (await _callback(client, run.id, callback_token(run.id, org_id), output=big)).status_code == 404


async def test_only_n8n_runs_can_be_resolved_by_callback(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb7@example.com")
    run = await _pending_run(db_session, org_id, tool_type="http")
    assert (await _callback(client, run.id, callback_token(run.id, org_id))).status_code == 404


async def test_unknown_run_gives_the_same_answer_as_a_bad_token(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb8@example.com")
    run = await _pending_run(db_session, org_id)
    unknown = await _callback(client, uuid.uuid4(), "a" * 32)
    wrong = await _callback(client, run.id, "a" * 32)
    assert unknown.status_code == wrong.status_code == 404
    assert unknown.json() == wrong.json()  # nothing reveals which run ids exist


async def test_a_bad_transport_signature_is_still_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org(client, "cb9@example.com")
    run = await _pending_run(db_session, org_id)
    resp = await _callback(client, run.id, callback_token(run.id, org_id), sign_ok=False)
    assert resp.status_code == 401


async def test_rejections_leave_a_security_log_without_secrets(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_a = await _org(client, "cb10a@example.com")
    _, org_b = await _org(client, "cb10b@example.com")
    run_a = await _pending_run(db_session, org_a)
    token_b = callback_token(uuid.uuid4(), org_b)
    with capture_logs() as logs:
        await _callback(client, run_a.id, token_b)
    events = [e for e in logs if e["event"] == "n8n_callback_rejected"]
    assert len(events) == 1 and events[0]["reason"] == "bad_token" and events[0]["run_id"] == str(run_a.id)
    assert token_b not in str(logs)


# ── one org per webhook path; locked target ──────────────────────────────────────────
async def _bind(client: AsyncClient, headers: dict[str, str], path: str, name: str = "wf"):
    return await client.post(
        "/v1/tools/n8n/bind",
        json={"name": name, "webhook_url": f"http://n8n/webhook/{path}", "mode": "sync"},
        headers=headers,
    )


async def test_two_orgs_cannot_bind_the_same_webhook(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "n8n_require_signature_check", False)
    headers_a, _ = await _org(client, "bind-a@example.com")
    headers_b, _ = await _org(client, "bind-b@example.com")
    assert (await _bind(client, headers_a, "shared-flow")).status_code == 201
    refused = await _bind(client, headers_b, "shared-flow")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "tools.n8n_forbidden"
    # The same org may bind its own webhook again (e.g. a second agent), and other paths are unaffected.
    assert (await _bind(client, headers_a, "shared-flow", name="again")).status_code == 201
    assert (await _bind(client, headers_b, "b-own-flow")).status_code == 201


async def test_an_n8n_tools_target_cannot_be_rewritten(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "n8n_require_signature_check", False)
    headers, _ = await _org(client, "lock@example.com")
    tool = (await _bind(client, headers, "mine")).json()

    for evil in (
        {"webhook_url": "http://169.254.169.254/latest/meta-data/"},
        {"webhook_url": "http://n8n/webhook/someone-elses"},
        {"workflow_id": "999"},
    ):
        resp = await client.patch(f"/v1/tools/{tool['id']}", json={"config": evil}, headers=headers)
        assert resp.status_code == 400, evil
        assert resp.json()["error"]["code"] == "tools.n8n_config_locked"

    bogus = await client.patch(f"/v1/tools/{tool['id']}", json={"config": {"mode": "bogus"}}, headers=headers)
    assert bogus.status_code == 400
    ok = await client.patch(f"/v1/tools/{tool['id']}", json={"config": {"mode": "async"}}, headers=headers)
    assert ok.status_code == 200 and ok.json()["config"]["webhook_url"] == "http://n8n/webhook/mine"
    assert ok.json()["config"]["mode"] == "async"


async def test_webhook_calls_always_go_to_vicero_own_n8n() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(200, json={"ok": True})

    client = N8nClient("http://n8n:5678", "k", transport=httpx.MockTransport(handler))
    await client.trigger_webhook("https://evil.example.net/webhook/abc?x=1", {"a": 1})
    assert seen[0].url.host == "n8n" and seen[0].url.path == "/webhook/abc"

    for bad in ("http://169.254.169.254/latest/meta-data/", "http://n8n:5678/rest/login", "http://n8n/webhook-test/x"):
        with pytest.raises(Exception, match="production /webhook/"):
            await client.trigger_webhook(bad, {})
    assert len(seen) == 1


# ── ownership re-check at execution ───────────────────────────────────────────────
def _ctx(db: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID) -> ToolContext:
    return ToolContext(session=db, org_id=org_id, agent_id=agent_id, version=None)  # type: ignore[arg-type]


async def test_a_foreign_tool_is_never_executed(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_a = await _org(client, "own-a@example.com")
    _, org_b = await _org(client, "own-b@example.com")
    agent_a, other_agent = uuid.uuid4(), uuid.uuid4()
    foreign_org = Tool(organization_id=org_b, agent_id=None, name="x", type="n8n", config={}, input_schema={})
    other_agent_tool = Tool(
        organization_id=org_a, agent_id=other_agent, name="y", type="n8n", config={}, input_schema={}
    )
    for tool in (foreign_org, other_agent_tool):
        tool.id = uuid.uuid4()
        with capture_logs() as logs:
            result = await tools_service.execute_tool_call(
                db_session,
                _ctx(db_session, org_a, agent_a),
                {tool.name: tool},
                ToolCall(id="c", name=tool.name, arguments={}),
            )
        assert result.status == "error" and "not available" in (result.error or "")
        assert any(e["event"] == "tool_ownership_violation" for e in logs)


async def test_a_webhook_now_owned_by_another_org_is_not_executed(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy rows: two orgs already point at one path. Neither runs it until an operator sorts it out."""
    _, org_a = await _org(client, "legacy-a@example.com")
    _, org_b = await _org(client, "legacy-b@example.com")
    shared = "http://n8n/webhook/legacy-shared"
    await _pending_run(db_session, org_b, webhook=shared)  # org B's tool on the path
    tool_a = Tool(
        organization_id=org_a, name="a", type="n8n", config={"webhook_url": shared, "mode": "sync"}, input_schema={}
    )
    tool_a.id = uuid.uuid4()

    called = False

    async def _boom(*_a: object, **_k: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(tools_service, "_dispatch", _boom)
    result = await tools_service.execute_tool_call(
        db_session, _ctx(db_session, org_a, uuid.uuid4()), {"a": tool_a}, ToolCall(id="c", name="a", arguments={})
    )
    assert result.status == "error" and not called
