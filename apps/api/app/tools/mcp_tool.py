"""Execution of MCP-bound tools (docs/17 Phase 1 §4).

Config shape (Tool.config): {"server_id": "...", "tool_name": "..."}. The server row (org-
scoped, credentials encrypted) is looked up fresh on every call and checked against the
calling org — mirrors ADR-055/047/048: a server is never resolved against the wrong tenant,
even if a `Tool` row somehow carried another org's `server_id`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.models import Connection, MCPServer, User
from app.tools import connections as conn_auth
from app.tools.base import ToolResult
from app.tools.mcp_client import MCPServerConfig, MCPToolError, call_tool

#: A tool result larger than this is cut before it goes anywhere (it will still be neutralized downstream). About 2k
#: tokens: a free-tier model allows ~8k tokens a minute in all, and a live Microsoft Learn result at twice 16000
#: characters made the follow-up model call fail with a 413 (2026-10-20).
MAX_RESULT_CHARS = 8000


def server_config(server: MCPServer) -> MCPServerConfig:
    auth: dict[str, Any] = {}
    if server.auth_config_enc:
        try:
            auth = json.loads(decrypt(server.auth_config_enc))
        except (ValueError, TypeError):
            auth = {}
    return MCPServerConfig(
        transport=server.transport,
        url_or_command=server.url_or_command,
        args=auth.get("args"),
        env=auth.get("env"),
        headers=auth.get("headers"),
    )


async def resolve_server_config(session: AsyncSession, server: MCPServer) -> MCPServerConfig:
    """`server_config` plus the stdio permission: only a server registered by platform staff may
    spawn a subprocess. Checked at connect time, not just at registration, so a stdio row created
    before that rule existed (or by a since-demoted user) stops running."""
    config = server_config(server)
    if server.transport == "stdio" and server.created_by is not None:
        creator = await session.get(User, server.created_by)
        config.stdio_allowed = bool(creator and creator.is_staff)
    if server.connection_id is not None:
        conn = await session.get(Connection, server.connection_id)
        if conn is None or conn.organization_id != server.organization_id:
            raise MCPToolError("this server's connection no longer exists")
        resolved = conn_auth.resolve(conn)
        if not resolved.enabled:
            raise MCPToolError("this server's connection is disabled")
        try:
            injected = await conn_auth.build_auth(resolved)
        except conn_auth.ConnectionAuthError as exc:
            raise MCPToolError(f"authentication failed: {exc}") from exc
        if injected.query:
            raise MCPToolError("an MCP server can only use header-based connection auth")
        config.headers = {**resolved.default_headers, **(config.headers or {}), **injected.headers}
        # The credential may only travel to a host the connection names.
        config.allowed_hosts = resolved.allowed_hosts or None
    return config


@dataclass(slots=True)
class PreparedMCP:
    """A call whose database work is done, so the network part can run without holding the session."""

    server_cfg: MCPServerConfig
    tool_name: str
    secrets: list[str]


async def prepare_mcp_call(session: AsyncSession, org_id: UUID, config: dict[str, Any]) -> PreparedMCP | ToolResult:
    """Everything that needs the database: the server row, its credentials, the plan checks. Returns a ready call,
    or the error to hand back."""
    server_id = config.get("server_id")
    tool_name = config.get("tool_name")
    if not server_id or not tool_name:
        return ToolResult(output={}, status="error", error="MCP tool is missing server_id/tool_name")

    server = await session.get(MCPServer, UUID(str(server_id)))
    if server is None or server.organization_id != org_id:
        return ToolResult(output={}, status="error", error="MCP server not found for this organization")
    if not server.enabled:
        return ToolResult(output={}, status="error", error=f"MCP server '{server.name}' is disabled")
    try:
        server_cfg = await resolve_server_config(session, server)
    except MCPToolError as exc:
        return ToolResult(output={}, status="error", error=str(exc))
    secrets = [v for v in (server_cfg.headers or {}).values() if len(v) >= 8]
    secrets += [v for v in (server_cfg.env or {}).values() if len(v) >= 8]
    return PreparedMCP(server_cfg, str(tool_name), secrets)


async def run_prepared_mcp(prepared: PreparedMCP, args: dict[str, Any]) -> ToolResult:
    """The network half. Touches no database session, so several can run at once."""
    try:
        output = await call_tool(prepared.server_cfg, prepared.tool_name, args)
    except MCPToolError as exc:
        return ToolResult(output={}, status="error", error=str(conn_auth.scrub(str(exc), prepared.secrets)))
    text = output.get("text")
    if isinstance(text, str) and len(text) > MAX_RESULT_CHARS:
        output = {**output, "text": text[:MAX_RESULT_CHARS], "truncated": True}
    return ToolResult(output=conn_auth.scrub(output, prepared.secrets), status="success")


async def execute_mcp_tool(
    session: AsyncSession, org_id: UUID, config: dict[str, Any], args: dict[str, Any]
) -> ToolResult:
    prepared = await prepare_mcp_call(session, org_id, config)
    if isinstance(prepared, ToolResult):
        return prepared
    return await run_prepared_mcp(prepared, args)
