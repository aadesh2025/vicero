"""Generic MCP tool provider (docs/17 Phase 1 §4, ADR-070/ADR-071).

Modeled on OpenManus's `MCPClients` (ADR-070): an explicit transport per server (stdio vs.
SSE, never autodetected) and a session used only for the duration of one call. Stateless per
call rather than a long-lived session pool — there is no persistent worker process here to
hold sessions open across requests, and a fresh connection per call keeps org isolation
trivial: there is no shared pool a session could leak across tenants through.

Deliberately does NOT use Anthropic's server-side remote-MCP-connector beta
(`mcp_servers=[...]` on `client.beta.messages.create`) even when the agent's provider is
Anthropic — ADR-071 found that's why open-agent-builder's MCP support only works on one
provider. This client always runs the client-side call/execute/append loop uniformly, so MCP
tool-calling behaves identically no matter which of Vicero's 13+ providers an org's agent
is configured on.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp import types as mcp_types
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from app.core.config import settings
from app.core.egress import EgressBlocked, check_destination
from app.core.logging import get_logger

log = get_logger("tools.mcp")


#: Test seam (like `egress.DEFAULT_TRANSPORT`): an in-process transport (an ASGI app) standing in for the network.
INNER_TRANSPORT: httpx2.AsyncBaseTransport | None = None

#: Hard ceiling on any single MCP call or discovery, whatever the server or the settings say (docs/28 section 5.3).
MAX_TIMEOUT_S = 30.0


class MCPToolError(RuntimeError):
    """The server reached us but reported the call failed, or discovery/connection failed."""


@dataclass(slots=True)
class MCPServerConfig:
    transport: str  # "stdio" | "sse" | "streamable_http"
    url_or_command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None  # HTTP auth, e.g. {"Authorization": "Bearer ..."}
    # Where this server's credentials may be sent (a Connection's allowed hosts). None = no extra rule.
    allowed_hosts: list[str] | None = None
    # A stdio server is a subprocess on this host, so it is only ever run when platform staff
    # registered it. Callers set this from the registering user; the default refuses.
    stdio_allowed: bool = False


class PinnedTransport(httpx2.AsyncBaseTransport):
    """Every request, redirects included, is re-validated and sent to the IP that was validated (docs/28 section 7
    step 9). The MCP SDK opens its own connections, so this is where it gets the same rebinding defence as
    `egress.safe_request`: resolve, refuse non-public addresses, connect to that IP with the original Host and
    TLS server name."""

    def __init__(self, allowed_hosts: list[str] | None, inner: httpx2.AsyncBaseTransport | None = None) -> None:
        self._allowed = allowed_hosts
        self._inner = inner or INNER_TRANSPORT or httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        try:
            scheme, host, _port, ip = await check_destination(str(request.url), self._allowed)
        except EgressBlocked as exc:
            raise httpx2.ConnectError(exc.reason) from exc
        if INNER_TRANSPORT is None:  # a real connection: dial the validated IP, keep Host + SNI
            request.url = request.url.copy_with(host=ip)
            if scheme == "https":
                request.extensions["sni_hostname"] = host
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _http_client(
    config: MCPServerConfig, timeout_s: float, headers: dict[str, str] | None = None
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=PinnedTransport(config.allowed_hosts),
        headers=headers if headers is not None else (config.headers or {}),
        timeout=httpx2.Timeout(timeout_s, read=max(timeout_s, 5.0)),
        follow_redirects=True,
        trust_env=False,
    )


@contextlib.asynccontextmanager
async def _session_cm(config: MCPServerConfig, timeout_s: float) -> AsyncIterator[Any]:
    if config.transport == "stdio":
        params = StdioServerParameters(command=config.url_or_command, args=config.args or [], env=config.env)
        async with stdio_client(params) as streams:
            yield streams
    elif config.transport == "sse":

        def factory(headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None) -> httpx2.AsyncClient:
            return _http_client(config, timeout_s, headers)

        async with sse_client(
            config.url_or_command,
            headers=config.headers,
            timeout=timeout_s,
            sse_read_timeout=timeout_s,
            httpx_client_factory=factory,
        ) as streams:
            yield streams
    elif config.transport == "streamable_http":
        async with _http_client(config, timeout_s) as client:
            async with streamable_http_client(config.url_or_command, http_client=client) as streams:
                yield streams
    else:
        raise MCPToolError(f"unsupported MCP transport '{config.transport}'")


async def _check_destination(config: MCPServerConfig) -> None:
    """Refuse the two ways a tenant-registered server could reach the host itself."""
    if config.transport == "stdio":
        if not config.stdio_allowed:
            raise MCPToolError("stdio MCP servers can only be registered by platform staff")
        return
    try:
        await check_destination(config.url_or_command, config.allowed_hosts)
    except EgressBlocked as exc:
        raise MCPToolError(exc.reason) from exc


def _reason(exc: BaseException) -> str:
    """A readable cause. The SDK runs on anyio task groups, so a dropped connection surfaces as "unhandled errors in
    a TaskGroup (1 sub-exception)", which tells neither the model nor the owner anything."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


async def _with_session(config: MCPServerConfig, fn: Any, *, timeout_s: float) -> Any:
    timeout_s = min(timeout_s, MAX_TIMEOUT_S)
    await _check_destination(config)

    async def _run() -> Any:
        async with _session_cm(config, timeout_s) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_s)
    except TimeoutError as exc:
        raise MCPToolError(f"MCP server timed out after {timeout_s}s") from exc
    except MCPToolError:
        raise
    except Exception as exc:  # connection refused, bad handshake, subprocess spawn failure, ...
        raise MCPToolError(_reason(exc)) from exc


def _texts_of(content: list[Any]) -> list[str]:
    return [c.text for c in content if isinstance(c, mcp_types.TextContent)]


def _flatten_content(result: mcp_types.CallToolResult) -> dict[str, Any]:
    out: dict[str, Any] = {"text": "\n".join(_texts_of(result.content))}
    # Most servers send the same data as text AND structured content; sending both doubled what the model had to
    # read. Structured content is kept only when there is no text to carry it.
    if result.structured_content is not None and not out["text"].strip():
        out["structured"] = result.structured_content
    return out


async def list_tools(
    config: MCPServerConfig, *, timeout_s: float | None = None
) -> list[dict[str, Any]]:
    """Discover a server's tools. Raises `MCPToolError` on connection/protocol failure."""

    async def _fn(session: ClientSession) -> list[dict[str, Any]]:
        result = await session.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in result.tools
        ]

    return list(await _with_session(config, _fn, timeout_s=timeout_s or settings.mcp_tool_timeout_seconds))


async def call_tool(
    config: MCPServerConfig, tool_name: str, arguments: dict[str, Any], *, timeout_s: float | None = None
) -> dict[str, Any]:
    """Call one tool on a registered server. Raises `MCPToolError` on failure."""

    async def _fn(session: ClientSession) -> dict[str, Any]:
        result = await session.call_tool(tool_name, arguments)
        if result.is_error:
            text = "\n".join(_texts_of(result.content))
            raise MCPToolError(text or f"MCP tool '{tool_name}' reported an error")
        return _flatten_content(result)

    return dict(await _with_session(config, _fn, timeout_s=timeout_s or settings.mcp_tool_timeout_seconds))
