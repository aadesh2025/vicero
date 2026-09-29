"""Phase 10 tests: n8n client signing, workflow parsing, the n8n tool, and the callback."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.integrations.n8n_client import N8nClient, sign, verify_callback
from app.llm.fake import ScriptedToolProvider
from app.llm.types import ToolCall
from app.models import AgentVersion
from app.tools import n8n_tool
from app.tools.base import ToolContext


def _mock(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


# ── Signing / verification ────────────────────────────────────────────────────────
def test_sign_and_verify_roundtrip() -> None:
    body = b'{"hello":"world"}'
    ts, sig = sign(body)
    assert verify_callback(sig, ts, body) is True
    assert verify_callback("deadbeef", ts, body) is False
    assert verify_callback(sig, ts, b'{"tampered":true}') is False


def test_verify_rejects_stale_timestamp() -> None:
    body = b"{}"
    ts, sig = sign(body, timestamp="1000000000")  # far in the past
    assert verify_callback(sig, ts, body) is False


# ── Client ─────────────────────────────────────────────────────────────────────────
async def test_list_workflows_parses_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-n8n-api-key") == "k"
        return httpx.Response(200, json={"data": [{"id": "1", "name": "WF", "active": True}]})

    client = N8nClient("http://n8n", "k", transport=_mock(handler))
    workflows = await client.list_workflows()
    assert workflows[0]["name"] == "WF"


def test_extract_webhook_url() -> None:
    client = N8nClient("http://n8n:5678", "k")
    wf = {"nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "botforge-echo"}}]}
    assert client.extract_webhook_url(wf) == "http://n8n:5678/webhook/botforge-echo"
    assert client.extract_webhook_url({"nodes": []}) is None


def test_extract_tags_handles_dict_and_string_entries() -> None:
    assert N8nClient.extract_tags({"tags": [{"name": "Acme"}, "Beta"]}) == {"acme", "beta"}
    assert N8nClient.extract_tags({"tags": []}) == set()
    assert N8nClient.extract_tags({}) == set()


async def test_trigger_webhook_signs_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sig"] = request.headers.get("x-botforge-signature")
        captured["ts"] = request.headers.get("x-botforge-timestamp")
        captured["body"] = request.content
        return httpx.Response(200, json={"result": "pong"})

    client = N8nClient("http://n8n", "k", transport=_mock(handler))
    status, data = await client.trigger_webhook("http://n8n/webhook/x", {"a": 1})
    assert status == 200 and data["result"] == "pong"
    assert verify_callback(captured["sig"], captured["ts"], captured["body"]) is True


# ── Multi-tenant workflow visibility (client A must not see client B's automations,
#    and no client should ever see a platform-internal workflow) ────────────────────
def test_workflow_visible_to_org_scopes_by_tag() -> None:
    from app.tools.service import workflow_visible_to_org

    # tagged for a specific org — only that org sees it
    assert workflow_visible_to_org({"acme"}, "Booking", "acme") is True
    assert workflow_visible_to_org({"acme"}, "Booking", "widgetco") is False
    # explicitly internal — hidden from every org regardless of tag
    assert workflow_visible_to_org({"internal"}, "Anything", "acme") is False
    # legacy "SHARED —" naming convention is treated as internal even if untagged
    assert workflow_visible_to_org(set(), "SHARED — Master Router", "acme") is False


def test_untagged_workflow_is_hidden_from_every_org() -> None:
    """Deny-by-default: the old permissive rule showed a new, empty org every other
    client's automations, because untagged is the state every workflow starts in."""
    from app.tools.service import workflow_visible_to_org

    assert workflow_visible_to_org(set(), "Website Lead — Contact Form", "widgetco") is False
    assert workflow_visible_to_org(set(), "Website Lead — Contact Form", "acme") is False
    # A tag that belongs to nobody doesn't leak either.
    assert workflow_visible_to_org({"misc"}, "Somebody's Workflow", "acme") is False


def test_shared_template_is_visible_to_every_org() -> None:
    """The one deliberate escape hatch — opt-in, never a default."""
    from app.tools.service import workflow_visible_to_org

    assert workflow_visible_to_org({"shared-template"}, "Starter Automation", "acme") is True
    assert workflow_visible_to_org({"shared-template"}, "Starter Automation", "widgetco") is True
    # Internal wins over it, so mislabelling something both ways still fails closed.
    assert workflow_visible_to_org({"shared-template", "internal"}, "Provisioner", "acme") is False


def test_visibility_never_depends_on_an_empty_slug() -> None:
    """An org with a blank slug must not match a workflow tagged with the empty string."""
    from app.tools.service import workflow_visible_to_org

    assert workflow_visible_to_org({""}, "Anything", "") is False
    assert workflow_visible_to_org(set(), "Anything", "") is False


async def test_list_n8n_workflows_hides_internal_and_other_orgs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools import service

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "1", "name": "SHARED — Master Router", "active": True, "tags": []},
                    {"id": "2", "name": "Acme — Booking", "active": True, "tags": [{"name": "acme"}]},
                    {"id": "3", "name": "WidgetCo — Booking", "active": True, "tags": [{"name": "widgetco"}]},
                    {"id": "4", "name": "Website Lead — Contact Form", "active": True, "tags": []},
                    {"id": "5", "name": "Starter Automation", "active": True, "tags": [{"name": "shared-template"}]},
                ]
            },
        )

    monkeypatch.setattr(service, "get_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler)))

    class _Org:
        slug = "acme"
        plan = "legacy"  # the n8n gate reads the org's plan (docs/18)
        trial_ends_at = None
        plan_expires_at = None

    class _Ctx:
        role = "owner"
        org = _Org()

    result = await service.list_n8n_workflows(None, _Ctx())  # type: ignore[arg-type]
    names = {w.name for w in result}
    # The untagged "Website Lead" workflow is now hidden — that reversal is the point.
    assert names == {"Acme — Booking", "Starter Automation"}


async def test_bind_by_id_rejects_workflow_from_another_org(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import AppError
    from app.tools import schemas, service

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/workflows/3"
        return httpx.Response(
            200, json={"id": "3", "name": "WidgetCo — Booking", "tags": [{"name": "widgetco"}], "nodes": []}
        )

    monkeypatch.setattr(service, "get_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler)))

    class _Org:
        slug = "acme"
        plan = "legacy"  # the n8n gate reads the org's plan (docs/18)
        trial_ends_at = None
        plan_expires_at = None

    class _Ctx:
        role = "owner"
        org = _Org()

    with pytest.raises(AppError) as exc_info:
        await service.bind_n8n_workflow(
            None,  # type: ignore[arg-type]
            _Ctx(),  # type: ignore[arg-type]
            schemas.BindN8nRequest(name="steal_booking", workflow_id="3"),
        )
    assert exc_info.value.code == "tools.n8n_forbidden"


# ── n8n tool execution ──────────────────────────────────────────────────────────────
def _n8n_ctx() -> ToolContext:
    return ToolContext(
        session=None,  # type: ignore[arg-type]
        org_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        version=AgentVersion(agent_id=uuid.uuid4(), version=1),
        run_id=uuid.uuid4(),
    )


async def test_n8n_tool_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ticket_id": 42})

    monkeypatch.setattr(n8n_tool, "get_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler)))
    res = await n8n_tool.execute_n8n_tool(
        {"webhook_url": "http://n8n/webhook/x", "mode": "sync"}, {"q": "hi"}, _n8n_ctx()
    )
    assert res.status == "success"
    assert res.output["response"]["ticket_id"] == 42


async def test_n8n_tool_async_returns_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    monkeypatch.setattr(n8n_tool, "get_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler)))
    res = await n8n_tool.execute_n8n_tool(
        {"webhook_url": "http://n8n/webhook/x", "mode": "async"}, {}, _n8n_ctx()
    )
    assert res.status == "pending"
    assert res.output["status"] == "accepted" and res.output["run_id"]


# ── API: bind + agent uses tool + callback ──────────────────────────────────────────
async def _headers(client: AsyncClient, email: str = "n8n@example.com") -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "N8nOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def test_bind_and_agent_calls_n8n_tool(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests tool execution, not bind gating (tests/test_n8n_signature_gate.py does that).
    monkeypatch.setattr(settings, "n8n_require_signature_check", False)
    from app.modules.conversations import service as chat_service

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"weather": "sunny", "temp_c": 21})

    monkeypatch.setattr(n8n_tool, "get_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler)))

    async def _provider(*_a: object, **_k: object) -> ScriptedToolProvider:
        return ScriptedToolProvider(
            ToolCall(id="c1", name="get_weather", arguments={"city": "SF"}), answer="it is sunny"
        )

    monkeypatch.setattr(chat_service, "_resolve_provider", lambda *a, **k: _provider())

    headers = await _headers(client)
    agent = await client.post("/v1/agents", json={"name": "WeatherBot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"features": {"tools_enabled": True, "memory_enabled": True, "handoff_enabled": False}},
        headers=headers,
    )
    bind = await client.post(
        "/v1/tools/n8n/bind",
        json={"name": "get_weather", "webhook_url": "http://n8n/webhook/weather", "mode": "sync", "agent_id": aid},
        headers=headers,
    )
    assert bind.status_code == 201, bind.text
    assert bind.json()["type"] == "n8n"

    resp = await client.post(
        f"/v1/agents/{aid}/chat", json={"message": "weather in SF?", "stream": False}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["content"] == "it is sunny"
    assert data["tool_runs"][0]["output"]["response"]["weather"] == "sunny"

    runs = await client.get("/v1/tools/runs", headers=headers)
    assert runs.json()[0]["status"] == "success"


async def test_n8n_callback_resolves_pending_run(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "n8n_require_signature_check", False)
    from app.modules.conversations import service as chat_service

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    monkeypatch.setattr(n8n_tool, "get_client", lambda **_k: N8nClient("http://n8n", "k", transport=_mock(handler)))

    async def _provider(*_a: object, **_k: object) -> ScriptedToolProvider:
        return ScriptedToolProvider(ToolCall(id="c1", name="long_job", arguments={}), answer="working on it")

    monkeypatch.setattr(chat_service, "_resolve_provider", lambda *a, **k: _provider())

    headers = await _headers(client, "n8n2@example.com")
    agent = await client.post("/v1/agents", json={"name": "JobBot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"features": {"tools_enabled": True, "memory_enabled": True, "handoff_enabled": False}},
        headers=headers,
    )
    await client.post(
        "/v1/tools/n8n/bind",
        json={"name": "long_job", "webhook_url": "http://n8n/webhook/job", "mode": "async", "agent_id": aid},
        headers=headers,
    )
    resp = await client.post(f"/v1/agents/{aid}/chat", json={"message": "start", "stream": False}, headers=headers)
    run_id = resp.json()["tool_runs"][0]["output"]["run_id"]

    # n8n calls back later with a signed payload → resolves the pending run.
    body = json.dumps({"run_id": run_id, "output": {"done": True}, "status": "success"}).encode()
    ts, sig = sign(body)
    cb = await client.post(
        "/v1/tools/n8n/callback",
        content=body,
        headers={"X-BotForge-Signature": sig, "X-BotForge-Timestamp": ts, "Content-Type": "application/json"},
    )
    assert cb.status_code == 200 and cb.json()["ok"] is True

    runs = await client.get("/v1/tools/runs", headers=headers)
    resolved = next(r for r in runs.json() if r["id"] == run_id)
    assert resolved["status"] == "success"
    assert resolved["output"]["done"] is True


# ── Argument/schema tolerance (blank-reply fix) ───────────────────────────────
def test_relax_n8n_schema_drops_type_on_args() -> None:
    """A legacy `args: {"type": "object"}` made providers reject a tool call whose args
    came back as a string, which surfaced to the end user as an empty reply."""
    legacy = {
        "type": "object",
        "properties": {"args": {"type": "object", "description": "arguments passed to the workflow"}},
    }
    relaxed = n8n_tool.relax_n8n_schema(legacy)
    assert "type" not in relaxed["properties"]["args"]
    assert relaxed["properties"]["args"]["description"]
    assert relaxed["type"] == "object"
    # A freshly generated schema is already tolerant.
    assert "type" not in n8n_tool.n8n_args_schema()["properties"]["args"]


def test_relax_n8n_schema_passes_through_custom_schemas() -> None:
    custom = {"type": "object", "properties": {"ticket_id": {"type": "string"}}}
    assert n8n_tool.relax_n8n_schema(custom) == custom
    assert n8n_tool.relax_n8n_schema(None) == n8n_tool.n8n_args_schema()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"args": {"customer": "Acme"}}, {"customer": "Acme"}),      # documented wrapper
        ({"args": '{"customer": "Acme"}'}, {"customer": "Acme"}),    # JSON-in-a-string
        ({"args": "just text"}, {"input": "just text"}),             # bare string
        ({"customer": "Acme"}, {"customer": "Acme"}),                # already flat
        ({"args": None}, {"input": None}),
        ("not a dict", {"input": "not a dict"}),
    ],
)
def test_normalize_n8n_args(raw: object, expected: dict[str, object]) -> None:
    """Every shape a model produces must reach the workflow identically — and must match
    what the tool `/test` endpoint sends (previously the wrapper double-nested as
    {"args": {"args": ...}})."""
    assert n8n_tool.normalize_n8n_args(raw) == expected
