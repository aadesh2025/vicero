"""API tool v2 executor (docs/28 T1.4): templating, encoding, selectors, retries, auth, secret hygiene."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from app.tools import api_tool
from app.tools.api_tool import execute_api_tool, parse_selector, pick, validate_args, validate_selectors
from app.tools.connections import ResolvedConnection

SECRET = "sk-live-9f8e7d6c5b4a3210"


def _conn(auth_type: str = "bearer", secrets: dict[str, Any] | None = None, **kw: Any) -> ResolvedConnection:
    base = {
        "id": uuid.uuid4(),
        "name": "crm",
        "base_url": "https://api.example.com/v1",
        "auth_type": auth_type,
        "secrets": secrets if secrets is not None else {"token": SECRET},
        "default_headers": {},
        "allowed_hosts": ["api.example.com"],
        "timeout_s": 5,
    }
    base.update(kw)
    return ResolvedConnection(**base)


class Recorder:
    def __init__(self, response: httpx.Response | list[httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = (
            response if isinstance(response, list) else [response or httpx.Response(200, json={"ok": True})]
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[idx]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


# ── Templating and encoding ──────────────────────────────────────────────────────
async def test_path_param_is_url_encoded_so_it_cannot_traverse(dns: dict) -> None:
    rec = Recorder()
    res = await execute_api_tool(
        {"method": "GET", "path": "/orders/{{id}}"},
        {"id": "../admin?x=1&y=2"},
        connection=_conn(),
        transport=rec.transport,
    )
    assert res.status == "success"
    assert rec.requests[0].url.raw_path == b"/v1/orders/..%2Fadmin%3Fx%3D1%26y%3D2"


async def test_query_params_are_encoded_and_optional_ones_dropped(dns: dict) -> None:
    rec = Recorder()
    cfg = {"method": "GET", "path": "/search", "query": {"q": "{{q}}", "page": "{{page}}", "fixed": "a b"}}
    await execute_api_tool(cfg, {"q": "x&admin=true"}, connection=_conn(), transport=rec.transport)
    url = rec.requests[0].url
    assert url.params["q"] == "x&admin=true" and "admin" not in url.params
    assert "page" not in url.params
    assert url.params["fixed"] == "a b"


async def test_header_values_lose_crlf(dns: dict) -> None:
    rec = Recorder()
    cfg = {"method": "GET", "path": "/x", "headers": {"X-Ref": "{{ref}}"}}
    await execute_api_tool(cfg, {"ref": "a\r\nX-Injected: yes"}, connection=_conn(), transport=rec.transport)
    assert "x-injected" not in rec.requests[0].headers
    assert rec.requests[0].headers["x-ref"] == "aX-Injected: yes"


async def test_json_body_keeps_native_types_for_exact_placeholders(dns: dict) -> None:
    rec = Recorder()
    cfg = {"method": "POST", "path": "/o", "body": {"qty": "{{n}}", "note": "n={{n}}", "tags": ["{{t}}"]}}
    await execute_api_tool(cfg, {"n": 3, "t": "vip"}, connection=_conn(), transport=rec.transport)
    assert json.loads(rec.requests[0].content) == {"qty": 3, "note": "n=3", "tags": ["vip"]}


async def test_legacy_http_renders_strings_only(dns: dict) -> None:
    rec = Recorder()
    cfg = {"method": "POST", "url": "https://api.example.com/o", "body": {"qty": "{{n}}"}}
    await execute_api_tool(cfg, {"n": 3}, legacy=True, transport=rec.transport)
    assert json.loads(rec.requests[0].content) == {"qty": "3"}


async def test_form_and_raw_bodies(dns: dict) -> None:
    rec = Recorder()
    await execute_api_tool(
        {"method": "POST", "path": "/f", "body_type": "form", "body": {"a": "{{a}}"}},
        {"a": "1 2"},
        connection=_conn(),
        transport=rec.transport,
    )
    assert rec.requests[0].content == b"a=1+2"
    assert rec.requests[0].headers["content-type"].startswith("application/x-www-form-urlencoded")
    rec2 = Recorder()
    await execute_api_tool(
        {"method": "POST", "path": "/r", "body_type": "raw", "body": "hello {{a}}"},
        {"a": "world"},
        connection=_conn(),
        transport=rec2.transport,
    )
    assert rec2.requests[0].content == b"hello world"


async def test_vars_placeholders(dns: dict) -> None:
    rec = Recorder()
    cfg = {"method": "GET", "path": "/c/{{vars.customer}}", "headers": {"X-Who": "{{vars.who}}"}}
    await execute_api_tool(
        cfg, {}, connection=_conn(), variables={"customer": "c 9", "who": "bot"}, transport=rec.transport
    )
    assert rec.requests[0].url.raw_path == b"/v1/c/c%209"
    assert rec.requests[0].headers["x-who"] == "bot"


async def test_host_cannot_be_templated(dns: dict) -> None:
    rec = Recorder()
    res = await execute_api_tool(
        {"method": "GET", "url": "https://{{host}}/x"}, {"host": "evil.example"}, transport=rec.transport
    )
    assert res.status == "error" and rec.requests == []
    res = await execute_api_tool(
        {"method": "GET", "url": "{{u}}"}, {"u": "https://evil.example"}, transport=rec.transport
    )
    assert res.status == "error" and rec.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        "' OR 1=1 --",
        "{{vars.secret}}",
        "\r\nHost: evil",
        "../../../etc/passwd",
        "%00",
        "a" * 5000,
        "😀 ünï",
        "https://evil.example/",
        "\\u0000",
    ],
)
async def test_hostile_arguments_never_change_the_destination(payload: str, dns: dict) -> None:
    rec = Recorder()
    cfg = {"method": "GET", "path": "/items/{{v}}", "query": {"q": "{{v}}"}, "headers": {"X-V": "{{v}}"}}
    res = await execute_api_tool(cfg, {"v": payload}, connection=_conn(), transport=rec.transport)
    assert res.status == "success"
    req = rec.requests[0]
    assert req.headers["host"] == "api.example.com"
    assert req.url.raw_path.startswith(b"/v1/items/")
    assert b"/../" not in req.url.raw_path
    assert "\r" not in req.headers["x-v"] and "\n" not in req.headers["x-v"]


# ── Argument validation ──────────────────────────────────────────────────────────
SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "qty": {"type": "integer", "minimum": 1}, "kind": {"enum": ["a", "b"]}},
    "required": ["id"],
}


async def test_invalid_arguments_return_a_structured_error_and_make_no_call(dns: dict) -> None:
    rec = Recorder()
    res = await execute_api_tool(
        {"method": "GET", "path": "/x"},
        {"qty": 0, "kind": "z"},
        connection=_conn(),
        input_schema=SCHEMA,
        transport=rec.transport,
    )
    assert res.status == "error" and res.output["error"] == "invalid_arguments"
    assert len(res.output["details"]) == 3 and rec.requests == []


def test_validate_args_valid_and_empty_schema() -> None:
    assert validate_args(SCHEMA, {"id": "1", "qty": 2}) == []
    assert validate_args({}, {"anything": 1}) == []
    assert validate_args(None, {}) == []


# ── Selectors ────────────────────────────────────────────────────────────────────
DATA = {"data": {"items": [{"price": 5, "tags": ["a", "b"]}, {"price": 7}], "name": "x"}, "n": 1}


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("$.n", 1),
        ("$.data.name", "x"),
        ("$.data.items[0].price", 5),
        ("$.data.items[1].price", 7),
        ("$.data.items[*].price", [5, 7]),
        ("$.data.items[0].tags[1]", "b"),
        ("$['data'].name", "x"),
        ("$.data.items[9].price", None),
        ("$.nope.deeper", None),
    ],
)
def test_pick(selector: str, expected: Any) -> None:
    out = pick(DATA, [selector])
    assert list(out.values()) == [expected]


@pytest.mark.parametrize("bad", ["data.x", "$.a..b", "$.a[", "$.a[x]", "$(import os)", "$.__class__()", "", "$.a b"])
def test_malformed_selectors_are_rejected(bad: str) -> None:
    assert parse_selector(bad) is None
    assert validate_selectors([bad]) is not None


def test_selectors_never_evaluate_code() -> None:
    assert pick({"a": 1}, ["$.a; __import__('os').system('x')"]) == {}


async def test_response_pick_limits_what_the_model_sees(dns: dict) -> None:
    rec = Recorder(httpx.Response(200, json={"data": {"price": 9, "internal": "do-not-show"}, "ssn": "1"}))
    cfg = {"method": "GET", "path": "/p", "response": {"pick": ["$.data.price"]}}
    res = await execute_api_tool(cfg, {}, connection=_conn(), transport=rec.transport)
    assert res.output == {"status_code": 200, "data": {"data.price": 9}}


async def test_long_responses_are_capped(dns: dict) -> None:
    rec = Recorder(httpx.Response(200, json={"blob": "x" * 50_000}))
    res = await execute_api_tool(
        {"method": "GET", "path": "/b", "response": {"max_chars": 500}}, {}, connection=_conn(), transport=rec.transport
    )
    assert res.output["truncated"] is True and len(res.output["body"]) == 500


async def test_http_error_status_is_an_error_with_the_body(dns: dict) -> None:
    rec = Recorder(httpx.Response(404, json={"message": "no such order"}))
    res = await execute_api_tool({"method": "GET", "path": "/o/1"}, {}, connection=_conn(), transport=rec.transport)
    assert res.status == "error" and res.error == "HTTP 404"
    assert res.output["body"] == {"message": "no such order"}


# ── Retries ──────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_s: float) -> None:
        return None

    monkeypatch.setattr(api_tool.asyncio, "sleep", instant)


async def test_get_is_retried_on_503_then_succeeds(dns: dict) -> None:
    rec = Recorder([httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"ok": 1})])
    cfg = {"method": "GET", "path": "/x", "retry": {"max": 3, "backoff_ms": 10}}
    res = await execute_api_tool(cfg, {}, connection=_conn(), transport=rec.transport)
    assert res.status == "success" and res.attempt == 3 and len(rec.requests) == 3


async def test_post_without_idempotency_key_is_never_retried(dns: dict) -> None:
    rec = Recorder([httpx.Response(503), httpx.Response(200, json={})])
    cfg = {"method": "POST", "path": "/pay", "body": {}, "retry": {"max": 3}}
    res = await execute_api_tool(cfg, {}, connection=_conn(), transport=rec.transport)
    assert res.status == "error" and len(rec.requests) == 1


async def test_post_with_idempotency_key_is_retried(dns: dict) -> None:
    rec = Recorder([httpx.Response(502), httpx.Response(200, json={})])
    cfg = {"method": "POST", "path": "/pay", "body": {}, "headers": {"Idempotency-Key": "{{k}}"}, "retry": {"max": 2}}
    res = await execute_api_tool(cfg, {"k": "abc"}, connection=_conn(), transport=rec.transport)
    assert res.status == "success" and len(rec.requests) == 2


async def test_4xx_is_not_retried_and_retries_are_capped_at_three(dns: dict) -> None:
    rec = Recorder([httpx.Response(400)])
    await execute_api_tool(
        {"method": "GET", "path": "/x", "retry": {"max": 3}}, {}, connection=_conn(), transport=rec.transport
    )
    assert len(rec.requests) == 1
    rec2 = Recorder([httpx.Response(503)])
    res = await execute_api_tool(
        {"method": "GET", "path": "/x", "retry": {"max": 99}}, {}, connection=_conn(), transport=rec2.transport
    )
    assert len(rec2.requests) == 4 and res.status == "error"


async def test_network_error_is_retried_for_get(dns: dict) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={})

    res = await execute_api_tool(
        {"method": "GET", "path": "/x", "retry": {"max": 1}},
        {},
        connection=_conn(),
        transport=httpx.MockTransport(handler),
    )
    assert res.status == "success" and len(calls) == 2


async def test_timeout_status(dns: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    res = await execute_api_tool(
        {"method": "GET", "path": "/x"}, {}, connection=_conn(), transport=httpx.MockTransport(handler)
    )
    assert res.status == "timeout"


# ── Auth and secret hygiene ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("auth_type", "secrets", "check"),
    [
        ("bearer", {"token": SECRET}, lambda r: r.headers["authorization"] == f"Bearer {SECRET}"),
        ("api_key", {"name": "X-Key", "in": "header", "value": SECRET}, lambda r: r.headers["x-key"] == SECRET),
        ("api_key", {"name": "key", "in": "query", "value": SECRET}, lambda r: r.url.params["key"] == SECRET),
        ("basic", {"username": "u", "password": SECRET}, lambda r: r.headers["authorization"].startswith("Basic ")),
        ("custom_headers", {"headers": {"X-Tenant": SECRET}}, lambda r: r.headers["x-tenant"] == SECRET),
    ],
)
async def test_auth_is_injected(auth_type: str, secrets: dict, check: Any, dns: dict) -> None:
    rec = Recorder()
    await execute_api_tool(
        {"method": "GET", "path": "/x"}, {}, connection=_conn(auth_type, secrets), transport=rec.transport
    )
    assert check(rec.requests[0])


async def test_a_tool_cannot_shadow_the_connection_auth_header(dns: dict) -> None:
    rec = Recorder()
    cfg = {"method": "GET", "path": "/x", "headers": {"Authorization": "Bearer attacker"}}
    await execute_api_tool(cfg, {}, connection=_conn(), transport=rec.transport)
    assert rec.requests[0].headers["authorization"] == f"Bearer {SECRET}"


async def test_secret_never_appears_in_output_error_or_sanitized_request(dns: dict) -> None:
    # The server echoes the credential back (headers, body, error text) - all of it must be scrubbed.
    rec = Recorder(httpx.Response(401, json={"error": f"bad token {SECRET}", "echo": {"auth": f"Bearer {SECRET}"}}))
    cfg = {"method": "POST", "path": "/x", "body": {"k": SECRET}, "headers": {"X-Debug": SECRET}}
    res = await execute_api_tool(cfg, {}, connection=_conn(), transport=rec.transport)
    blob = json.dumps({"o": res.output, "e": res.error, "r": res.request})
    assert SECRET not in blob
    assert res.request is not None and res.request["headers"]["Authorization"] == "••••"


async def test_api_key_in_query_is_masked_in_the_logged_request(dns: dict) -> None:
    rec = Recorder()
    res = await execute_api_tool(
        {"method": "GET", "path": "/x"},
        {},
        connection=_conn("api_key", {"name": "key", "in": "query", "value": SECRET}),
        transport=rec.transport,
    )
    assert res.request is not None and res.request["query"]["key"] == "••••" and SECRET not in json.dumps(res.request)


async def test_destination_outside_the_connections_allowed_hosts_is_refused(dns: dict) -> None:
    rec = Recorder()
    res = await execute_api_tool(
        {"method": "GET", "url": "https://other.example/x"}, {}, connection=_conn(), transport=rec.transport
    )
    assert res.status == "error" and "allowed list" in (res.error or "") and rec.requests == []


async def test_private_destination_is_refused_even_when_allowlisted(dns: dict) -> None:
    rec = Recorder()
    conn = _conn(base_url="http://169.254.169.254", allowed_hosts=["169.254.169.254"])
    res = await execute_api_tool(
        {"method": "GET", "path": "/latest/meta-data/"}, {}, connection=conn, transport=rec.transport
    )
    assert res.status == "error" and rec.requests == []


async def test_disabled_connection_blocks_the_call(dns: dict) -> None:
    rec = Recorder()
    res = await execute_api_tool(
        {"method": "GET", "path": "/x"}, {}, connection=_conn(enabled=False), transport=rec.transport
    )
    assert res.status == "error" and rec.requests == []


async def test_oauth_client_credentials_token_is_fetched_once_and_cached(dns: dict) -> None:
    from app.tools import connections as conn_mod

    conn_mod._mem_tokens.clear()
    token_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            token_calls.append(request)
            return httpx.Response(200, json={"access_token": "tok-123456789", "expires_in": 3600})
        return httpx.Response(200, json={"auth": request.headers.get("authorization")})

    conn = _conn(
        "oauth2_client_credentials",
        {"token_url": "https://auth.example.com/oauth/token", "client_id": "cid", "client_secret": "csecret-12345"},
        id=uuid.uuid4(),
    )
    t = httpx.MockTransport(handler)
    for _ in range(2):
        res = await execute_api_tool({"method": "GET", "path": "/x"}, {}, connection=conn, transport=t)
        assert res.status == "success"
    assert len(token_calls) == 1
    assert b"grant_type=client_credentials" in token_calls[0].content
    assert "tok-123456789" not in json.dumps(res.output)  # the echoed token is scrubbed
    conn_mod._mem_tokens.clear()


async def test_oauth_token_failure_is_reported_without_the_secret(dns: dict) -> None:
    from app.tools import connections as conn_mod

    conn_mod._mem_tokens.clear()
    conn = _conn(
        "oauth2_client_credentials",
        {"token_url": "https://auth.example.com/t", "client_id": "cid", "client_secret": "csecret-12345"},
    )
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "invalid_client"}))
    res = await execute_api_tool({"method": "GET", "path": "/x"}, {}, connection=conn, transport=t)
    assert res.status == "error" and "authentication failed" in (res.error or "") and "csecret" not in (res.error or "")
