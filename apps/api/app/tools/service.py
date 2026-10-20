"""Tool CRUD, resolution, execution dispatch, and the tool-run log."""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import automations as automation_rules
from app.billing import usage
from app.chat import blocks as chat_blocks
from app.core import rbac
from app.core.audit import write_audit
from app.core.catalog import catalog_enabled
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.integrations.n8n_client import N8nClient, get_client, valid_callback_token
from app.integrations.n8n_signature import FIX_HINT, unverified_reason
from app.llm.types import ToolCall, ToolSpec
from app.models import Agent, AgentToolBinding, AgentVersion, Connection, MCPServer, Organization, Tool, ToolRun
from app.modules.orgs.deps import OrgContext
from app.tools import api_tool, block_tools, schemas
from app.tools.base import ToolContext, ToolResult
from app.tools.builtins import BUILTINS, CATALOG_TOOLS
from app.tools.compact import compact_description, compact_schema
from app.tools.connections import load_connection
from app.tools.limits import MAX_TOOLS_PER_AGENT, is_managed_mcp
from app.tools.mcp_tool import execute_mcp_tool
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
        connection_id=tool.connection_id,
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
    await usage.require_tool_slot(session, ctx.org)
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
    elif data.type == "api":
        _require_tool_name(data.name)
        await _check_api_config(session, ctx, config, data.connection_id)
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

    if data.connection_id is not None and data.type != "api":
        raise AppError("tools.connection_not_applicable", "Only API tools use a connection.", 400)
    tool = Tool(
        organization_id=ctx.org.id,
        agent_id=data.agent_id,
        connection_id=data.connection_id,
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


_TOOL_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


def _require_tool_name(name: str) -> None:
    """Providers reject a function name outside this set, which fails the whole turn - so catch it at save."""
    if not _TOOL_NAME.match(name):
        raise AppError(
            "tools.bad_name",
            "An API tool's name must start with a letter and use only letters, digits, '_' or '-' (max 64).",
            400,
        )


async def _check_api_config(
    session: AsyncSession, ctx: OrgContext, config: dict[str, Any], connection_id: uuid.UUID | None
) -> None:
    """Validate an API tool's config at save time, so a typo fails here and not on the first live call."""
    method = str(config.get("method", "GET")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise AppError("tools.bad_method", f"Unsupported method '{method}'.", 400)
    if connection_id is not None:
        conn = await session.get(Connection, connection_id)
        if conn is None or conn.organization_id != ctx.org.id:
            raise AppError("connections.not_found", "Connection not found.", 404)
        if not config.get("path") and not config.get("url"):
            config.setdefault("path", "")
    elif not config.get("url"):
        raise AppError("tools.url_required", "An API tool without a connection needs a config.url.", 400)
    url = config.get("url")
    if url and api_tool.template_has_host_placeholder(str(url)):
        raise AppError("tools.url_host_template", "The host of a tool URL cannot contain placeholders.", 400)
    pick = (config.get("response") or {}).get("pick")
    if pick is not None and (problem := api_tool.validate_selectors(pick)):
        raise AppError("tools.bad_response_pick", problem, 400)
    body_type = config.get("body_type")
    if body_type is not None and body_type not in ("json", "form", "raw"):
        raise AppError("tools.bad_body_type", "body_type must be json, form or raw.", 400)


async def list_tools(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID | None) -> list[schemas.ToolOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(Tool).where(Tool.organization_id == ctx.org.id).order_by(Tool.created_at.desc())
    if agent_id is not None:
        stmt = stmt.join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id).where(
            AgentToolBinding.agent_id == agent_id, AgentToolBinding.organization_id == ctx.org.id
        )
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
    if "connection_id" in data.model_fields_set:
        if tool.type != "api":
            raise AppError("tools.connection_not_applicable", "Only API tools use a connection.", 400)
        if data.connection_id is not None:
            current = data.config if data.config is not None else tool.config
            await _check_api_config(session, ctx, dict(current), data.connection_id)
        tool.connection_id = data.connection_id
    if data.config is not None:
        if tool.type == "n8n":
            # An n8n tool's target is fixed at bind time, after the visibility, signature and ownership
            # checks. Letting an org rewrite `webhook_url`/`workflow_id` here would bypass all of them.
            # Only the call mode may change; to point at another workflow, bind a new tool.
            if any(v != tool.config.get(k) for k, v in data.config.items() if k != "mode"):
                raise AppError(
                    "tools.n8n_config_locked",
                    "An n8n tool's workflow cannot be changed. Bind the other workflow as a new tool.",
                    400,
                )
            if "mode" in data.config:
                mode = str(data.config["mode"]).lower()
                if mode not in ("sync", "async"):
                    raise AppError("tools.n8n_bad_mode", "mode must be 'sync' or 'async'.", 400)
                tool.config = {**tool.config, "mode": mode}
        else:
            if tool.type == "api":
                new_config = dict(data.config)
                await _check_api_config(session, ctx, new_config, tool.connection_id)
                tool.config = new_config
            else:
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
    # Which tools an agent may use is whatever is bound to it (docs/28 T1.5), in the owner's order. A tool
    # created for an agent is bound to it on insert, so this is the same set the old `Tool.agent_id` filter gave.
    stmt = (
        select(Tool, AgentToolBinding)
        .join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id)
        .where(
            Tool.organization_id == org_id,
            AgentToolBinding.organization_id == org_id,
            AgentToolBinding.agent_id == agent_id,
            AgentToolBinding.enabled.is_(True),
            Tool.enabled.is_(True),
        )
        .order_by(AgentToolBinding.position, Tool.created_at, Tool.id)
    )
    if not include_mcp:
        # Rows an agent's MCP binding created are always offered; the older hand-made ones stay behind the flag.
        stmt = stmt.where(or_(Tool.type != "mcp", Tool.config["managed"].as_boolean().is_(True)))
    rows = list((await session.execute(stmt)).all())
    rows = await _drop_unusable_mcp(session, org_id, rows)
    tools = [t for t, _b in rows]
    overrides = {t.name: (b.overrides or {}) for t, b in rows}
    specs = [
        ToolSpec(
            name=t.name,
            description=compact_description(overrides[t.name].get("description") or t.description),
            # n8n schemas are repaired on read so tools bound before the fix stop
            # breaking turns without needing a data migration.
            parameters=compact_schema(
                relax_n8n_schema(t.input_schema) if t.type == "n8n" else (t.input_schema or {})
            ),
        )
        for t in tools
    ]
    by_name: dict[str, Tool] = {}
    for t in tools:
        by_name.setdefault(t.name, t)  # the first of two same-named tools wins, as the model sees only one
    return specs, by_name


async def _drop_unusable_mcp(
    session: AsyncSession, org_id: uuid.UUID, rows: list[Any]
) -> list[Any]:
    """Managed MCP tools whose server is switched off, or whose plan no longer includes MCP, are not offered:
    the model should not be told about a tool the dispatcher will refuse."""
    server_ids = {
        uuid.UUID(str(t.config["server_id"])) for t, _b in rows if is_managed_mcp(t) and t.config.get("server_id")
    }
    if not server_ids:
        return rows
    on = set(
        (
            await session.execute(
                select(MCPServer.id).where(
                    MCPServer.id.in_(server_ids), MCPServer.organization_id == org_id, MCPServer.enabled.is_(True)
                )
            )
        ).scalars()
    )
    org = await session.get(Organization, org_id)
    plan_ok = org is None or usage.mcp_allowed(org)
    return [
        (t, b)
        for t, b in rows
        if not is_managed_mcp(t) or (plan_ok and uuid.UUID(str(t.config.get("server_id"))) in on)
    ]


async def build_tooling(
    session: AsyncSession,
    org_id: uuid.UUID,
    agent: Agent,
    version: AgentVersion,
    conversation_id: uuid.UUID | None,
    *,
    include_mcp: bool = False,
    outbound_media: list[uuid.UUID] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    block_flags: dict[str, bool] | None = None,
) -> tuple[list[ToolSpec], Any]:
    """Return (ToolSpecs, executor) for an agent's enabled tools, or ([], None) when disabled.

    Takes `org_id` (not an OrgContext) so the public/widget chat can reuse it. `executor(call)`
    runs the tool and returns a plain dict {output, status, error} (the runtime stays decoupled
    from the tools package). `include_mcp` — see `resolve_agent_tools`.

    `outbound_media` is the list a channel reply collects product photos in; only channels that can
    send images pass it, so only they are offered `send_product_images` (docs/26 §M4).

    `blocks` + `block_flags` (docs/27 Phase 2): the widget passes the list its turn collects interactive blocks
    in and the agent's `interactiveBlocks` settings. Both default to None, which is exactly today's behaviour:
    no block tool is registered and nothing about the request changes.
    """
    features = version.features or {}
    specs: list[ToolSpec] = []
    by_name: dict[str, Tool] = {}
    if features.get("tools_enabled"):
        # Runtime entitlement check, independent of the CRUD gates above: an agent that somehow has
        # tools attached (created before a downgrade, imported, or by a bug) must still not run
        # them on a plan that excludes tool calling (docs/18 §9).
        org = await session.get(Organization, org_id)
        if org is not None and not usage.feature_allowed(org, "tool_calling"):
            log.info("tooling_skipped_plan", org_id=str(org_id), agent_id=str(agent.id))
        else:
            specs, by_name = await resolve_agent_tools(session, org_id, agent.id, include_mcp=include_mcp)
            if org is not None and not usage.feature_allowed(org, "n8n"):
                by_name = {k: t for k, t in by_name.items() if t.type != "n8n"}
                specs = [sp for sp in specs if sp.name in by_name]

    # Catalog tools are not `Tool` rows: they are not attached by hand, do not count toward the plan's tool
    # limit, and are offered purely by the agent's "use product catalog" switch. A tool the owner named the
    # same keeps precedence.
    catalog_names: set[str] = set()
    if catalog_enabled(version):
        for name, tool in CATALOG_TOOLS.items():
            if name in by_name or (name == "send_product_images" and outbound_media is None):
                continue
            catalog_names.add(name)
            specs.append(ToolSpec(name=name, description=tool.description, parameters=tool.parameters))
    block_names: set[str] = set()
    if blocks is not None:
        for name, block_tool in chat_blocks.tools_for(block_flags).items():
            if name in by_name or name in catalog_names:
                continue
            block_names.add(name)
            # Same compaction as the owner's tools (ADR-125): the server re-validates every call anyway.
            specs.append(
                ToolSpec(
                    name=name,
                    description=compact_description(block_tool.description),
                    parameters=compact_schema(block_tool.parameters),
                )
            )
    if not specs:
        return [], None

    tool_ctx = ToolContext(
        session=session,
        org_id=org_id,
        agent_id=agent.id,
        version=version,
        conversation_id=conversation_id,
        outbound_media=outbound_media,
        blocks=blocks if block_names else None,
        bound_tool_ids=frozenset(t.id for t in by_name.values()),
    )

    async def executor(call: ToolCall) -> dict[str, Any]:
        if call.name in block_names:
            block_res = await block_tools.run_block_tool(tool_ctx, call.name, call.arguments)
            return {"output": block_res.output, "status": block_res.status, "error": block_res.error}
        if call.name in catalog_names:
            catalog_res = await CATALOG_TOOLS[call.name].run(tool_ctx, call.arguments)
            return {"output": catalog_res.output, "status": catalog_res.status, "error": catalog_res.error}
        res = await execute_tool_call(session, tool_ctx, by_name, call)
        return {"output": res.output, "status": res.status, "error": res.error}

    return specs, executor


async def _dispatch(tool: Tool, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if tool.type == "builtin":
        builtin = BUILTINS.get(tool.name)
        if builtin is None:
            return ToolResult(output={}, status="error", error=f"unknown built-in '{tool.name}'")
        return await builtin.run(ctx, args)
    if tool.type in ("api", "http"):
        connection = None
        if tool.connection_id is not None:
            connection = await load_connection(ctx.session, tool.organization_id, tool.connection_id)
            if connection is None:
                return ToolResult(output={}, status="error", error="this tool's connection no longer exists")
        return await api_tool.execute_api_tool(
            tool.config,
            args,
            connection=connection,
            input_schema=tool.input_schema if tool.type == "api" else None,
            legacy=tool.type == "http",
        )
    if tool.type == "n8n":
        return await execute_n8n_tool(tool.config, args, ctx)
    if tool.type == "mcp":
        if is_managed_mcp(tool):
            org = await ctx.session.get(Organization, tool.organization_id)
            if org is not None and not usage.mcp_allowed(org):
                return ToolResult(output={}, status="error", error="MCP is not included in this plan")
            problems = api_tool.validate_args(tool.input_schema, args)
            if problems:
                return ToolResult(
                    output={"error": "invalid_arguments", "details": problems},
                    status="error",
                    error="invalid arguments: " + "; ".join(problems),
                )
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
    # Defence in depth: the tool list was already built per org + agent, so this should never fire. If a
    # future code path ever hands the runtime another tenant's tool, refuse and leave a security trail.
    foreign_agent = tool.agent_id is not None and tool.agent_id != ctx.agent_id
    if ctx.bound_tool_ids is not None and tool.id in ctx.bound_tool_ids:
        foreign_agent = False  # shared across agents on purpose (docs/28 T1.5): resolution proved the binding
    if tool.organization_id != ctx.org_id or foreign_agent:
        log.warning(
            "tool_ownership_violation",
            tool_id=str(tool.id),
            tool_org=str(tool.organization_id),
            ctx_org=str(ctx.org_id),
            tool_agent=str(tool.agent_id),
            ctx_agent=str(ctx.agent_id),
        )
        return ToolResult(output={}, status="error", error=f"tool '{call.name}' is not available")
    if tool.type == "n8n" and settings.n8n_require_registered_automation:
        blocked = await automation_rules.check_agent_call(
            session, ctx.org_id, ctx.agent_id, str(tool.config.get("webhook_url", ""))
        )
        if blocked is not None:
            log.warning("n8n_call_blocked", tool_id=str(tool.id), org=str(ctx.org_id), agent=str(ctx.agent_id))
            return ToolResult(output={}, status="error", error=blocked)
    if tool.type == "n8n" and await _webhook_bound_to_other_org(session, tool.organization_id, tool.config):
        log.warning("n8n_webhook_cross_org", tool_id=str(tool.id), org=str(tool.organization_id))
        return ToolResult(output={}, status="error", error="This automation is not available.")

    run = ToolRun(
        organization_id=tool.organization_id,
        tool_id=tool.id,
        conversation_id=ctx.conversation_id,
        agent_id=ctx.agent_id,
        provider=_provider_of(tool),
        input=call.arguments,
        status="pending",
    )
    session.add(run)
    await session.flush()
    ctx.run_id = run.id
    if tool.type == "n8n":
        # n8n calls Vicero back while this call is still in flight (`/internal/tools/*`, the async callback) from
        # its own connection, and a row that is only flushed is invisible to it: every such call got
        # `bookings.bad_run` (401) until the turn committed. So make the run, and the conversation and message
        # it hangs off, durable BEFORE the webhook goes out. (A separate session cannot do this: the run's
        # foreign key to the conversation needs that row committed first.)
        await session.commit()

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
    run.attempt = result.attempt
    run.request_sanitized = result.request
    from app.webhooks.dispatch import emit_event

    await emit_event(
        session,
        tool.organization_id,
        "tool.run",
        {"tool": tool.name, "status": result.status, "run_id": str(run.id)},
    )
    return result


def _provider_of(tool: Tool) -> str:
    return "api" if tool.type in ("api", "http") else tool.type


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
            agent_id=tool.agent_id,
            provider=_provider_of(tool),
            input=data.input,
            output=result.output,
            status=result.status,
            latency_ms=latency_ms,
            error=result.error,
            attempt=result.attempt,
            request_sanitized=result.request,
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

    Vicero signs every request to an n8n webhook, but n8n only checks that signature if the
    workflow says to — a hand-built or pre-fix workflow accepts a bare `curl` from anyone with
    its URL, bypassing the agent, RBAC and budgets. `workflow` is what we already fetched when
    binding by id; for a pasted URL it is None and we resolve it, because **the URL is what
    Vicero will actually call**, whatever `workflow_id` accompanied it. A URL that cannot be
    resolved (no `N8N_API_KEY`, n8n down, a workflow on some other n8n) cannot be verified, so it
    is refused rather than trusted.
    """
    if workflow is None:
        try:
            workflow = await client.find_workflow_by_webhook_url(webhook_url)
        except AppError as exc:
            raise AppError(
                "tools.n8n_unverifiable",
                f"Could not check that this webhook verifies Vicero's signature ({exc.message}). "
                "Bind by workflow instead, or set N8N_API_KEY so Vicero can inspect it.",
                400,
            ) from exc
        if workflow is None:
            raise AppError(
                "tools.n8n_unverifiable",
                "No workflow on Vicero's n8n serves that webhook URL, so Vicero cannot check "
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

    if settings.n8n_require_registered_automation:
        # Only a workflow staff registered as an Active automation of THIS org (and this agent) can be bound.
        await automation_rules.require_registered_for_bind(session, ctx.org.id, data.agent_id, webhook_url)
    if await _webhook_bound_to_other_org(session, ctx.org.id, {"webhook_url": webhook_url}):
        # Same message as a hidden workflow: do not reveal that another org uses it.
        raise AppError("tools.n8n_forbidden", "This workflow is not available to your organization.", 403)

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


# An async workflow gets this long to call back; later callbacks are refused (replay / stale).
N8N_CALLBACK_MAX_AGE = dt.timedelta(minutes=10)
N8N_CALLBACK_MAX_OUTPUT_BYTES = 64 * 1024


def _webhook_path(config: dict[str, Any]) -> str | None:
    path = urlparse(str(config.get("webhook_url", ""))).path.rstrip("/")
    return path if path.startswith("/webhook/") else None


async def _webhook_bound_to_other_org(session: AsyncSession, org_id: uuid.UUID, config: dict[str, Any]) -> bool:
    """True if another organization already has a tool on the same n8n webhook path.

    One n8n serves every org, so a webhook path is single-tenant. Matched on the path (not the host):
    the host is rewritten to Vicero's own n8n when the call is made (`canonical_webhook_url`).
    """
    path = _webhook_path(config)
    if path is None:
        return False
    stmt = select(Tool.config).where(Tool.type == "n8n", Tool.organization_id != org_id)
    return any(_webhook_path(other or {}) == path for other in (await session.execute(stmt)).scalars().all())


def _reject_callback(reason: str, run_id: uuid.UUID) -> AppError:
    log.warning("n8n_callback_rejected", reason=reason, run_id=str(run_id))
    # One generic answer for every reason: the caller learns nothing about which runs exist.
    return AppError("n8n.callback_rejected", "Callback rejected.", 404)


async def resolve_n8n_callback(session: AsyncSession, data: schemas.N8nCallbackRequest) -> bool:
    """Resolve a pending async n8n tool run from a callback whose transport signature is already verified.

    Accepted only if ALL hold: the run exists and is an n8n run; the per-call token matches THIS run and
    its org; the run is still ``pending`` (a run resolves once); it is younger than 10 minutes; the output
    is small. Anything else is refused with one generic 404 and a security log line (reason, run id).
    """
    run = await session.get(ToolRun, data.run_id)
    if run is None:
        raise _reject_callback("unknown_run", data.run_id)
    tool = await session.get(Tool, run.tool_id)
    if tool is None or tool.type != "n8n" or tool.organization_id != run.organization_id:
        raise _reject_callback("not_an_n8n_run", data.run_id)
    if not valid_callback_token(data.callback_token, run.id, run.organization_id):
        raise _reject_callback("bad_token", data.run_id)
    if run.status != "pending":
        raise _reject_callback("not_pending", data.run_id)
    if dt.datetime.now(tz=dt.UTC) - run.created_at > N8N_CALLBACK_MAX_AGE:
        raise _reject_callback("expired", data.run_id)
    if len(json.dumps(data.output, default=str)) > N8N_CALLBACK_MAX_OUTPUT_BYTES:
        raise _reject_callback("output_too_large", data.run_id)
    run.output = data.output
    run.status = data.status
    run.error = data.error
    return True


# ── Agent tool bindings (docs/28 T1.5) ────────────────────────────────────────────────
async def _get_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.organization_id != ctx.org.id:
        raise AppError("agents.not_found", "Agent not found.", 404)
    return agent


async def list_agent_tools(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID
) -> list[schemas.ToolBindingOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_agent(session, ctx, agent_id)
    stmt = (
        select(Tool, AgentToolBinding)
        .join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id)
        .where(AgentToolBinding.agent_id == agent_id, AgentToolBinding.organization_id == ctx.org.id)
        .order_by(AgentToolBinding.position, Tool.created_at, Tool.id)
    )
    return [
        schemas.ToolBindingOut(
            tool_id=t.id,
            name=t.name,
            type=t.type,
            description=t.description,
            enabled=b.enabled,
            position=b.position,
            overrides=b.overrides or {},
        )
        for t, b in (await session.execute(stmt)).all()
    ]


async def set_agent_tools(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.SetAgentToolsRequest
) -> list[schemas.ToolBindingOut]:
    """Replace the agent's tool set and order in one call. Tools not listed are unbound (not deleted)."""
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    await _get_agent(session, ctx, agent_id)
    ids = [b.tool_id for b in data.tools]
    if len(set(ids)) != len(ids):
        raise AppError("tools.duplicate_binding", "A tool can be listed only once.", 400)
    mcp_rows = (
        await session.execute(
            select(AgentToolBinding, Tool)
            .join(Tool, Tool.id == AgentToolBinding.tool_id)
            .where(AgentToolBinding.agent_id == agent_id, AgentToolBinding.organization_id == ctx.org.id)
        )
    ).all()
    managed_ids = {t.id for _b, t in mcp_rows if is_managed_mcp(t)}
    managed_enabled = sum(1 for b, t in mcp_rows if t.id in managed_ids and b.enabled)
    if sum(1 for b in data.tools if b.enabled) + managed_enabled > MAX_TOOLS_PER_AGENT:
        raise AppError(
            "tools.too_many",
            f"An agent can have at most {MAX_TOOLS_PER_AGENT} enabled tools. "
            "Split the work across agents or workflows.",
            400,
        )
    found = {}
    if ids:
        rows = await session.execute(select(Tool).where(Tool.id.in_(ids), Tool.organization_id == ctx.org.id))
        found = {t.id: t for t in rows.scalars().all()}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise AppError("tools.not_found", "Tool not found.", 404, details={"tool_ids": missing})
    if any(is_managed_mcp(t) for t in found.values()):
        raise AppError(
            "tools.managed_binding",
            "MCP tools are chosen per server with PUT /v1/agents/{id}/mcp-servers, not here.",
            400,
        )
    for b in data.tools:
        extra = set(b.overrides) - {"description"}
        if extra:
            raise AppError("tools.bad_override", f"Unsupported override(s): {', '.join(sorted(extra))}.", 400)
        if "description" in b.overrides and not isinstance(b.overrides["description"], str):
            raise AppError("tools.bad_override", "The description override must be text.", 400)

    existing = {
        b.tool_id: b
        for b in (
            await session.execute(
                select(AgentToolBinding).where(
                    AgentToolBinding.agent_id == agent_id, AgentToolBinding.organization_id == ctx.org.id
                )
            )
        )
        .scalars()
        .all()
    }
    for tool_id, stale in existing.items():
        if tool_id not in found and tool_id not in managed_ids:  # MCP bindings belong to /mcp-servers
            await session.delete(stale)
    for position, b in enumerate(data.tools):
        row = existing.get(b.tool_id)
        if row is None:
            session.add(
                AgentToolBinding(
                    organization_id=ctx.org.id,
                    agent_id=agent_id,
                    tool_id=b.tool_id,
                    enabled=b.enabled,
                    overrides=b.overrides,
                    position=position,
                )
            )
        else:
            row.enabled, row.overrides, row.position = b.enabled, b.overrides, position
    await session.flush()
    await write_audit(
        session, ctx.org.id, ctx.user.id, "agent.tools_set",
        target_type="agent", target_id=str(agent_id), meta={"tool_ids": [str(i) for i in ids]},
    )
    return await list_agent_tools(session, ctx, agent_id)


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
            agent_id=r.agent_id,
            provider=r.provider,
            request_sanitized=r.request_sanitized,
            attempt=r.attempt,
        )
        for r in (await session.execute(stmt)).scalars().all()
    ]
