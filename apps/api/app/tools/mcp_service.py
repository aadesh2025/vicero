"""MCP servers and their per-agent bindings (docs/28 section 5.3, ADR-133).

A server is registered once per org. What an agent may use is an explicit allowlist (`agent_mcp_bindings`;
empty means none). A turn never talks to a server to find out what it offers: discovery fills `tools_cache`
(on demand, and by a scheduled task once it is stale), and the tools an agent uses are materialised as ordinary
`tools` rows (`config.managed`) so they share the dispatcher, the run log, the plan counters and the
`agent_tool_bindings` ordering with every other tool.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.chat.guardrails import neutralize_injections
from app.core import rbac
from app.core.audit import write_audit
from app.core.crypto import encrypt
from app.core.egress import host_allowed, ip_is_public, literal_ip
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import (
    Agent,
    AgentMCPBinding,
    AgentToolBinding,
    Connection,
    MCPServer,
    Tool,
)
from app.modules.orgs.deps import OrgContext
from app.tools import connections as conn_auth
from app.tools import policy as tool_policy
from app.tools import schemas
from app.tools.limits import MAX_TOOLS_PER_AGENT, is_managed_mcp
from app.tools.mcp_client import MCPToolError
from app.tools.mcp_client import list_tools as mcp_list_tools
from app.tools.mcp_tool import resolve_server_config

log = get_logger("tools.mcp")

#: A server's cache older than this is refreshed by the scheduled task (never inside a visitor turn).
CACHE_MAX_AGE = dt.timedelta(hours=24)
MAX_CACHED_TOOLS = 200
_MAX_DESCRIPTION = 1000
_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]+")


# ── Names ───────────────────────────────────────────────────────────────────────
def make_slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "mcp"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}_{n}"
        n += 1
    return slug


def exposed_name(slug: str | None, tool: str) -> str:
    """What the model sees: `<server_slug>__<tool>`, unique across servers, safe as a function name."""
    return f"{slug or 'mcp'}__{_NAME_UNSAFE.sub('_', tool)}"[:64]


# ── Untrusted text in the cache ──────────────────────────────────────────────────
def _clean_text(text: str) -> str:
    return neutralize_injections(text.strip())[:_MAX_DESCRIPTION]


def _clean_schema(node: Any, depth: int = 0) -> Any:
    """A server's tool descriptions are shown to the model as instructions-adjacent text ("tool poisoning"),
    so every free-text field in a discovered schema is neutralized like any other untrusted input."""
    if depth > 12:
        return {}
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("description", "title") and isinstance(v, str):
                out[k] = _clean_text(v)
            else:
                out[k] = _clean_schema(v, depth + 1)
        return out
    if isinstance(node, list):
        return [_clean_schema(v, depth + 1) for v in node[:200]]
    return node


def _cache_entries(discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for t in discovered:
        name = str(t.get("name") or "").strip()
        if not name or name in seen or len(name) > 128:
            continue
        seen.add(name)
        schema = t.get("input_schema") or {}
        out.append(
            {
                "name": name,
                "description": _clean_text(str(t.get("description") or "")),
                "input_schema": _clean_schema(schema if isinstance(schema, dict) else {}),
            }
        )
        if len(out) >= MAX_CACHED_TOOLS:
            break
    return out


def _cached(server: MCPServer) -> list[dict[str, Any]]:
    return list(server.tools_cache or [])


# ── Validation ───────────────────────────────────────────────────────────────────
def _check_url(url: str) -> None:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AppError("tools.mcp_url_invalid", "An MCP server needs an http(s) URL.", 422)
    if parts.username or parts.password:
        raise AppError("tools.mcp_url_invalid", "Put credentials in headers or a connection, not in the URL.", 422)
    ip = literal_ip(parts.hostname)
    if parts.hostname.rstrip(".").lower() == "localhost" or (ip is not None and not ip_is_public(ip)):
        raise AppError("tools.mcp_url_blocked", "That URL points at a private or loopback address.", 422)


async def _check_connection(session: AsyncSession, ctx: OrgContext, connection_id: uuid.UUID | None, url: str) -> None:
    if connection_id is None:
        return
    conn = await session.get(Connection, connection_id)
    if conn is None or conn.organization_id != ctx.org.id:
        raise AppError("connections.not_found", "Connection not found.", 404)
    host = urlsplit(url).hostname or ""
    if not host_allowed(host, list(conn.allowed_hosts or [])):
        raise AppError(
            "mcp.connection_host_mismatch",
            "That connection's allowed hosts do not include this server, so its credentials cannot be sent there. "
            "Add the host to the connection first.",
            400,
        )


async def _get_server(session: AsyncSession, ctx: OrgContext, server_id: uuid.UUID) -> MCPServer:
    server = await session.get(MCPServer, server_id)
    if server is None or server.organization_id != ctx.org.id:
        raise AppError("tools.mcp_server_not_found", "MCP server not found.", 404)
    return server


async def _agents_count(session: AsyncSession, server_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(AgentMCPBinding).where(AgentMCPBinding.mcp_server_id == server_id)
    return int((await session.execute(stmt)).scalar_one())


async def server_out(session: AsyncSession, server: MCPServer) -> schemas.MCPServerOut:
    return schemas.MCPServerOut(
        id=server.id,
        name=server.name,
        slug=server.slug,
        transport=server.transport,
        url_or_command=server.url_or_command,
        enabled=server.enabled,
        created_at=server.created_at,
        connection_id=server.connection_id,
        tools=[schemas.MCPCachedTool(name=t["name"], description=t.get("description", "")) for t in _cached(server)],
        tools_cached_at=server.tools_cached_at,
        last_error=server.last_error,
        agents_count=await _agents_count(session, server.id),
    )


# ── Server CRUD ──────────────────────────────────────────────────────────────────
async def create_mcp_server(
    session: AsyncSession, ctx: OrgContext, data: schemas.CreateMCPServerRequest
) -> schemas.MCPServerOut:
    """Register an org-scoped MCP server. Registration alone attaches it to no agent."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    await usage.require_mcp_slot(session, ctx.org)
    if data.transport == "stdio":
        # A stdio server is a command run on the API host: remote code execution by design for a tenant.
        if not ctx.user.is_staff:
            raise AppError(
                "mcp.transport_forbidden",
                "stdio MCP servers can only be registered by platform staff. Use a Streamable HTTP server.",
                403,
            )
        if data.connection_id is not None:
            raise AppError("mcp.connection_not_applicable", "A stdio server cannot use a connection.", 400)
    else:
        _check_url(data.url_or_command)
        await _check_connection(session, ctx, data.connection_id, data.url_or_command)
    auth: dict[str, Any] = {}
    if data.args:
        auth["args"] = data.args
    if data.env:
        auth["env"] = data.env
    if data.headers:
        auth["headers"] = data.headers
    taken = set(
        (await session.execute(select(MCPServer.slug).where(MCPServer.organization_id == ctx.org.id))).scalars()
    )
    server = MCPServer(
        organization_id=ctx.org.id,
        name=data.name,
        slug=make_slug(data.name, {s for s in taken if s}),
        transport=data.transport,
        url_or_command=data.url_or_command.strip() if data.transport != "stdio" else data.url_or_command,
        auth_config_enc=encrypt(json.dumps(auth)) if auth else None,
        connection_id=data.connection_id,
        enabled=data.enabled,
        created_by=ctx.user.id,
    )
    session.add(server)
    await session.flush()
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "mcp_server.created",
        target_type="mcp_server",
        target_id=str(server.id),
        meta={"name": data.name, "transport": data.transport},
    )
    return await server_out(session, server)


async def list_mcp_servers(session: AsyncSession, ctx: OrgContext) -> list[schemas.MCPServerOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(MCPServer).where(MCPServer.organization_id == ctx.org.id).order_by(MCPServer.created_at.desc())
    return [await server_out(session, s) for s in (await session.execute(stmt)).scalars().all()]


async def update_mcp_server(
    session: AsyncSession, ctx: OrgContext, server_id: uuid.UUID, data: schemas.UpdateMCPServerRequest
) -> schemas.MCPServerOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    server = await _get_server(session, ctx, server_id)
    changed: list[str] = []
    url_changed = False
    if data.name is not None and data.name != server.name:
        server.name = data.name  # the slug (and so every tool name the model sees) deliberately does not follow
        changed.append("name")
    new_url = data.url_or_command if data.url_or_command is not None else server.url_or_command
    new_conn = data.connection_id if "connection_id" in data.model_fields_set else server.connection_id
    if server.transport != "stdio":
        if data.url_or_command is not None or "connection_id" in data.model_fields_set:
            _check_url(new_url)
            await _check_connection(session, ctx, new_conn, new_url)
    elif new_conn is not None:
        raise AppError("mcp.connection_not_applicable", "A stdio server cannot use a connection.", 400)
    if data.url_or_command is not None and data.url_or_command != server.url_or_command:
        server.url_or_command = data.url_or_command.strip()
        url_changed = True
        changed.append("url")
    if "connection_id" in data.model_fields_set and new_conn != server.connection_id:
        server.connection_id = new_conn
        changed.append("connection")
    if data.headers is not None:
        auth = _auth_blob(server)
        if data.headers:
            auth["headers"] = data.headers
        else:
            auth.pop("headers", None)
        server.auth_config_enc = encrypt(json.dumps(auth)) if auth else None
        changed.append("headers")
    if data.enabled is not None and data.enabled != server.enabled:
        server.enabled = data.enabled
        changed.append("enabled")
    if url_changed:
        # A different URL is a different server: forget what the old one offered and switch its tools off
        # until a refresh shows what this one offers.
        server.tools_cache, server.tools_cached_at, server.last_error = None, None, None
        for t in await _managed_tools(session, server):
            t.enabled = False
    await session.flush()
    if changed:
        await write_audit(
            session,
            ctx.org.id,
            ctx.user.id,
            "mcp_server.updated",
            target_type="mcp_server",
            target_id=str(server.id),
            meta={"fields": changed},
        )
    return await server_out(session, server)


def _auth_blob(server: MCPServer) -> dict[str, Any]:
    if not server.auth_config_enc:
        return {}
    from app.core.crypto import decrypt

    try:
        blob = json.loads(decrypt(server.auth_config_enc))
    except (ValueError, TypeError):
        return {}
    return blob if isinstance(blob, dict) else {}


async def delete_mcp_server(session: AsyncSession, ctx: OrgContext, server_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    server = await _get_server(session, ctx, server_id)
    legacy = [t.name for t in await _server_tool_rows(session, server) if not is_managed_mcp(t)]
    if legacy:
        raise AppError(
            "mcp.in_use", "Tools still point at this server. Delete them first.", 409, details={"tools": sorted(legacy)}
        )
    for t in await _managed_tools(session, server):
        await session.delete(t)  # its agent_tool_bindings and runs go with it (cascade)
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "mcp_server.deleted",
        target_type="mcp_server",
        target_id=str(server.id),
        meta={"name": server.name},
    )
    await session.delete(server)
    await session.flush()


# ── Discovery ────────────────────────────────────────────────────────────────────
async def _server_tool_rows(session: AsyncSession, server: MCPServer) -> list[Tool]:
    stmt = select(Tool).where(
        Tool.organization_id == server.organization_id,
        Tool.type == "mcp",
        Tool.config["server_id"].astext == str(server.id),
    )
    return list((await session.execute(stmt)).scalars().all())


async def _managed_tools(session: AsyncSession, server: MCPServer) -> list[Tool]:
    return [t for t in await _server_tool_rows(session, server) if is_managed_mcp(t)]


async def refresh_server(session: AsyncSession, server: MCPServer) -> schemas.MCPRefreshResponse:
    """Connect, list the server's tools, store them, and bring materialised tools in step. Never raises."""
    secrets: list[str] = []
    try:
        config = await resolve_server_config(session, server)
        secrets = [v for v in (config.headers or {}).values() if len(v) >= 8]
        discovered = await mcp_list_tools(config)
    except MCPToolError as exc:
        message = str(conn_auth.scrub(str(exc), secrets))[:500]
        server.last_error = message
        await session.flush()
        return schemas.MCPRefreshResponse(ok=False, error=message)
    cache = _cache_entries(discovered)
    server.tools_cache = cache
    server.tools_cached_at = dt.datetime.now(tz=dt.UTC)
    server.last_error = None
    by_name = {t["name"]: t for t in cache}
    for row in await _managed_tools(session, server):
        entry = by_name.get(str(row.config.get("tool_name")))
        if entry is None:
            row.enabled = False  # the server no longer offers it
        else:
            row.enabled = True
            row.description = entry["description"]
            row.input_schema = entry["input_schema"]
    await session.flush()
    return schemas.MCPRefreshResponse(
        ok=True, tools=[schemas.MCPCachedTool(name=t["name"], description=t["description"]) for t in cache]
    )


async def refresh_tools(session: AsyncSession, ctx: OrgContext, server_id: uuid.UUID) -> schemas.MCPRefreshResponse:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    server = await _get_server(session, ctx, server_id)
    return await refresh_server(session, server)


async def test_mcp_server_connection(
    session: AsyncSession, ctx: OrgContext, server_id: uuid.UUID
) -> schemas.MCPTestConnectionResponse:
    """Connect, list the server's tools, and disconnect, without touching the cache. A bad connection is a
    normal result, not a 5xx."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    server = await _get_server(session, ctx, server_id)
    try:
        tools = await mcp_list_tools(await resolve_server_config(session, server))
    except MCPToolError as exc:
        return schemas.MCPTestConnectionResponse(ok=False, error=str(exc))
    return schemas.MCPTestConnectionResponse(
        ok=True,
        tools=[
            schemas.MCPToolInfo(name=t["name"], description=t["description"], input_schema=t["input_schema"])
            for t in tools
        ],
    )


async def stale_server_ids(session: AsyncSession, *, limit: int = 50) -> list[uuid.UUID]:
    """Enabled servers an agent uses whose cache is missing or older than a day (for the scheduled refresh)."""
    cutoff = dt.datetime.now(tz=dt.UTC) - CACHE_MAX_AGE
    stmt = (
        select(MCPServer.id)
        .where(
            MCPServer.enabled.is_(True),
            MCPServer.transport != "stdio",
            MCPServer.id.in_(select(AgentMCPBinding.mcp_server_id)),
            (MCPServer.tools_cached_at.is_(None)) | (MCPServer.tools_cached_at < cutoff),
        )
        .order_by(MCPServer.tools_cached_at.asc().nulls_first())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# ── Per-agent bindings ───────────────────────────────────────────────────────────
async def _get_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.organization_id != ctx.org.id:
        raise AppError("agents.not_found", "Agent not found.", 404)
    return agent


def _binding_out(server: MCPServer, b: AgentMCPBinding) -> schemas.MCPBindingOut:
    enabled = set(b.enabled_tools or [])
    stale = server.tools_cached_at is None or server.tools_cached_at < dt.datetime.now(tz=dt.UTC) - CACHE_MAX_AGE
    return schemas.MCPBindingOut(
        server_id=server.id,
        server_name=server.name,
        slug=server.slug,
        enabled=b.enabled,
        enabled_tools=sorted(enabled),
        tool_overrides={k: schemas.MCPToolOverride(**v) for k, v in (b.tool_overrides or {}).items()},
        available_tools=[
            schemas.MCPAvailableTool(
                name=t["name"],
                description=t.get("description", ""),
                exposed_as=exposed_name(server.slug, t["name"]),
                enabled=t["name"] in enabled,
            )
            for t in _cached(server)
        ],
        tools_cached_at=server.tools_cached_at,
        cache_stale=stale,
    )


async def list_agent_mcp(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> list[schemas.MCPBindingOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_agent(session, ctx, agent_id)
    stmt = (
        select(MCPServer, AgentMCPBinding)
        .join(AgentMCPBinding, AgentMCPBinding.mcp_server_id == MCPServer.id)
        .where(AgentMCPBinding.agent_id == agent_id, AgentMCPBinding.organization_id == ctx.org.id)
        .order_by(MCPServer.name)
    )
    return [_binding_out(s, b) for s, b in (await session.execute(stmt)).all()]


async def _ensure_managed_tool(
    session: AsyncSession, org_id: uuid.UUID, server: MCPServer, entry: dict[str, Any], existing: list[Tool]
) -> Tool:
    for row in existing:
        if row.config.get("tool_name") == entry["name"]:
            row.enabled = True
            row.description = entry["description"]
            row.input_schema = entry["input_schema"]
            return row
    name = exposed_name(server.slug, entry["name"])
    used = {r.name for r in existing}
    n = 2
    while name in used:  # two tool names that sanitise to the same text
        name = f"{exposed_name(server.slug, entry['name'])[:60]}_{n}"
        n += 1
    row = Tool(
        organization_id=org_id,
        agent_id=None,
        name=name,
        type="mcp",
        description=entry["description"],
        enabled=True,
        config={"server_id": str(server.id), "tool_name": entry["name"], "managed": True},
        input_schema=entry["input_schema"],
    )
    session.add(row)
    existing.append(row)
    await session.flush()
    return row


async def set_agent_mcp(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.SetAgentMCPRequest
) -> list[schemas.MCPBindingOut]:
    """Replace the agent's MCP allowlist. Servers left out are unbound; their tools stop reaching the model."""
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    await usage.require_mcp_allowed(session, ctx.org)
    await _get_agent(session, ctx, agent_id)
    ids = [b.server_id for b in data.servers]
    if len(set(ids)) != len(ids):
        raise AppError("mcp.duplicate_binding", "A server can be listed only once.", 400)
    servers: dict[uuid.UUID, MCPServer] = {}
    if ids:
        rows = await session.execute(
            select(MCPServer).where(MCPServer.id.in_(ids), MCPServer.organization_id == ctx.org.id)
        )
        servers = {s.id: s for s in rows.scalars().all()}
    if len(servers) != len(ids):
        raise AppError("tools.mcp_server_not_found", "MCP server not found.", 404)

    for b in data.servers:
        server = servers[b.server_id]
        offered = {t["name"] for t in _cached(server)}
        if b.enabled_tools and server.tools_cached_at is None:
            raise AppError(
                "mcp.refresh_required",
                f"'{server.name}' has not been discovered yet. Refresh its tools first.",
                409,
            )
        unknown = sorted(set(b.enabled_tools) - offered)
        if unknown:
            raise AppError(
                "mcp.unknown_tool",
                f"'{server.name}' does not offer: {', '.join(unknown)}.",
                400,
                details={"tools": unknown},
            )
        stray = sorted(set(b.tool_overrides) - set(b.enabled_tools))
        if stray:
            raise AppError(
                "mcp.bad_override", f"Overrides given for tools that are not enabled: {', '.join(stray)}.", 400
            )

    # The 20-tool ceiling counts every enabled tool the agent would have: MCP and everything else.
    other_enabled = int(
        (
            await session.execute(
                select(func.count())
                .select_from(AgentToolBinding)
                .join(Tool, Tool.id == AgentToolBinding.tool_id)
                .where(
                    AgentToolBinding.agent_id == agent_id,
                    AgentToolBinding.enabled.is_(True),
                    Tool.config["managed"].as_boolean().is_not(True),
                )
            )
        ).scalar_one()
    )
    mcp_enabled = sum(len(set(b.enabled_tools)) for b in data.servers if b.enabled)
    if other_enabled + mcp_enabled > MAX_TOOLS_PER_AGENT:
        raise AppError(
            "tools.too_many",
            f"An agent can have at most {MAX_TOOLS_PER_AGENT} enabled tools "
            f"({other_enabled} others + {mcp_enabled} MCP). "
            "Split the work across agents or workflows.",
            400,
        )

    existing_bindings = {
        b.mcp_server_id: b
        for b in (
            await session.execute(
                select(AgentMCPBinding).where(
                    AgentMCPBinding.agent_id == agent_id, AgentMCPBinding.organization_id == ctx.org.id
                )
            )
        ).scalars()
    }
    desired_tool_ids: dict[uuid.UUID, dict[str, Any]] = {}
    for b in data.servers:
        server = servers[b.server_id]
        enabled_tools = sorted(set(b.enabled_tools))
        overrides = {
            k: tool_policy.clean_overrides(v.model_dump(exclude_none=True)) for k, v in b.tool_overrides.items()
        }
        row = existing_bindings.get(server.id)
        if row is None:
            session.add(
                AgentMCPBinding(
                    organization_id=ctx.org.id,
                    agent_id=agent_id,
                    mcp_server_id=server.id,
                    enabled_tools=enabled_tools,
                    tool_overrides=overrides,
                    enabled=b.enabled,
                )
            )
        else:
            row.enabled_tools, row.tool_overrides, row.enabled = enabled_tools, overrides, b.enabled
        if not b.enabled:
            continue
        managed = await _managed_tools(session, server)
        by_name = {t["name"]: t for t in _cached(server)}
        for tool_name in enabled_tools:
            tool = await _ensure_managed_tool(session, ctx.org.id, server, by_name[tool_name], managed)
            desired_tool_ids[tool.id] = overrides.get(tool_name, {})
    for server_id, row in existing_bindings.items():
        if server_id not in servers:
            await session.delete(row)

    # Mirror the allowlist into agent_tool_bindings, touching only this agent's managed MCP rows.
    mine = (
        await session.execute(
            select(AgentToolBinding, Tool)
            .join(Tool, Tool.id == AgentToolBinding.tool_id)
            .where(AgentToolBinding.agent_id == agent_id, Tool.type == "mcp")
        )
    ).all()
    have: dict[uuid.UUID, AgentToolBinding] = {}
    for atb, tool in mine:
        if not is_managed_mcp(tool):
            continue
        if tool.id in desired_tool_ids:
            have[tool.id] = atb
        else:
            await session.delete(atb)
    next_pos = int(
        (
            await session.execute(
                select(func.coalesce(func.max(AgentToolBinding.position), -1)).where(
                    AgentToolBinding.agent_id == agent_id
                )
            )
        ).scalar_one()
    )
    for tool_id, override in sorted(desired_tool_ids.items(), key=lambda kv: str(kv[0])):
        atb = have.get(tool_id)
        if atb is None:
            next_pos += 1
            session.add(
                AgentToolBinding(
                    organization_id=ctx.org.id,
                    agent_id=agent_id,
                    tool_id=tool_id,
                    enabled=True,
                    overrides=override,
                    position=next_pos,
                )
            )
        else:
            atb.enabled, atb.overrides = True, override
    await session.flush()
    await write_audit(
        session,
        ctx.org.id,
        ctx.user.id,
        "agent.mcp_set",
        target_type="agent",
        target_id=str(agent_id),
        meta={"servers": {str(b.server_id): sorted(set(b.enabled_tools)) for b in data.servers}},
    )
    return await list_agent_mcp(session, ctx, agent_id)
