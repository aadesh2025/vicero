"""API tool v2 (docs/28 §5.2, ADR-132): a configurable HTTP request the model fills in with typed arguments.

Config (Tool.config, `type="api"`; legacy `type="http"` is the same shape with no connection):

    {"method": "GET|POST|PUT|PATCH|DELETE", "path": "/orders/{{id}}"  (joined to the connection's base_url)
     | "url": "https://...", "query": {..}, "headers": {..}, "body": {..}|"..", "body_type": "json|form|raw",
     "response": {"pick": ["$.data.items[0].price"], "max_chars": 4000},
     "retry": {"max": 0-3, "backoff_ms": 300}, "timeout_s": 15}

Templates take `{{param}}` (a model-supplied argument) and `{{vars.name}}` (a conversation variable).
Values go in as **data**: URL path/query values are percent-encoded, header values lose CR/LF, and an
argument that is an exact placeholder in a JSON body keeps its type (a number stays a number).
Secrets come from the Connection at execution time only and never reach the model or a stored row.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft202012Validator

from app.core.config import settings
from app.core.egress import EgressBlocked, EgressError, EgressResponse, safe_request
from app.tools.base import ToolResult
from app.tools.connections import (
    MASK,
    AuthInjection,
    ConnectionAuthError,
    ResolvedConnection,
    build_auth,
    scrub,
    secret_values,
)

_PLACEHOLDER = re.compile(r"\{\{\s*(vars\.)?([a-zA-Z0-9_]+)\s*\}\}")
_EXACT = re.compile(r"^\s*\{\{\s*(vars\.)?([a-zA-Z0-9_]+)\s*\}\}\s*$")
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_RETRY_STATUS = frozenset({429, 502, 503, 504})
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_SENSITIVE_HEADER = re.compile(r"authorization|cookie|token|secret|password|api[-_]?key", re.IGNORECASE)

DEFAULT_MAX_CHARS = 4000
HARD_MAX_CHARS = 16000
MAX_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 1_000_000
_MAX_PICK = 20


# ── Templating ───────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class _Ctx:
    args: dict[str, Any]
    vars: dict[str, Any]

    def lookup(self, is_var: bool, name: str) -> Any:
        return (self.vars if is_var else self.args).get(name)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def render_text(template: str, ctx: _Ctx, *, encode: bool = False) -> str:
    """Fill `{{x}}`. `encode` percent-encodes each value (path / query), `/` included."""

    def sub(m: re.Match[str]) -> str:
        text = _as_text(ctx.lookup(bool(m.group(1)), m.group(2)))
        return quote(text, safe="") if encode else text

    return _PLACEHOLDER.sub(sub, template)


def render_header_value(template: str, ctx: _Ctx) -> str:
    return render_text(template, ctx).replace("\r", "").replace("\n", "")


def render_json(value: Any, ctx: _Ctx, *, native: bool) -> Any:
    if isinstance(value, str):
        m = _EXACT.match(value)
        if m and native:
            return ctx.lookup(bool(m.group(1)), m.group(2))
        return render_text(value, ctx)
    if isinstance(value, dict):
        return {k: render_json(v, ctx, native=native) for k, v in value.items()}
    if isinstance(value, list):
        return [render_json(v, ctx, native=native) for v in value]
    return value


def template_has_host_placeholder(url_template: str) -> bool:
    """True when `{{...}}` appears in the scheme or host of an absolute URL template: the destination
    must never be steered by model-supplied text (the egress guard would still catch a private
    address, but a public attacker host is just as bad)."""
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]*)", url_template.strip())
    head = m.group(1) if m else url_template.strip().split("/", 1)[0]
    return "{{" in head


# ── Response selectors: a small JSONPath subset, no eval ──────────────────────────────
_TOKEN = re.compile(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]|\[\*\]|\['([^']+)'\]")


def parse_selector(selector: str) -> list[str | int | None] | None:
    """`$.a.b[0].c` -> ['a','b',0,'c']; `[*]` -> None (every element). None if malformed."""
    sel = selector.strip()
    if not sel.startswith("$"):
        return None
    pos, steps = 1, []
    while pos < len(sel):
        m = _TOKEN.match(sel, pos)
        if m is None:
            return None
        if m.group(1) is not None:
            steps.append(m.group(1))
        elif m.group(2) is not None:
            steps.append(int(m.group(2)))
        elif m.group(3) is not None:
            steps.append(m.group(3))
        else:
            steps.append(None)
        pos = m.end()
    return steps


_MISSING = object()


def select(data: Any, steps: list[str | int | None]) -> Any:
    """Apply parsed steps. A missing key/index yields `_MISSING`; `[*]` maps over a list."""
    if not steps:
        return data
    step, rest = steps[0], steps[1:]
    if step is None:
        if not isinstance(data, list):
            return _MISSING
        items = [select(item, rest) for item in data]
        return [i for i in items if i is not _MISSING]
    if isinstance(step, int):
        if isinstance(data, list) and -len(data) <= step < len(data):
            return select(data[step], rest)
        return _MISSING
    if isinstance(data, dict) and step in data:
        return select(data[step], rest)
    return _MISSING


def pick(data: Any, selectors: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for selector in selectors[:_MAX_PICK]:
        steps = parse_selector(selector)
        if steps is None:
            continue
        value = select(data, steps)
        out[selector[2:] if selector.startswith("$.") else selector] = None if value is _MISSING else value
    return out


def validate_selectors(selectors: Any) -> str | None:
    """Config-time check, so a typo fails at save and not on the first live call."""
    if not isinstance(selectors, list) or not all(isinstance(s, str) for s in selectors):
        return "response.pick must be a list of selector strings"
    if len(selectors) > _MAX_PICK:
        return f"response.pick takes at most {_MAX_PICK} selectors"
    for s in selectors:
        if parse_selector(s) is None:
            return f"'{s}' is not a valid selector (use $.a.b[0].c)"
    return None


# ── Arguments ───────────────────────────────────────────────────────────────────
def validate_args(schema: dict[str, Any] | None, args: dict[str, Any]) -> list[str]:
    """JSON Schema errors, as short strings the model can act on. Empty list = valid."""
    if not schema:
        return []
    try:
        errors = sorted(Draft202012Validator(schema).iter_errors(args), key=lambda e: list(e.absolute_path))
    except Exception:  # a malformed schema must not take the turn down
        return []
    out: list[str] = []
    for e in errors[:5]:
        where = ".".join(str(p) for p in e.absolute_path)
        out.append(f"{where}: {e.message}" if where else e.message)
    return out


# ── Execution ───────────────────────────────────────────────────────────────────
def _timeout(config: dict[str, Any], conn: ResolvedConnection | None) -> float:
    raw = config.get("timeout_s", config.get("timeout"))
    if raw is None:
        raw = conn.timeout_s if conn else settings.tool_timeout_seconds
    try:
        return max(1.0, min(float(raw), MAX_TIMEOUT_S))
    except (TypeError, ValueError):
        return float(settings.tool_timeout_seconds)


def _retry_policy(config: dict[str, Any]) -> tuple[int, float]:
    retry = config.get("retry") or {}
    try:
        n = max(0, min(int(retry.get("max", 0)), 3))
        backoff = max(0.0, min(float(retry.get("backoff_ms", 300)), 5000.0)) / 1000
    except (TypeError, ValueError):
        return 0, 0.3
    return n, backoff


def _max_chars(config: dict[str, Any]) -> int:
    try:
        n = int((config.get("response") or {}).get("max_chars", DEFAULT_MAX_CHARS))
    except (TypeError, ValueError):
        n = DEFAULT_MAX_CHARS
    return max(200, min(n, HARD_MAX_CHARS))


def build_url(
    config: dict[str, Any], conn: ResolvedConnection | None, ctx: _Ctx
) -> tuple[str | None, str | None]:
    """(url, error). With a connection a `path` is joined to its base URL; otherwise `url` is required."""
    path, url = config.get("path"), config.get("url")
    if conn is not None and path is not None:
        return conn.base_url.rstrip("/") + "/" + render_text(str(path), ctx, encode=True).lstrip("/"), None
    template = str(url if url is not None else "").strip()
    if not template:
        return None, "tool has no url configured"
    if template_has_host_placeholder(template):
        return None, "the host of a tool URL cannot come from arguments"
    parts = urlsplit(template)
    if parts.scheme and parts.netloc:
        origin = f"{parts.scheme}://{parts.netloc}"
        rest = template[len(origin) :]
        return origin + render_text(rest, ctx, encode=True), None
    return render_text(template, ctx, encode=True), None


def _sanitize_request(
    method: str,
    url: str,
    headers: dict[str, str],
    injected: AuthInjection,
    params: dict[str, str],
    body_preview: Any,
    secrets: list[str],
) -> dict[str, Any]:
    shown_params = {k: (MASK if k in injected.query else v) for k, v in params.items()}
    shown_headers = {
        k: (MASK if k in injected.headers or _SENSITIVE_HEADER.search(k) else v) for k, v in headers.items()
    }
    request: dict[str, Any] = {"method": method, "url": url, "headers": shown_headers}
    if shown_params:
        request["query"] = shown_params
    if body_preview is not None:
        text = body_preview if isinstance(body_preview, str) else json.dumps(body_preview, default=str)
        request["body"] = text[:2000]
    return scrub(request, secrets)  # type: ignore[no-any-return]


def _shape_response(resp: EgressResponse, config: dict[str, Any]) -> dict[str, Any]:
    max_chars = _max_chars(config)
    parsed: Any = None
    is_json = False
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype and not resp.truncated:
        try:
            parsed = resp.json()
            is_json = True
        except ValueError:
            pass
    out: dict[str, Any] = {"status_code": resp.status_code}
    selectors = (config.get("response") or {}).get("pick")
    if selectors and is_json:
        out["data"] = pick(parsed, list(selectors))
        return out
    if is_json:
        text = json.dumps(parsed, ensure_ascii=False)
        if len(text) <= max_chars:
            out["body"] = parsed
            return out
        out["body"] = text[:max_chars]
        out["truncated"] = True
        return out
    text = resp.text
    out["body"] = text[:max_chars]
    if len(text) > max_chars or resp.truncated:
        out["truncated"] = True
    return out


async def execute_api_tool(
    config: dict[str, Any],
    args: dict[str, Any],
    *,
    connection: ResolvedConnection | None = None,
    input_schema: dict[str, Any] | None = None,
    variables: dict[str, Any] | None = None,
    legacy: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ToolResult:
    """Run one API tool call. Never raises; failures come back as a `ToolResult` with status "error".

    `legacy=True` is the old `type="http"` behaviour: arguments always render as strings.
    """
    secrets = secret_values(connection.auth_type, connection.secrets) if connection else []

    problems = validate_args(input_schema, args)
    if problems:
        return ToolResult(
            output={"error": "invalid_arguments", "details": problems},
            status="error",
            error="invalid arguments: " + "; ".join(problems),
        )

    if connection is not None and not connection.enabled:
        return ToolResult(output={}, status="error", error="this tool's connection is disabled")

    method = str(config.get("method", "GET")).upper()
    if method not in _METHODS:
        return ToolResult(output={}, status="error", error=f"unsupported method '{method}'")

    ctx = _Ctx(args=args, vars=variables or {})
    url, err = build_url(config, connection, ctx)
    if url is None:
        return ToolResult(output={}, status="error", error=err)

    # Headers: connection defaults, then the tool's, then auth last so a tool cannot shadow it.
    headers: dict[str, str] = dict(connection.default_headers) if connection else {}
    for k, v in (config.get("headers") or {}).items():
        headers[str(k)] = render_header_value(str(v), ctx)

    # Query: an optional parameter that is exactly one placeholder and was not supplied is left out.
    params: dict[str, str] = {}
    for k, v in (config.get("query") or {}).items():
        if isinstance(v, str):
            m = _EXACT.match(v)
            if m and ctx.lookup(bool(m.group(1)), m.group(2)) in (None, ""):
                continue
            params[str(k)] = render_text(v, ctx)
        elif v is not None:
            params[str(k)] = _as_text(v)

    body_kwargs: dict[str, Any] = {}
    body_preview: Any = None
    raw_body = config.get("body")
    if raw_body is not None and method in ("POST", "PUT", "PATCH", "DELETE"):
        body_type = config.get("body_type") or ("raw" if isinstance(raw_body, str) else "json")
        rendered = render_json(raw_body, ctx, native=not legacy and body_type == "json")
        body_preview = rendered
        if body_type == "form" and isinstance(rendered, dict):
            body_kwargs["data"] = {k: _as_text(v) for k, v in rendered.items()}
        elif body_type == "raw" or isinstance(rendered, str):
            body_kwargs["content"] = rendered if isinstance(rendered, str) else json.dumps(rendered)
        else:
            body_kwargs["json"] = rendered

    try:
        injected = await build_auth(connection, transport=transport) if connection else AuthInjection()
    except ConnectionAuthError as exc:
        return ToolResult(output={}, status="error", error=f"authentication failed: {exc}")
    # Injected secrets are visible to the scrubber even if the connection type did not list them.
    secrets = secrets + [v for v in [*injected.headers.values(), *injected.query.values()] if len(v) >= 8]
    send_headers = {**headers, **injected.headers}
    send_params = {**params, **injected.query}

    timeout_s = _timeout(config, connection)
    retries, backoff = _retry_policy(config)
    idempotent = method in _IDEMPOTENT_METHODS or any("idempotency-key" in h.lower() for h in send_headers)
    attempts = 1 + (retries if idempotent else 0)
    allowed = connection.allowed_hosts if connection and connection.allowed_hosts else None
    sanitized = _sanitize_request(method, url, send_headers, injected, send_params, body_preview, secrets)

    last_error = "request failed"
    for attempt in range(1, attempts + 1):
        try:
            resp = await safe_request(
                method,
                url,
                allowed_hosts=allowed,
                headers=send_headers,
                params=send_params or None,
                timeout_s=timeout_s,
                max_bytes=MAX_RESPONSE_BYTES,
                transport=transport,
                **body_kwargs,
            )
        except EgressBlocked as exc:
            return ToolResult(output={}, status="error", error=exc.reason, attempt=attempt, request=sanitized)
        except EgressError as exc:
            last_error = str(exc)
            if attempt < attempts:
                await asyncio.sleep(backoff * attempt)
                continue
            status = "timeout" if "timed out" in last_error else "error"
            return ToolResult(
                output={}, status=status, error=scrub(last_error, secrets), attempt=attempt, request=sanitized
            )
        if resp.status_code in _RETRY_STATUS and attempt < attempts:
            await asyncio.sleep(backoff * attempt)
            continue
        output = scrub(_shape_response(resp, config), secrets)
        ok = resp.status_code < 400
        return ToolResult(
            output=output,
            status="success" if ok else "error",
            error=None if ok else f"HTTP {resp.status_code}",
            attempt=attempt,
            request=sanitized,
        )
    return ToolResult(  # pragma: no cover
        output={}, status="error", error=last_error, attempt=attempts, request=sanitized
    )
