"""Workflow CRUD, versioning, and execution (docs/17 Phase 2).

Execution runs on Celery in production (docs/17 Phase 2 item 2): `run_workflow_now` /
`resume_workflow_run` create/update the `WorkflowRun` row, then `_dispatch_run` either enqueues
`app.worker.tasks.run_workflow_task` / `resume_workflow_run_task` — which call
`execute_queued_run` on the worker's own DB session — or, when `settings.celery_task_always_eager`
is set, run the SAME execution coroutine in-process instead of going through Celery at all. That
in-process branch exists for the same reason `app.core.email.queue_email` has one: every task in
`app.worker.tasks` drives its coroutine with `asyncio.run()`, which raises inside a loop that is
already running — exactly what a request handler's event loop is. Going through Celery's own
"eager" machinery here would hit that trap; bypassing it entirely does not. See CLAUDE.md §12.

The `WorkflowRun` row is the resumability boundary regardless of path — `variables`/`budget`/
`current_node_id` are always read back from the DB, never kept in Python process memory across a
pause, which is what makes "which process runs this" a detail the execution logic itself doesn't
need to know about.

Every run gets a budget from `app.chat.budget.default_budget()` unconditionally — unlike
Phase 1's dual platform+org flag (which had to gate a change to every existing agent's default
chat behavior), a workflow is a brand-new object type nobody has until an org with
`WORKFLOWS_WRITE` creates one, so RBAC is already the opt-in gate; a second rollout flag would
be redundant. See ADR-074.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.chat import variables as chat_variables
from app.chat.assembly import compose_system_prompt
from app.chat.budget import AgentBudget, default_budget
from app.chat.runtime import TurnResult, run_turn
from app.core import rbac
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.registry import get_chat_provider, get_chat_provider_chain
from app.llm.types import ChatRequest, Message, ToolCall
from app.models import (
    Agent,
    AgentVersion,
    Handoff,
    Organization,
    Workflow,
    WorkflowRun,
    WorkflowStep,
    WorkflowVersion,
)
from app.modules.orgs.deps import OrgContext
from app.realtime.hub import hub, inbox_topic
from app.tools.base import ToolContext
from app.tools.service import execute_tool_call, resolve_agent_tools
from app.workflows import schemas
from app.workflows.diff import diff_graphs
from app.workflows.graph import (
    WorkflowAgentExecutor,
    WorkflowRunResult,
    WorkflowSubExecutor,
    run_workflow,
    validate_graph,
)

log = get_logger("workflows")


# ── Internals ────────────────────────────────────────────────────────────────────────────


async def _get_workflow(session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID) -> Workflow:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None or workflow.organization_id != ctx.org.id or workflow.deleted_at is not None:
        raise AppError("workflows.not_found", "Workflow not found.", 404)
    return workflow


async def _get_version(
    session: AsyncSession, workflow_id: uuid.UUID, version: int
) -> WorkflowVersion:
    stmt = select(WorkflowVersion).where(
        WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.version == version
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AppError("workflows.version_not_found", "Workflow version not found.", 404)
    return row


async def _get_run(session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID) -> WorkflowRun:
    run = await session.get(WorkflowRun, run_id)
    if run is None or run.organization_id != ctx.org.id:
        raise AppError("workflows.run_not_found", "Workflow run not found.", 404)
    return run


async def _latest_workflow_version(session: AsyncSession, workflow_id: uuid.UUID) -> WorkflowVersion:
    """The version a "Test run" exercises — draft or published, whichever was created most
    recently. Mirrors `app.modules.agents.service._latest_version`'s semantics for the Agent
    Playground exactly: an operator testing wants to see what they just saved, not necessarily
    what's live."""
    stmt = (
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    )
    version = (await session.execute(stmt)).scalar_one_or_none()
    if version is None:
        raise AppError("workflows.no_version", "This workflow has no versions yet.", 400)
    return version


def _workflow_out(w: Workflow) -> schemas.WorkflowOut:
    return schemas.WorkflowOut(
        id=w.id, organization_id=w.organization_id, agent_id=w.agent_id, name=w.name,
        description=w.description, current_version_id=w.current_version_id,
        created_at=w.created_at, updated_at=w.updated_at,
    )


def _version_out(v: WorkflowVersion) -> schemas.WorkflowVersionOut:
    return schemas.WorkflowVersionOut(
        id=v.id, workflow_id=v.workflow_id, version=v.version, status=v.status,
        graph=v.graph, created_at=v.created_at,
    )


def _run_out(r: WorkflowRun) -> schemas.WorkflowRunOut:
    return schemas.WorkflowRunOut(
        id=r.id, workflow_version_id=r.workflow_version_id, status=r.status,
        current_node_id=r.current_node_id, variables=r.variables, error=r.error,
        started_at=r.started_at, completed_at=r.completed_at, is_test=r.is_test,
    )


def _budget_to_dict(b: AgentBudget) -> dict[str, Any]:
    return {
        "max_steps": b.max_steps, "max_tool_calls": b.max_tool_calls,
        "max_runtime_s": b.max_runtime_s, "max_cost_usd": b.max_cost_usd,
        "consumed_steps": b.consumed_steps, "consumed_tool_calls": b.consumed_tool_calls,
        "consumed_cost_usd": b.consumed_cost_usd, "tripped": b.tripped,
    }


def _budget_from_dict(d: dict[str, Any]) -> AgentBudget:
    """Reconstruct the budget a paused run left off with — resuming must inherit what was
    already spent (docs/17 §2 rule 3), not start a fresh ceiling."""
    b = default_budget()
    if not d:
        return b
    b.max_steps = d.get("max_steps", b.max_steps)
    b.max_tool_calls = d.get("max_tool_calls", b.max_tool_calls)
    b.max_runtime_s = d.get("max_runtime_s", b.max_runtime_s)
    b.max_cost_usd = d.get("max_cost_usd", b.max_cost_usd)
    b.consumed_steps = d.get("consumed_steps", 0)
    b.consumed_tool_calls = d.get("consumed_tool_calls", 0)
    b.consumed_cost_usd = d.get("consumed_cost_usd", 0.0)
    b.tripped = d.get("tripped")
    return b


async def _tool_executor_for(
    session: AsyncSession, org: Organization, agent_id: uuid.UUID | None
) -> Any:
    """A `WorkflowToolExecutor` over the same `Tool` rows chat's `run_turn` uses — reuses
    `execute_tool_call` rather than a second dispatch path. `None` when there is no agent to
    scope tools to, or the agent has no version yet — a tool node then fails cleanly per
    `graph.py`'s own handling rather than crashing on a `ToolContext.version` a builtin tool
    (e.g. `knowledge_search`, which reads `ctx.version.rag_config`) assumes is real.

    Takes `org: Organization`, not a full `OrgContext` — a Celery worker process has no HTTP
    request to build one from, only the `organization_id` a `WorkflowRun` row carries. The
    RBAC-checked callers (`run_workflow_now` etc.) still take `OrgContext`; only the pure
    execution layer was narrowed to what it actually uses.
    """
    if agent_id is None:
        return None
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.current_version_id is None:
        return None
    version = await session.get(AgentVersion, agent.current_version_id)
    if version is None:
        return None
    _specs, by_name = await resolve_agent_tools(session, org.id, agent_id, include_mcp=True)
    tool_ctx = ToolContext(session=session, org_id=org.id, agent_id=agent_id, version=version)

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        call = ToolCall(id=str(uuid.uuid4()), name=name, arguments=args)
        res = await execute_tool_call(session, tool_ctx, by_name, call)
        return {"output": res.output, "status": res.status, "error": res.error}

    return executor


async def _agent_executor_for(session: AsyncSession, org: Organization) -> WorkflowAgentExecutor:
    """An `agent` node's executor: runs an existing published `Agent` through the real
    agentic runtime (`app.chat.runtime.run_turn`), a single non-streaming user turn with no
    conversation persistence — the workflow's own `WorkflowStep` for this node is the record
    of what happened, not a second `Conversation`/`Message` pair. Passes the SAME budget
    instance into `run_turn` so a nested think→act→observe cycle decrements the workflow
    run's own ceiling (docs/17 §2 rule 3), and disables the output guard/PII allowlist the
    same way the Playground does — the caller here is another workflow node, not a visitor,
    and the untrusted-output rule is enforced by `graph.py`'s `neutralize_injections()` on the
    return value instead.
    """

    async def executor(agent_id_str: str, message: str, budget: AgentBudget) -> dict[str, Any]:
        try:
            agent_id = uuid.UUID(agent_id_str)
        except ValueError:
            return {"content": "", "status": "error", "error": f"invalid agent_id {agent_id_str!r}"}
        agent = await session.get(Agent, agent_id)
        if agent is None or agent.organization_id != org.id:
            return {"content": "", "status": "error", "error": "agent not found"}
        if agent.current_version_id is None:
            return {"content": "", "status": "error", "error": "agent has no published version"}
        version = await session.get(AgentVersion, agent.current_version_id)
        if version is None:
            return {"content": "", "status": "error", "error": "agent has no published version"}

        mc = version.model_config_json or {}
        provider_name = mc.get("provider", "fake")
        try:
            provider = await get_chat_provider_chain(
                session, org.id, mc, agent_id=agent.id, resolve=get_chat_provider
            )
        except AppError as exc:
            log.warning("workflow_agent_node_provider_unavailable", agent_id=str(agent.id), error=str(exc))
            return {"content": "", "status": "error", "error": str(exc)}

        system_prompt = compose_system_prompt(
            version.system_prompt, version.persona, agent_name=agent.name, business_name=org.name,
            variables=chat_variables.build_context(agent_name=agent.name, business_name=org.name),
        )
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))
        messages.append(Message(role="user", content=message))
        req = ChatRequest(
            model=mc.get("model", provider_name), messages=messages,
            temperature=mc.get("temperature", 0.7), top_p=mc.get("top_p", 1.0),
            max_tokens=mc.get("max_tokens", 1024),
            frequency_penalty=mc.get("frequency_penalty", 0.0), presence_penalty=mc.get("presence_penalty", 0.0),
            stop=mc.get("stop") or None, stream=False,
        )
        result = TurnResult()
        async for _ev in run_turn(provider, req, [], result, budget=budget, guard_output=False):
            pass
        return {
            "content": result.content,
            "status": "error" if result.error else "completed",
            "error": result.error,
        }

    return executor


async def _sub_workflow_executor_for(session: AsyncSession, org: Organization) -> WorkflowSubExecutor:
    """A `sub_agent` node's executor: runs another org-owned, published `Workflow` in-process,
    sharing the same budget (call depth included). Deliberately does not persist a separate
    `WorkflowRun` row for the nested execution — the parent's own `WorkflowStep` for the
    `sub_agent` node already records the sanitized outcome; a full nested audit trail is a
    follow-up, not built this slice.
    """

    async def executor(
        workflow_id_str: str, variables_snapshot: dict[str, Any], budget: AgentBudget
    ) -> WorkflowRunResult:
        try:
            workflow_id = uuid.UUID(workflow_id_str)
        except ValueError:
            return WorkflowRunResult(
                status="failed", variables=variables_snapshot, error=f"invalid workflow_id {workflow_id_str!r}"
            )
        workflow = await session.get(Workflow, workflow_id)
        if workflow is None or workflow.organization_id != org.id or workflow.deleted_at is not None:
            return WorkflowRunResult(status="failed", variables=variables_snapshot, error="sub-workflow not found")
        if workflow.current_version_id is None:
            return WorkflowRunResult(
                status="failed", variables=variables_snapshot, error="sub-workflow has no published version"
            )
        version = await session.get(WorkflowVersion, workflow.current_version_id)
        assert version is not None

        nested_tool_executor = await _tool_executor_for(session, org, workflow.agent_id)
        nested_agent_executor = await _agent_executor_for(session, org)
        return await run_workflow(
            version.graph, variables=variables_snapshot, budget=budget,
            tool_executor=nested_tool_executor, agent_executor=nested_agent_executor,
            # Passing itself allows a chain deeper than one level — bounded by
            # `budget.max_call_depth`, not by how many executor factories were pre-built.
            sub_workflow_executor=executor,
        )

    return executor


# ── Approval → Handoff/inbox (docs/17 Phase 2 item 4) ──────────────────────────────────────
# Approval nodes reuse the existing Handoff model rather than a parallel "workflow approval"
# concept — `status`/`assigned_to`/`notes`/`tags` all mean the same thing for a paused
# workflow as for a chat handoff. See the ADR in docs/DECISIONS.md for why, and
# `app.modules.inbox.service` for the operator-facing list/decide endpoints that read these.


async def _resolve_run_handoffs(session: AsyncSession, run_id: uuid.UUID, organization_id: uuid.UUID) -> None:
    """Resolves any still-open handoff for this run. A no-op (no publish) when there is
    nothing open — called unconditionally on every non-paused outcome and before dispatching a
    resume, so it must not spam the inbox topic when there was never anything to resolve."""
    stmt = select(Handoff).where(Handoff.workflow_run_id == run_id, Handoff.status != "resolved")
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return
    now = dt.datetime.now(tz=dt.UTC)
    for h in rows:
        h.status = "resolved"
        h.resolved_at = now
    await session.flush()
    await hub.publish(
        inbox_topic(organization_id),
        {"type": "workflow_approval.resolved", "workflow_run_id": str(run_id)},
    )


async def _create_approval_handoff(session: AsyncSession, run: WorkflowRun, result: WorkflowRunResult) -> None:
    message = None
    if result.steps:
        last = result.steps[-1]
        if last.get("status") == "awaiting_approval":
            message = (last.get("output") or {}).get("message")
    handoff = Handoff(
        organization_id=run.organization_id, conversation_id=run.conversation_id,
        workflow_run_id=run.id, requested_by="workflow",
        reason=message or "A workflow is waiting on your approval.", status="open",
    )
    session.add(handoff)
    await session.flush()
    await hub.publish(
        inbox_topic(run.organization_id),
        {"type": "workflow_approval.created", "workflow_run_id": str(run.id), "handoff_id": str(handoff.id)},
    )


async def _schedule_delay_resume(run: WorkflowRun, result: WorkflowRunResult) -> None:
    """Schedules the Celery task that wakes a `paused_delay` run back up (ADR-076) — a real
    `apply_async(eta=...)`, not a blocking sleep on any worker. A no-op under eager execution:
    `_run_delay` already refuses to pause there (no worker exists to wake up later), so this
    should not normally be reached in that mode, but the check stays as a defensive backstop
    rather than trusting that invariant silently.
    """
    if settings.celery_task_always_eager:
        return
    resume_at_raw = None
    if result.steps:
        last = result.steps[-1]
        if last.get("status") == "awaiting_delay":
            resume_at_raw = (last.get("output") or {}).get("resume_at")
    if not resume_at_raw:
        log.error("workflow_delay_missing_resume_at", run_id=str(run.id))
        return
    from app.worker.tasks import resume_delayed_workflow_task

    resume_at = dt.datetime.fromisoformat(resume_at_raw)
    resume_delayed_workflow_task.apply_async(args=[str(run.id)], eta=resume_at)


async def _persist_one_step(
    session: AsyncSession, run: WorkflowRun, step: dict[str, Any], *, commit: bool
) -> None:
    session.add(
        WorkflowStep(
            workflow_run_id=run.id, node_id=step["node_id"], node_type=step["node_type"],
            status=step["status"], input=step.get("input"), output=step.get("output"),
            latency_ms=step.get("latency_ms"), cost_usd=step.get("cost_usd"), error=step.get("error"),
        )
    )
    await session.flush()
    if commit:
        # Durable immediately — so `GET .../steps` polled from a DIFFERENT connection (the
        # API request serving that poll) sees progress on a long-running run while the Celery
        # task is still executing it, not only once the whole run finishes. Only ever true on
        # the worker's own dedicated session (see `execute_queued_run`) — never on a
        # request-scoped session, which the test harness shares across a whole test inside one
        # uncommitted transaction (`tests/conftest.py`); committing there would break that
        # isolation, which is exactly why `commit` is plumbed through rather than hardcoded.
        await session.commit()


async def _execute_and_persist(
    session: AsyncSession,
    run: WorkflowRun,
    workflow: Workflow,
    version: WorkflowVersion,
    *,
    resume_input: dict[str, Any] | None = None,
    commit_each_step: bool = False,
) -> WorkflowRunResult:
    """The one execution path both the in-process (eager) and Celery-task dispatch routes call
    — `_dispatch_run` decides WHICH process runs this, not what it does once it does.
    """
    org = await session.get(Organization, run.organization_id)
    assert org is not None
    # Runtime entitlement check (docs/18 §9): the CRUD endpoints refuse a trial org, but a run
    # can still be queued for one — a workflow created before a downgrade, a resume of an old
    # run, a Celery retry. Nothing executes on a plan that excludes workflows.
    if not usage.feature_allowed(org, "workflows"):
        run.status = "failed"
        run.error = "plan_limit: workflows are not included in this plan"
        run.completed_at = dt.datetime.now(tz=dt.UTC)
        await session.flush()
        return WorkflowRunResult(status="failed", variables=dict(run.variables or {}), error=run.error)
    budget = _budget_from_dict(run.budget)
    tool_executor = await _tool_executor_for(session, org, workflow.agent_id)
    agent_executor = await _agent_executor_for(session, org)
    sub_workflow_executor = await _sub_workflow_executor_for(session, org)

    async def on_step(step: dict[str, Any]) -> None:
        await _persist_one_step(session, run, step, commit=commit_each_step)

    result = await run_workflow(
        version.graph, variables=run.variables, budget=budget, tool_executor=tool_executor,
        agent_executor=agent_executor, sub_workflow_executor=sub_workflow_executor,
        start_node_id=run.current_node_id if resume_input is not None else None,
        resume_input=resume_input, on_step=on_step,
    )
    run.status = result.status
    run.current_node_id = result.current_node_id
    run.error = result.error
    run.budget = _budget_to_dict(budget)
    if result.status in ("completed", "failed", "budget_exceeded"):
        run.completed_at = dt.datetime.now(tz=dt.UTC)
    if result.status == "paused_approval":
        await _create_approval_handoff(session, run, result)
    else:
        # Also the correctness backstop for the resume path: `resume_workflow_run_unchecked`
        # already resolves the handoff being acted on before dispatch, but this covers a run
        # reaching a terminal state any other way, and is a no-op when there was nothing open.
        await _resolve_run_handoffs(session, run.id, run.organization_id)
    await session.flush()
    if commit_each_step:
        await session.commit()
    if result.status == "paused_delay":
        # After the commit (if any) — the scheduled task's own session must see this run's
        # `paused_delay` status as durable, not just flushed in a transaction that might not
        # have landed yet on whatever connection it reads from.
        await _schedule_delay_resume(run, result)
    return result


async def _dispatch_run(
    session: AsyncSession,
    run: WorkflowRun,
    workflow: Workflow,
    version: WorkflowVersion,
    *,
    resume_input: dict[str, Any] | None = None,
) -> None:
    """Runs `_execute_and_persist` in-process when Celery is in eager mode (see the module
    docstring for why that must NOT go through Celery's own eager machinery), otherwise
    enqueues a Celery task and returns immediately — the caller's `WorkflowRun` stays in
    `"running"` until that task updates it.
    """
    if settings.celery_task_always_eager:
        await _execute_and_persist(session, run, workflow, version, resume_input=resume_input)
        return
    # Deliberately no explicit commit before enqueueing here — matches the existing
    # `enqueue_document_ingestion` convention (flush, then `.delay()`) rather than introducing
    # a stricter guarantee only for workflows. A worker that picks up this task before the
    # enclosing request's `get_session()` commit lands would find no row; unobserved in
    # practice so far for ingestion, and the same trade-off applies here. See docs/PROGRESS.md.
    from app.worker.tasks import resume_workflow_run_task, run_workflow_task

    if resume_input is None:
        run_workflow_task.delay(str(run.id))
    else:
        resume_workflow_run_task.delay(str(run.id), resume_input.get("decision", "rejected"))


async def execute_queued_run(
    session: AsyncSession, run_id: uuid.UUID, *, resume_decision: str | None = None, resume: bool = False
) -> str:
    """Entry point for the Celery task (`app.worker.tasks.run_workflow_task` /
    `resume_workflow_run_task` / `resume_delayed_workflow_task`). No RBAC check and no
    `OrgContext` — the request that enqueued this already checked `WORKFLOWS_WRITE` (or, for a
    delay's self-scheduled wake-up, nothing needs re-checking — it's not a new user action),
    and a worker process has no HTTP request to build one from; this loads only what execution
    itself needs.

    `resume=True` with `resume_decision=None` is the delay-wake-up shape: resume from
    `run.current_node_id` with an empty (but non-`None`) `resume_input`, which `_run_delay`
    reads as "this is the wake-up, not the original visit" — it doesn't care what's in it,
    only that it's there. `resume_decision` set is the approval shape; neither set is a fresh
    run from the graph's start node.
    """
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        log.error("workflow_run_vanished", run_id=str(run_id))
        return "not_found"
    if resume and resume_decision is None and run.status != "paused_delay":
        # A scheduled delay-resume firing after the run was already moved on some other way
        # (an operator cancelled it while it was still waiting, most plausibly) must not revive
        # it — Celery has no way to un-schedule an already-queued `eta` task, so the guard has
        # to live here instead.
        log.info("workflow_delay_resume_skipped_stale", run_id=str(run_id), status=run.status)
        return run.status
    version = await session.get(WorkflowVersion, run.workflow_version_id)
    if version is None:
        run.status = "failed"
        run.error = "workflow version was deleted before this run could execute"
        run.completed_at = dt.datetime.now(tz=dt.UTC)
        return run.status
    workflow = await session.get(Workflow, version.workflow_id)
    if workflow is None:
        run.status = "failed"
        run.error = "workflow was deleted before this run could execute"
        run.completed_at = dt.datetime.now(tz=dt.UTC)
        return run.status
    if resume_decision is not None:
        resume_input: dict[str, Any] | None = {"decision": resume_decision}
    elif resume:
        resume_input = {}
    else:
        resume_input = None
    result = await _execute_and_persist(
        session, run, workflow, version, resume_input=resume_input, commit_each_step=True
    )
    return result.status


# ── Workflow CRUD ────────────────────────────────────────────────────────────────────────


async def create_workflow(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID | None, data: schemas.CreateWorkflowRequest
) -> schemas.WorkflowOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    if agent_id is not None:
        agent = await session.get(Agent, agent_id)
        if agent is None or agent.organization_id != ctx.org.id:
            raise AppError("workflows.agent_not_found", "Agent not found.", 404)
    workflow = Workflow(
        organization_id=ctx.org.id, agent_id=agent_id, name=data.name, description=data.description,
        created_by=ctx.user.id,
    )
    session.add(workflow)
    await session.flush()
    return _workflow_out(workflow)


async def list_workflows(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID | None
) -> list[schemas.WorkflowOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(Workflow).where(Workflow.organization_id == ctx.org.id, Workflow.deleted_at.is_(None))
    if agent_id is not None:
        stmt = stmt.where(Workflow.agent_id == agent_id)
    rows = (await session.execute(stmt.order_by(Workflow.created_at.desc()))).scalars().all()
    return [_workflow_out(w) for w in rows]


async def get_workflow(session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID) -> schemas.WorkflowOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return _workflow_out(await _get_workflow(session, ctx, workflow_id))


async def update_workflow(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, data: schemas.UpdateWorkflowRequest
) -> schemas.WorkflowOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    workflow = await _get_workflow(session, ctx, workflow_id)
    if data.name is not None:
        workflow.name = data.name
    if data.description is not None:
        workflow.description = data.description
    return _workflow_out(workflow)


async def delete_workflow(session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    workflow = await _get_workflow(session, ctx, workflow_id)

    workflow.deleted_at = dt.datetime.now(tz=dt.UTC)


# ── Versions ─────────────────────────────────────────────────────────────────────────────


async def create_version(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, data: schemas.CreateWorkflowVersionRequest
) -> schemas.WorkflowVersionOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    workflow = await _get_workflow(session, ctx, workflow_id)
    errors = validate_graph(data.graph)
    if errors:
        raise AppError("workflows.invalid_graph", "; ".join(errors), 400)
    stmt = select(WorkflowVersion.version).where(WorkflowVersion.workflow_id == workflow_id).order_by(
        WorkflowVersion.version.desc()
    )
    latest = (await session.execute(stmt)).scalars().first()
    version = WorkflowVersion(
        workflow_id=workflow.id, version=(latest or 0) + 1, status="draft", graph=data.graph,
        created_by=ctx.user.id,
    )
    session.add(version)
    await session.flush()
    return _version_out(version)


async def list_versions(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID
) -> list[schemas.WorkflowVersionOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_workflow(session, ctx, workflow_id)
    stmt = select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id).order_by(
        WorkflowVersion.version.desc()
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_version_out(v) for v in rows]


async def submit_for_review(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, version: int
) -> schemas.WorkflowVersionOut:
    """`draft -> in_review` (docs/17 Phase 4). `WorkflowVersion.status` has carried this value
    since Phase 2 (ADR-074's model comment names this exact phase as the reason) but nothing
    ever set it — Agent has no equivalent transition at all; its "awaiting review" is inferred
    purely from `is_published` + the viewer's own permission (`builder-header.tsx`), because
    `AgentVersion` never grew a real in_review state to carry. Workflows did, so this makes it
    real instead of leaving it declared-but-unused. `WORKFLOWS_WRITE`, not `WORKFLOWS_PUBLISH`
    — requesting review is part of authoring, the same split `test-run` already uses.

    Deliberately does NOT gate `publish_version` below on having passed through this state
    first: `AgentVersion.publish_version` has never required any prior step, and forcing one
    here would be a new, undiscussed product rule rather than "reuse the pattern" (see ADR-080).
    """
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    await _get_workflow(session, ctx, workflow_id)
    v = await _get_version(session, workflow_id, version)
    if v.status != "draft":
        raise AppError(
            "workflows.submit_review_invalid_state",
            f"Only a draft version can be submitted for review (this version is {v.status!r}).",
            400,
        )
    v.status = "in_review"
    return _version_out(v)


async def publish_version(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, version: int
) -> schemas.WorkflowOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_PUBLISH)
    await usage.require_feature(session, ctx.org, "workflows")
    workflow = await _get_workflow(session, ctx, workflow_id)
    v = await _get_version(session, workflow_id, version)
    # docs/17 Phase 3 publish gate, closed for workflows here (ADR-081) — local import to avoid
    # a circular import, matching agents/service.py::publish_version's exact reason:
    # app.modules.workflow_tests.service imports FROM this module (to build the real executors
    # for live-mode test runs), so this module cannot also import it at the top level.
    from app.modules.workflow_tests.service import latest_batch_has_failures

    if await latest_batch_has_failures(session, workflow_id):
        raise AppError(
            "workflows.tests_failing",
            "The latest test run has failures — fix them before publishing.",
            400,
        )
    v.status = "published"
    workflow.current_version_id = v.id
    return _workflow_out(workflow)


async def rollback(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, version: int
) -> schemas.WorkflowOut:
    """Moves `current_version_id` back onto an already-published version — matches
    `app.modules.agents.service.rollback` exactly (read directly, not guessed, per docs/17
    Phase 4's instruction): no new version row is created, the target must already be
    published, and nothing about any other version changes. `WorkflowVersion` rows are never
    deleted or mutated once created, so an older published version is always still there to
    roll back onto, and rolling forward again afterwards is just another `publish_version` call.
    """
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_PUBLISH)
    await usage.require_feature(session, ctx.org, "workflows")
    workflow = await _get_workflow(session, ctx, workflow_id)
    v = await _get_version(session, workflow_id, version)
    if v.status != "published":
        raise AppError(
            "workflows.rollback_unpublished", "Can only roll back to a published version.", 400
        )
    workflow.current_version_id = v.id
    return _workflow_out(workflow)


async def diff_versions(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, version_a: int, version_b: int
) -> schemas.WorkflowVersionDiffOut:
    """Structural diff between two versions of this workflow's graph (docs/17 Phase 4).
    `WORKFLOWS_WRITE`, not `WORKFLOWS_PUBLISH` — reviewing a draft against what's live is part
    of authoring/reviewing, not a publish action, the same reasoning `run_workflow_test` already
    uses. Pure computation over the two stored `graph` JSON blobs; see `app.workflows.diff`."""
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    await _get_workflow(session, ctx, workflow_id)
    a = await _get_version(session, workflow_id, version_a)
    b = await _get_version(session, workflow_id, version_b)
    computed = diff_graphs(a.graph, b.graph)
    return schemas.WorkflowVersionDiffOut(from_version=a.version, to_version=b.version, **computed)


# ── Execution ────────────────────────────────────────────────────────────────────────────


async def _create_and_dispatch_run(
    session: AsyncSession,
    ctx: OrgContext,
    workflow: Workflow,
    version: WorkflowVersion,
    data: schemas.RunWorkflowRequest,
    *,
    is_test: bool,
) -> schemas.WorkflowRunOut:
    run = WorkflowRun(
        workflow_version_id=version.id, organization_id=ctx.org.id,
        conversation_id=data.conversation_id, status="running", variables=dict(data.variables),
        budget=_budget_to_dict(default_budget()), is_test=is_test,
    )
    session.add(run)
    await session.flush()
    await _dispatch_run(session, run, workflow, version)
    return _run_out(run)


async def run_workflow_now(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, data: schemas.RunWorkflowRequest
) -> schemas.WorkflowRunOut:
    """Runs the workflow's PUBLISHED version. Creates the `WorkflowRun` row and dispatches
    execution (Celery in production; see `_dispatch_run`) — this function itself never walks
    the graph. `run.status` is still `"running"` in the returned `WorkflowRunOut` when
    dispatched to a real worker; only eager/test mode finishes before this returns.

    Test-mode execution against the latest (possibly unpublished) version is
    `run_workflow_test` below, on a separate endpoint.
    """
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    workflow = await _get_workflow(session, ctx, workflow_id)
    if workflow.current_version_id is None:
        raise AppError("workflows.not_published", "This workflow has no published version.", 400)
    version = await session.get(WorkflowVersion, workflow.current_version_id)
    assert version is not None
    return await _create_and_dispatch_run(session, ctx, workflow, version, data, is_test=False)


async def run_workflow_test(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, data: schemas.RunWorkflowRequest
) -> schemas.WorkflowRunOut:
    """Runs the workflow's LATEST version — draft or published — so an editor can try out what
    they just saved before anyone with `WORKFLOWS_PUBLISH` needs to sign off on it. Requires
    only `WORKFLOWS_WRITE`, deliberately: testing your own draft is part of authoring it, not a
    publish action (docs/17 Phase 2 item 3). Marked `is_test=True`; otherwise executed through
    the exact same dispatch path as a real run — no side-effect sandboxing, the same trade-off
    the Agent Playground already makes.
    """
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    workflow = await _get_workflow(session, ctx, workflow_id)
    version = await _latest_workflow_version(session, workflow_id)
    return await _create_and_dispatch_run(session, ctx, workflow, version, data, is_test=True)


async def resume_workflow_run_unchecked(
    session: AsyncSession, run: WorkflowRun, data: schemas.ResumeWorkflowRequest
) -> schemas.WorkflowRunOut:
    """The actual resume logic, with NO RBAC check — `run` must already have been fetched and
    ownership-verified by the caller. Exists so `app.modules.inbox.service`'s approval-decide
    endpoint can drive the exact same execution path under `INBOX_HANDLE` instead of
    `WORKFLOWS_WRITE` (docs/17 Phase 2 item 4: approving/rejecting is an operational inbox
    action, not a workflow-editing one) without duplicating this logic. `resume_workflow_run`
    below is the RBAC-checked entry point every other caller should use.
    """
    if run.status != "paused_approval":
        raise AppError("workflows.not_paused", "This run is not waiting on approval.", 400)
    version = await session.get(WorkflowVersion, run.workflow_version_id)
    assert version is not None
    workflow = await session.get(Workflow, version.workflow_id)
    assert workflow is not None

    # Reflects the decision was accepted immediately, before a real worker picks it up — a
    # poller (and the inbox list) must never see the stale "paused_approval"/open-handoff once
    # a decision has been made. `_execute_and_persist` resolves it again once execution
    # actually finishes, a harmless no-op by then.
    run.status = "running"
    await _resolve_run_handoffs(session, run.id, run.organization_id)
    await session.flush()
    await _dispatch_run(session, run, workflow, version, resume_input={"decision": data.decision})
    return _run_out(run)


async def resume_workflow_run(
    session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID, data: schemas.ResumeWorkflowRequest
) -> schemas.WorkflowRunOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    run = await _get_run(session, ctx, run_id)
    return await resume_workflow_run_unchecked(session, run, data)


async def list_workflow_runs(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, *, limit: int = 20
) -> list[schemas.WorkflowRunOut]:
    """Every run across every version of this workflow, newest first — the canvas's run-
    history picker (docs/17 Phase 2 gap-closure item 3) reads this so an author can reopen a
    PAST run's step-by-step overlay without it needing to still be live or polled. `WorkflowRun`
    has no direct `workflow_id` column (only `workflow_version_id`), so this joins through every
    version of the workflow rather than just its current one — an author reviewing history
    reasonably expects a run against an older draft to still show up.

    Orders by `id DESC` as well as `started_at DESC`: within one Postgres transaction
    `func.now()` (the column's `server_default`) is frozen at transaction start, so two runs
    created moments apart in the same transaction can get an IDENTICAL `started_at` — sorting
    on that column alone leaves ties in an arbitrary, not-reliably-newest-first order.
    `WorkflowRun` uses UUIDv7 primary keys (time-ordered), so `id DESC` is a correct, cheap
    tiebreak for exactly this case — same fix shape as `ORDER BY ts_rank DESC, chunks.id` in
    the RAG retrieval path (docs/14 K1+K2).
    """
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_workflow(session, ctx, workflow_id)  # 404s + ownership check
    version_ids_stmt = select(WorkflowVersion.id).where(WorkflowVersion.workflow_id == workflow_id)
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.workflow_version_id.in_(version_ids_stmt))
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_out(r) for r in rows]


async def get_workflow_run(session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID) -> schemas.WorkflowRunOut:
    """The run's own status — the canvas's "Test run" polling reads this (docs/17 Phase 2
    item 6) rather than inferring completion from the step list, which has no dedicated
    terminal/paused marker of its own."""
    rbac.require_permission(ctx.role, rbac.READ)
    return _run_out(await _get_run(session, ctx, run_id))


async def cancel_workflow_run(session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID) -> schemas.WorkflowRunOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    run = await _get_run(session, ctx, run_id)
    if run.status in ("completed", "failed", "cancelled", "budget_exceeded"):
        return _run_out(run)
    await _resolve_run_handoffs(session, run.id, run.organization_id)
    run.status = "cancelled"
    run.completed_at = dt.datetime.now(tz=dt.UTC)
    return _run_out(run)


async def list_run_steps(
    session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID
) -> list[schemas.WorkflowStepOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_run(session, ctx, run_id)
    stmt = select(WorkflowStep).where(WorkflowStep.workflow_run_id == run_id).order_by(
        WorkflowStep.started_at.asc()
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        schemas.WorkflowStepOut(
            id=s.id, node_id=s.node_id, node_type=s.node_type, status=s.status, input=s.input,
            output=s.output, latency_ms=s.latency_ms, cost_usd=s.cost_usd, error=s.error,
            started_at=s.started_at,
        )
        for s in rows
    ]
