"""Tool calls that finish after the turn that started them (docs/28 T4.3, T4.4, ADR-135).

*Background tools* (`mode="async"`): the model gets "started" at once, a Celery task makes the call, and when it
lands the agent says how it went. *Approval*: a call that needs a person's yes parks as a request in the Inbox (a
`Handoff` with `requested_by="tool"`, reusing the existing primitive); the model is told it is pending and carries
on. A decision runs the stored call, or marks it declined, and the agent reports the outcome.

Neither blocks the conversation: the visitor can keep chatting while a request is open.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.types import ToolCall
from app.models import Agent, AgentVersion, Handoff, Tool, ToolRun
from app.realtime.hub import hub, inbox_topic
from app.tools.base import ToolContext, ToolResult
from app.webhooks.dispatch import sanitize_payload

log = get_logger("tools.deferred")

APPROVAL_REASON = "tool_approval"


def _service() -> Any:
    from app.tools import service

    return service


# ── Approval ─────────────────────────────────────────────────────────────────────
async def request_approval(session: AsyncSession, ctx: ToolContext, tool: Tool, call: ToolCall) -> ToolResult:
    """Park the call as a request in the Inbox. The model is told it is pending, not that it happened."""
    if ctx.conversation_id is None:
        # Nobody to ask on behalf of (a workflow tool node, a bare test): fail closed rather than run it.
        return ToolResult(
            output={"error": "approval_required", "message": "This action needs a person's approval first."},
            status="error",
            error="approval required",
        )
    svc = _service()
    run = svc.new_run(session, ctx, tool, call.arguments, status="pending_approval")
    run.output = {"status": "pending_approval"}
    await session.flush()
    handoff = Handoff(
        organization_id=tool.organization_id,
        conversation_id=ctx.conversation_id,
        requested_by="tool",
        reason=APPROVAL_REASON,
        status="open",
        payload={
            "tool_id": str(tool.id),
            "tool": tool.name,
            "run_id": str(run.id),
            "agent_id": str(ctx.agent_id),
            "args": sanitize_payload(call.arguments),
        },
    )
    session.add(handoff)
    await session.flush()
    await hub.publish(
        inbox_topic(tool.organization_id),
        {"type": "tool_approval.created", "handoff_id": str(handoff.id), "tool": tool.name},
    )
    return ToolResult(
        output={
            "status": "pending_approval",
            "message": (
                "This needs a teammate's approval before it happens. Tell the customer you have asked and that "
                "they will get a message here once it is decided. Do not say it is done."
            ),
        },
        status="success",
        deferred="pending_approval",
    )


# ── Background tools ───────────────────────────────────────────────────────────────
async def start_async(session: AsyncSession, ctx: ToolContext, tool: Tool, call: ToolCall) -> ToolResult:
    """Record the call, hand it to a worker, and let the turn go on."""
    svc = _service()
    run = svc.new_run(session, ctx, tool, call.arguments, status="pending")
    run.output = {"status": "started"}
    await session.flush()
    ctx.run_id = run.id
    # The worker reads the run from its own connection, and it hangs off a conversation and message that are only
    # flushed so far, so make them durable before the task can start (same reason n8n commits before its webhook).
    await session.commit()
    enqueue_async_run(run.id)
    return ToolResult(
        output={
            "status": "started",
            "message": (
                "The action is running in the background. Tell the customer it is in progress and that the "
                "result will arrive in this chat. Do not say it has finished."
            ),
        },
        status="success",
        deferred="pending",
    )


def enqueue_async_run(run_id: uuid.UUID) -> None:
    try:
        from app.worker.tasks import run_async_tool_task

        run_async_tool_task.delay(str(run_id))
    except Exception as exc:  # broker down: the run stays `pending` and shows as such in the log
        log.warning("async_tool_enqueue_failed", run=str(run_id), error=str(exc))


def enqueue_followup(run_id: uuid.UUID) -> None:
    try:
        from app.worker.tasks import tool_followup_task

        tool_followup_task.delay(str(run_id))
    except Exception as exc:
        log.warning("followup_enqueue_failed", run=str(run_id), error=str(exc))


async def context_for_run(session: AsyncSession, run: ToolRun) -> ToolContext | None:
    """A tool context for finishing a stored call outside any request: the agent's published version, falling back
    to its newest."""
    if run.agent_id is None:
        return None
    agent = await session.get(Agent, run.agent_id)
    if agent is None or agent.organization_id != run.organization_id:
        return None
    version = await session.get(AgentVersion, agent.current_version_id) if agent.current_version_id else None
    if version is None:
        version = (
            await session.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if version is None:
        return None
    return ToolContext(
        session=session,
        org_id=run.organization_id,
        agent_id=agent.id,
        version=version,
        conversation_id=run.conversation_id,
        run_id=run.id,
    )


async def _finish_run(session: AsyncSession, run: ToolRun, tool: Tool) -> ToolResult:
    """Make the stored call and write the outcome onto its run."""
    svc = _service()
    ctx = await context_for_run(session, run)
    if ctx is None:
        result = ToolResult(output={}, status="error", error="the agent for this action no longer exists")
        started = time.perf_counter()
        run.output, run.status, run.error = result.output, "error", result.error
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        return result
    started = time.perf_counter()
    try:
        result = await svc._dispatch(tool, ctx, dict(run.input or {}))
    except Exception as exc:  # a tool must never crash the worker
        result = ToolResult(output={}, status="error", error=str(exc))
        log.warning("deferred_tool_error", tool=tool.name, error=str(exc))
    await svc.finalize_run(
        session, ctx, tool, run, result, dict(run.input or {}), int((time.perf_counter() - started) * 1000)
    )
    return result


async def run_async_tool(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """The worker half of a background tool: make the call, then tell the visitor. Idempotent on a finished run."""
    run = await session.get(ToolRun, run_id)
    if run is None or run.status != "pending":
        return False
    tool = await session.get(Tool, run.tool_id)
    if tool is None:
        return False
    await _finish_run(session, run, tool)
    await session.commit()  # the outcome must be durable before the follow-up reads it
    from app.chat.followup import generate_followup

    sent = await generate_followup(session, run.id)
    await session.commit()
    return sent


# ── Deciding an approval ───────────────────────────────────────────────────────────
async def get_open_approval(session: AsyncSession, org_id: uuid.UUID, handoff_id: uuid.UUID) -> Handoff:
    handoff = await session.get(Handoff, handoff_id)
    if handoff is None or handoff.organization_id != org_id or handoff.requested_by != "tool":
        raise AppError("inbox.tool_approval_not_found", "Approval request not found.", 404)
    return handoff


async def decide(
    session: AsyncSession, handoff: Handoff, *, approve: bool, decided_by: uuid.UUID
) -> tuple[Handoff, ToolRun | None]:
    """Resolve a request. Approving runs the stored call now; declining records that it will not happen. Either way
    the agent is asked to report back (queued; the caller commits first)."""
    if handoff.status == "resolved":
        raise AppError("inbox.tool_approval_already_resolved", "This request was already decided.", 400)
    payload = handoff.payload or {}
    run = await session.get(ToolRun, uuid.UUID(str(payload.get("run_id")))) if payload.get("run_id") else None
    tool = await session.get(Tool, run.tool_id) if run is not None else None
    now = dt.datetime.now(tz=dt.UTC)
    handoff.status, handoff.resolved_at, handoff.assigned_to = "resolved", now, decided_by
    handoff.notes = [
        *list(handoff.notes or []),
        {"by": str(decided_by), "decision": "approved" if approve else "rejected", "at": now.isoformat()},
    ]
    if run is None or tool is None or run.status != "pending_approval":
        await session.flush()
        return handoff, run
    if approve:
        await _finish_run(session, run, tool)
    else:
        run.status, run.output, run.error = (
            "declined",
            {"status": "declined", "message": "A teammate declined this."},
            None,
        )
    await session.flush()
    await hub.publish(
        inbox_topic(handoff.organization_id), {"type": "tool_approval.resolved", "handoff_id": str(handoff.id)}
    )
    return handoff, run
