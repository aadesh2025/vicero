"""Tool CRUD, resolution, execution dispatch, and the tool-run log."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.core import rbac
from app.core.config import settings
from app.core.crypto import encrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ssrf import is_blocked_host
from app.integrations.n8n_client import N8nClient, get_client
from app.integrations.n8n_signature import FIX_HINT, unverified_reason
from app.llm.types import ToolCall, ToolSpec
from app.models import Agent, AgentVersion, MCPServer, Organization, Tool, ToolRun
from app.modules.orgs.deps import OrgContext
from app.tools import schemas
from app.tools.base import ToolContext, ToolResult
from app.tools.builtins import BUILTINS
from app.tools.http_tool import execute_http_tool
from app.tools.mcp_client import MCPToolError
from app.tools.mcp_client import list_tools as mcp_list_tools
from app.tools.mcp_tool import execute_mcp_tool
from app.tools.mcp_tool import resolve_server_config as mcp_resolve_server_config
from app.tools.n8n_tool import execute_n8n_tool, n8n_args_schema, relax_n8n_schema

log = get_logger("tools")


# ── Built-in catalog ──────────────────────────────────────────────────────────────
def list_builtins() -> list[schemas.BuiltinToolOut]:
    return [
        schemas.BuiltinToolOut(name=b.name, description=b.description, parameters=b.parameters)
        for b in BUILTINS.values()
    ]


# ── CRUD ───────────────────────────────────────────────────────────────────────────
def _tool_out(tool: Tool) -> schemas.ToolOut:
    return schemas.ToolOut(
        id=tool.id,
        agent_id=tool.agent_id,
        name=tool.name,
        type=tool.type,
        description=tool.description,
        enabled=tool.enabled,
        config=tool.config,
        input_schema=tool.input_schema,
        created_at=tool.created_at,
    )


async def _get_tool(session: AsyncSession, ctx: OrgContext, tool_id: uuid.UUID) -> Tool:
    tool = await session.get(Tool, tool_id)
    if tool is None or tool.organization_id != ctx.org.id:
        raise AppError("tools.not_found", "Tool not found.", 404)
    return tool


async def create_tool(session: AsyncSession, ctx: OrgContext, data: schemas.CreateToolRequest) -> schemas.ToolOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    description = data.description
    input_schema = data.input_schema
    config = data.config

    if data.type == "builtin":
        builtin = BUILTINS.get(data.name)
        if builtin is None:
            raise AppError("tools.unknown_builtin", f"Unknown built-in tool '{data.name}'.", 400)
        description = description or builtin.description
        input_schema = builtin.parameters  # always mirror the canonical schema
    elif data.type == "http":
        if not config.get("url"):
            raise AppError("tools.url_required", "HTTP tools require a config.url.", 400)
    elif data.type == "mcp":
        if not config.get("server_id") or not config.get("tool_name"):
            raise AppError(
                "tools.mcp_config_required",
                "MCP tools require config.server_id and config.tool_name — "
                "use POST /v1/mcp/servers/{id}/test-connection to discover tool names first.",
                400,
            )
        try:
            server_uuid = uuid.UUID(str(config["server_id"]))
        except ValueError as exc:
            raise AppError("tools.mcp_server_not_found", "MCP server not found.", 404) from exc
        mcp_server = await session.get(MCPServer, server_uuid)
        if mcp_server is None or mcp_server.organization_id != ctx.org.id:
            raise AppError("tools.mcp_server_not_found", "MCP server not found.", 404)

    tool = Tool(
        organization_id=ctx.org.id,
        agent_id=data.agent_id,
        name=data.name,
        type=data.type,
        description=description,
        enabled=data.enabled,
        config=config,
        input_schema=input_schema,
        created_by=ctx.user.id,
    )
    session.add(tool)
    await session.flush()
    return _tool_out(tool)


async def list_tools(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID | None) -> list[schemas.ToolOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(Tool).where(Tool.organization_id == ctx.org.id).order_by(Tool.created_at.desc())
    if agent_id is not None:
        stmt = stmt.where(Tool.agent_id == agent_id)
    return [_tool_out(t) for t in (await session.execute(stmt)).scalars().all()]


async def get_tool(session: AsyncSession, ctx: OrgContext, tool_id: uuid.UUID) -> schemas.ToolOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return _tool_out(await _get_tool(session, ctx, tool_id))


async def update_tool(
    session: AsyncSession, ctx: OrgContext, tool_id: uuid.UUID, data: schemas.UpdateToolRequest
) -> schemas.ToolOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    tool = await _get_tool(session, ctx, tool_id)
    if data.description is not None:
        tool.description = data.description
    if data.enabled is not None:
        tool.enabled = data.enabled
    if data.config is not None:
        tool.config = data.config
    if data.input_schema is not None and tool.type != "builtin":
        tool.input_schema = data.input_schema
    return _tool_out(tool)


async def delete_tool(session: AsyncSession, ctx: OrgContext, tool_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    tool = await _get_tool(session, ctx, tool_id)
    await session.delete(tool)


# ── Resolution + execution ─────────────────────────────────────────────────────────
async def resolve_agent_tools(
    session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID, *, include_mcp: bool = False
) -> tuple[list[ToolSpec], dict[str, Tool]]:
    """Return (ToolSpecs, name→Tool) for the agent's enabled tools.

    `include_mcp` is off by default (docs/17 Phase 1): even an org that has registered MCP
    servers and bound `Tool` rows to them never has those rows attached to a turn unless the
    agentic runtime is on for that org (see `app.chat.budget.agentic_loop_enabled`) — the same
    "registration doesn't imply usage" split n8n binding already has, just gated on a
    different flag. Registration/CRUD is unaffected either way.
    """
    stmt = select(Tool).where(
        Tool.organization_id == org_id, Tool.agent_id == agent_id, Tool.enabled.is_(True)
    )
    if not include_mcp:
        stmt = stmt.where(Tool.type != "mcp")
    tools = list((await session.execute(stmt)).scalars().all())
    specs = [
        ToolSpec(
            name=t.name,
            description=t.description or "",
            # n8n schemas are repaired on read so tools bound before the fix stop
            # breaking turns without needing a data migration.
            parameters=(
                relax_n8n_schema(t.input_schema) if t.type == "n8n" else (t.input_schema or {})
            ),
        )
        for t in tools
    ]
    return specs, {t.name: t for t in tools}


async def build_tooling(
    session: AsyncSession,
    org_id: uuid.UUID,
    agent: Agent,
    version: AgentVersion,
    conversation_id: uuid.UUID | None,
    *,
    include_mcp: bool = False,
) -> tuple[list[ToolSpec], Any]:
    """Return (ToolSpecs, executor) for an agent's enabled tools, or ([], None) when disabled.

    Takes `org_id` (not an OrgContext) so the public/widget chat can reuse it. `executor(call)`
    runs the tool and returns a plain dict {output, status, error} (the runtime stays decoupled
    from the tools package). `include_mcp` — see `resolve_agent_tools`.
    """
    features = version.features or {}
    if not features.get("tools_enabled"):
        return [], None
    # Runtime entitlement check, independent of the CRUD gates above: an agent that somehow has
    # tools attached (created before a downgrade, imported, or by a bug) must still not run
    # them on a plan that excludes tool calling (docs/18 §9).
    org = await session.get(Organization, org_id)
    if org is not None and not usage.feature_allowed(org, "tool_calling"):
        log.info("tooling_skipped_plan", org_id=str(org_id), agent_id=str(agent.id))
        return [], None
    specs, by_name = await resolve_agent_tools(session, org_id, agent.id, include_mcp=include_mcp)
    if org is not None and not usage.feature_allowed(org, "n8n"):
        by_name = {k: t for k, t in by_name.items() if t.type != "n8n"}
        specs = [sp for sp in specs if sp.name in by_name]
    if not specs:
        return [], None

    tool_ctx = ToolContext(
        session=session,
        org_id=org_id,
        agent_id=agent.id,
        version=version,
        conversation_id=conversation_id,
    )

    async def executor(call: ToolCall) -> dict[str, Any]:
        res = await execute_tool_call(session, tool_ctx, by_name, call)
        return {"output": res.output, "status": res.status, "error": res.error}

    return specs, executor


async def _dispatch(tool: Tool, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if tool.type == "builtin":
        builtin = BUILTINS.get(tool.name)
        if builtin is None:
            return ToolResult(output={}, status="error", error=f"unknown built-in '{tool.name}'")
        return await builtin.run(ctx, args)
    if tool.type == "http":
        return await execute_http_tool(tool.config, args)
    if tool.type == "n8n":
        return await execute_n8n_tool(tool.config, args, ctx)
    if tool.type == "mcp":
        return await execute_mcp_tool(ctx.session, ctx.org_id, tool.config, args)
    return ToolResult(output={}, status="error", error=f"unsupported tool type '{tool.type}'")


async def execute_tool_call(
    session: AsyncSession,
    ctx: ToolContext,
    tools_by_name: dict[str, Tool],
    call: ToolCall,
) -> ToolResult:
    """Execute a model-requested tool call, logging a ToolRun. Never raises.

    The ToolRun is created (status ``pending``) *before* dispatch so async tools (n8n) can pass
    its id as a callback token; it's finalized afterwards (async tools stay ``pending`` until the
    callback resolves them).
    """
    tool = tools_by_name.get(call.name)
    if tool is None:
        return ToolResult(output={}, status="error", error=f"tool '{call.name}' is not available")

    run = ToolRun(
        organization_id=tool.organization_id,
        tool_id=tool.id,
        conversation_id=ctx.conversation_id,
        input=call.arguments,
        status="pending",
    )
    session.add(run)
    await session.flush()
    ctx.run_id = run.id

    t0 = time.perf_counter()
    try:
        result = await _dispatch(tool, ctx, call.arguments)
    except Exception as exc:  # a tool must never crash the turn
        result = ToolResult(output={}, status="error", error=str(exc))
        log.warning("tool_execute_error", tool=call.name, error=str(exc))
    run.latency_ms = int((time.perf_counter() - t0) * 1000)
    run.output = result.output
    run.status = result.status
    run.error = result.error
    from app.webhooks.dispatch import emit_event

    await emit_event(
        session,
        tool.organization_id,
        "tool.run",
        {"tool": tool.name, "status": result.status, "run_id": str(run.id)},
    )
    return result


async def test_tool(
    session: AsyncSession, ctx: OrgContext, tool_id: uuid.UUID, data: schemas.TestToolRequest
) -> schemas.TestToolResponse:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    tool = await _get_tool(session, ctx, tool_id)
    tool_ctx = await _tool_context_for(session, ctx, tool)
    t0 = time.perf_counter()
    try:
        result = await _dispatch(tool, tool_ctx, data.input)
    except Exception as exc:
        result = ToolResult(output={}, status="error", error=str(exc))
    latency_ms = int((time.perf_counter() - t0) * 1000)
    session.add(
        ToolRun(
            organization_id=tool.organization_id,
            tool_id=tool.id,
            input=data.input,
            output=result.output,
            status=result.status,
            latency_ms=latency_ms,
            error=result.error,
        )
    )
    return schemas.TestToolResponse(
        status=result.status, output=result.output, error=result.error, latency_ms=latency_ms
    )


async def _tool_context_for(session: AsyncSession, ctx: OrgContext, tool: Tool) -> ToolContext:
    """Build a ToolContext for a standalone test (knowledge_search needs a version)."""
    version: AgentVersion | None = None
    agent_id = tool.agent_id
    if agent_id is not None:
        stmt = (
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
        version = (await session.execute(stmt)).scalar_one_or_none()
    if version is None:
        # A throwaway version object so schema-only tools (datetime/calculator/http) can run.
        version = AgentVersion(agent_id=agent_id or uuid.uuid4(), version=0, rag_config={})
    return ToolContext(
        session=session,
        org_id=ctx.org.id,
        agent_id=agent_id or (await _any_agent_id(session, ctx)),
        version=version,
    )


async def _any_agent_id(session: AsyncSession, ctx: OrgContext) -> uuid.UUID:
    stmt = select(Agent.id).where(Agent.organization_id == ctx.org.id).limit(1)
    found = (await session.execute(stmt)).scalar_one_or_none()
    return found or uuid.uuid4()


# ── n8n binding ────────────────────────────────────────────────────────────────────
# One shared n8n instance sits behind every org, so visibility is **deny-by-default**:
# a workflow reaches an org only if it is tagged (in n8n) with that org's slug, or with
# `shared-template` for a genuinely reusable starter every org may browse.
#
# This reverses the original permissive default (ADR-040), where an untagged workflow was
# visible to everyone. Live testing showed what that meant in practice: a brand-new, empty
# org opened Automations and saw every other client's automations, because "untagged" is the
# state every workflow starts in and the one nobody remembers to leave. An operator who
# forgets to tag now leaks nothing — the workflow simply doesn't appear until it's labelled,
# which is a visible, fixable annoyance rather than a silent cross-tenant disclosure.
#
# Internal tags still hide unconditionally and take precedence over everything, including
# `shared-template`: a client binding a tool to a platform-internal workflow (an admin
# provisioner, an internal router) is a security incident, not a UX gap.
INTERNAL_TAGS = {"internal", "shared-internal", "platform-internal"}

# Opt-in, and only ever set deliberately by staff — an untagged workflow never lands here.
SHARED_TEMPLATE_TAG = "shared-template"


def workflow_visible_to_org(tags: set[str], name: str, org_slug: str) -> bool:
    """Whether `org_slug` may see (and bind) this n8n workflow. Deny-by-default."""
    if tags & INTERNAL_TAGS:
        return False
    # Extra safety net for the "SHARED — ..." internal workflows that predate tagging (the
    # auto-provisioner, the master router). Redundant under deny-by-default — an untagged
    # workflow is hidden anyway — but kept so that tagging one with a client slug by mistake
    # still doesn't expose it. Tag them `internal` in n8n to retire this check.
    if name.strip().lower().startswith(("shared —", "shared -")):
        return False
    if SHARED_TEMPLATE_TAG in tags:
        return True
    slug = org_slug.strip().lower()
    return bool(slug) and slug in tags


async def list_n8n_workflows(session: AsyncSession, ctx: OrgContext) -> list[schemas.N8nWorkflowOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await usage.require_feature(session, ctx.org, "n8n")
    client = get_client()
    workflows = await client.list_workflows()
    out: list[schemas.N8nWorkflowOut] = []
    for wf in workflows:
        name = str(wf.get("name", "workflow"))
        if not workflow_visible_to_org(client.extract_tags(wf), name, ctx.org.slug):
            continue
        out.append(
            schemas.N8nWorkflowOut(
                id=str(wf.get("id")),
                name=name,
                active=bool(wf.get("active", False)),
                webhook_url=client.extract_webhook_url(wf),
            )
        )
    return out


async def _require_signed_workflow(
    client: N8nClient, workflow: dict[str, Any] | None, webhook_url: str
) -> None:
    """Refuse a workflow that would accept an unsigned call (RISK-REGISTER R15).

    BotForge signs every request to an n8n webhook, but n8n only checks that signature if the
    workflow says to — a hand-built or pre-fix workflow accepts a bare `curl` from anyone with
    its URL, bypassing the agent, RBAC and budgets. `workflow` is what we already fetched when
    binding by id; for a pasted URL it is None and we resolve it, because **the URL is what
    BotForge will actually call**, whatever `workflow_id` accompanied it. A URL that cannot be
    resolved (no `N8N_API_KEY`, n8n down, a workflow on some other n8n) cannot be verified, so it
    is refused rather than trusted.
    """
    if workflow is None:
        try:
            workflow = await client.find_workflow_by_webhook_url(webhook_url)
        except AppError as exc:
            raise AppError(
                "tools.n8n_unverifiable",
                f"Could not check that this webhook verifies BotForge's signature ({exc.message}). "
                "Bind by workflow instead, or set N8N_API_KEY so BotForge can inspect it.",
                400,
            ) from exc
        if workflow is None:
            raise AppError(
                "tools.n8n_unverifiable",
                "No workflow on BotForge's n8n serves that webhook URL, so BotForge cannot check "
                f"that it verifies the signature. {FIX_HINT}",
                400,
            )
    reason = unverified_reason(workflow)
    if reason:
        raise AppError(
            "tools.n8n_unsigned_workflow",
            f"This workflow would accept unsigned calls: {reason}. {FIX_HINT}",
            400,
            details={"workflow_id": workflow.get("id"), "workflow_name": workflow.get("name")},
        )


async def bind_n8n_workflow(
    session: AsyncSession, ctx: OrgContext, data: schemas.BindN8nRequest
) -> schemas.ToolOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "n8n")
    client = get_client()
    webhook_url = data.webhook_url
    workflow_name = data.workflow_name
    fetched: dict[str, Any] | None = None
    if not webhook_url and data.workflow_id:
        workflow = fetched = await client.get_workflow(data.workflow_id)
        name = str(workflow.get("name", ""))
        # Same visibility rule as the list endpoint — closes the gap where an org could
        # bind a workflow it was never shown just by knowing (or guessing) its n8n id.
        if not workflow_visible_to_org(client.extract_tags(workflow), name, ctx.org.slug):
            raise AppError(
                "tools.n8n_forbidden", "This workflow is not available to your organization.", 403
            )
        workflow_name = workflow_name or name
        webhook_url = client.extract_webhook_url(workflow)
    if not webhook_url:
        raise AppError("tools.n8n_no_webhook", "Could not resolve a webhook URL for this workflow.", 400)
    if settings.n8n_require_signature_check:
        await _require_signed_workflow(client, fetched, webhook_url)

    input_schema = data.input_schema or n8n_args_schema()
    tool = Tool(
        organization_id=ctx.org.id,
        agent_id=data.agent_id,
        name=data.name,
        type="n8n",
        description=data.description or f"n8n workflow: {workflow_name or data.workflow_id}",
        enabled=True,
        config={
            "workflow_id": data.workflow_id,
            "workflow_name": workflow_name,
            "webhook_url": webhook_url,
            "mode": data.mode,
        },
        input_schema=input_schema,
        created_by=ctx.user.id,
    )
    session.add(tool)
    await session.flush()
    return _tool_out(tool)


async def resolve_n8n_callback(
    session: AsyncSession, run_id: uuid.UUID, output: dict[str, Any], status: str, error: str | None
) -> bool:
    """Resolve a pending async n8n tool run from a verified callback. Returns True if updated."""
    run = await session.get(ToolRun, run_id)
    if run is None:
        return False
    run.output = output
    run.status = status
    run.error = error
    return True


# ── MCP servers (docs/17 Phase 1 §4) ────────────────────────────────────────────────
def _mcp_server_out(server: MCPServer) -> schemas.MCPServerOut:
    return schemas.MCPServerOut(
        id=server.id,
        name=server.name,
        transport=server.transport,
        url_or_command=server.url_or_command,
        enabled=server.enabled,
        created_at=server.created_at,
    )


async def create_mcp_server(
    session: AsyncSession, ctx: OrgContext, data: schemas.CreateMCPServerRequest
) -> schemas.MCPServerOut:
    """Register an org-scoped MCP server. Registration alone never attaches it to any agent —
    a `Tool` row of type `mcp` still has to be created (POST /v1/tools), and even then it is
    only usable in a turn once the agentic runtime is on for this org (docs/17 §2)."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    if data.transport == "stdio":
        # A stdio server is a command run on the API host. Any org role with TOOLS_MANAGE
        # (including the client `editor` role) could otherwise execute arbitrary code there.
        if not ctx.user.is_staff:
            raise AppError(
                "tools.mcp_stdio_forbidden",
                "stdio MCP servers can only be registered by platform staff. Use an SSE server.",
                403,
            )
    else:
        parsed = urlparse(data.url_or_command)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise AppError("tools.mcp_url_invalid", "An SSE MCP server needs an http(s) URL.", 422)
        if await asyncio.to_thread(is_blocked_host, parsed.hostname):
            raise AppError(
                "tools.mcp_url_blocked", "That URL points at a private or loopback address.", 422
            )
    auth: dict[str, Any] = {}
    if data.args:
        auth["args"] = data.args
    if data.env:
        auth["env"] = data.env
    if data.headers:
        auth["headers"] = data.headers
    server = MCPServer(
        organization_id=ctx.org.id,
        name=data.name,
        transport=data.transport,
        url_or_command=data.url_or_command,
        auth_config_enc=encrypt(json.dumps(auth)) if auth else None,
        enabled=data.enabled,
        created_by=ctx.user.id,
    )
    session.add(server)
    await session.flush()
    return _mcp_server_out(server)


async def list_mcp_servers(session: AsyncSession, ctx: OrgContext) -> list[schemas.MCPServerOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = (
        select(MCPServer)
        .where(MCPServer.organization_id == ctx.org.id)
        .order_by(MCPServer.created_at.desc())
    )
    return [_mcp_server_out(s) for s in (await session.execute(stmt)).scalars().all()]


async def _get_mcp_server(session: AsyncSession, ctx: OrgContext, server_id: uuid.UUID) -> MCPServer:
    server = await session.get(MCPServer, server_id)
    if server is None or server.organization_id != ctx.org.id:
        raise AppError("tools.mcp_server_not_found", "MCP server not found.", 404)
    return server


async def test_mcp_server_connection(
    session: AsyncSession, ctx: OrgContext, server_id: uuid.UUID
) -> schemas.MCPTestConnectionResponse:
    """Connect, list the server's tools, and disconnect. Never raises — a bad connection is a
    normal test result, not a 5xx."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    server = await _get_mcp_server(session, ctx, server_id)
    try:
        tools = await mcp_list_tools(await mcp_resolve_server_config(session, server))
    except MCPToolError as exc:
        return schemas.MCPTestConnectionResponse(ok=False, error=str(exc))
    return schemas.MCPTestConnectionResponse(
        ok=True,
        tools=[
            schemas.MCPToolInfo(name=t["name"], description=t["description"], input_schema=t["input_schema"])
            for t in tools
        ],
    )


async def list_runs(
    session: AsyncSession, ctx: OrgContext, conversation_id: uuid.UUID | None
) -> list[schemas.ToolRunOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = (
        select(ToolRun)
        .where(ToolRun.organization_id == ctx.org.id)
        .order_by(ToolRun.created_at.desc())
        .limit(200)
    )
    if conversation_id is not None:
        stmt = stmt.where(ToolRun.conversation_id == conversation_id)
    return [
        schemas.ToolRunOut(
            id=r.id,
            tool_id=r.tool_id,
            conversation_id=r.conversation_id,
            status=r.status,
            input=r.input,
            output=r.output,
            latency_ms=r.latency_ms,
            error=r.error,
            created_at=r.created_at,
        )
        for r in (await session.execute(stmt)).scalars().all()
    ]
