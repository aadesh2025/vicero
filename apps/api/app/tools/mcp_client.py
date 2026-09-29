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
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp import types as mcp_types
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

from app.core.config import settings
from app.core.logging import get_logger
from app.core.ssrf import is_blocked_host

log = get_logger("tools.mcp")


class MCPToolError(RuntimeError):
    """The server reached us but reported the call failed, or discovery/connection failed."""


@dataclass(slots=True)
class MCPServerConfig:
    transport: str  # "stdio" | "sse"
    url_or_command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None  # SSE auth, e.g. {"Authorization": "Bearer ..."}
    # A stdio server is a subprocess on this host, so it is only ever run when platform staff
    # registered it. Callers set this from the registering user; the default refuses.
    stdio_allowed: bool = False


def _session_cm(config: MCPServerConfig) -> Any:
    if config.transport == "stdio":
        params = StdioServerParameters(
            command=config.url_or_command, args=config.args or [], env=config.env
        )
        return stdio_client(params)
    if config.transport == "sse":
        return sse_client(config.url_or_command, headers=config.headers)
    raise MCPToolError(f"unsupported MCP transport '{config.transport}'")


async def _check_destination(config: MCPServerConfig) -> None:
    """Refuse the two ways a tenant-registered server could reach the host itself."""
    if config.transport == "stdio":
        if not config.stdio_allowed:
            raise MCPToolError("stdio MCP servers can only be registered by platform staff")
        return
    parsed = urlparse(config.url_or_command)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise MCPToolError("an SSE MCP server needs an http(s) URL")
    if await asyncio.to_thread(is_blocked_host, parsed.hostname):
        raise MCPToolError("refusing to connect to a private/loopback host")


async def _with_session(config: MCPServerConfig, fn: Any, *, timeout_s: float) -> Any:
    await _check_destination(config)

    async def _run() -> Any:
        async with _session_cm(config) as (read, write):
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
        raise MCPToolError(str(exc)) from exc


def _texts_of(content: list[Any]) -> list[str]:
    return [c.text for c in content if isinstance(c, mcp_types.TextContent)]


def _flatten_content(result: mcp_types.CallToolResult) -> dict[str, Any]:
    out: dict[str, Any] = {"text": "\n".join(_texts_of(result.content))}
    if result.structured_content is not None:
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
