"""Workflow regression testing (docs/17 Phase 4 follow-up, ADR-081).

Mirrors `app.modules.agent_tests.service` in structure and rules — cached mode is the default
and the only mode that may run automatically, live mode is explicitly triggered, a batch groups
every case from one `POST /tests/run` call, and `latest_batch_has_failures` gates
`WORKFLOWS_PUBLISH` the same way `app.modules.agent_tests.service.latest_batch_has_failures`
already gates `AGENTS_PUBLISH` (docs/17 Phase 3 DoD's original ask, closed here for workflows).

Cached mode never calls a real tool/agent/sub-workflow: `_scripted_tool_executor` /
`_scripted_agent_executor` / `_scripted_sub_workflow_executor` are injected into
`app.workflows.graph.run_workflow` in place of the real executors `app.workflows.service`
builds from the DB, so the assertion is about whether the REST of the graph (branching,
variable writes, budget accounting) still behaves correctly against a known, fixed input — same
reasoning as ADR-079's cached-mode `MultiRoundToolProvider`. Every one of these three injected
callables returns data the graph.py node handlers pass through `neutralize_injections()` before
it ever reaches `variables` — that call lives in `graph.py`, not in the executor, so it applies
identically whether the executor is real or scripted. No new sanitization needed here.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.chat.budget import AgentBudget, default_budget
from app.core import rbac
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import WorkflowTest, WorkflowTestRun
from app.modules.orgs.deps import OrgContext
from app.modules.workflow_tests import schemas
from app.workflows.graph import (
    WorkflowAgentExecutor,
    WorkflowRunResult,
    WorkflowSubExecutor,
    WorkflowToolExecutor,
    run_workflow,
)

log = get_logger("workflow_tests")


# ── Internals ────────────────────────────────────────────────────────────────────────────


async def _get_test(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, test_id: uuid.UUID
) -> WorkflowTest:
    test = await session.get(WorkflowTest, test_id)
    if test is None or test.organization_id != ctx.org.id or test.workflow_id != workflow_id:
        raise AppError("workflow_tests.not_found", "Test case not found.", 404)
    return test


async def _get_run(session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID) -> WorkflowTestRun:
    run = await session.get(WorkflowTestRun, run_id)
    if run is None or run.organization_id != ctx.org.id:
        raise AppError("workflow_tests.run_not_found", "Test run not found.", 404)
    return run


def _test_out(t: WorkflowTest) -> schemas.WorkflowTestOut:
    return schemas.WorkflowTestOut(
        id=t.id, workflow_id=t.workflow_id, name=t.name, description=t.description,
        input_variables=t.input_variables, scripted_node_outputs=t.scripted_node_outputs,
        expected_status=t.expected_status, expected_variables_contains=t.expected_variables_contains,
        expected_visited_node_ids=t.expected_visited_node_ids, enabled=t.enabled,
        created_at=t.created_at, updated_at=t.updated_at,
    )


def _run_out(r: WorkflowTestRun) -> schemas.WorkflowTestRunOut:
    return schemas.WorkflowTestRunOut(
        id=r.id, workflow_id=r.workflow_id, workflow_test_id=r.workflow_test_id, batch_id=r.batch_id,
        mode=r.mode, status=r.status, actual_status=r.actual_status, actual_variables=r.actual_variables,
        actual_visited_node_ids=r.actual_visited_node_ids, failure_reasons=r.failure_reasons,
        latency_ms=r.latency_ms, cost_usd=r.cost_usd, error=r.error,
        started_at=r.started_at, completed_at=r.completed_at,
    )


def _scripted_tool_executor(scripts: dict[str, Any]) -> WorkflowToolExecutor:
    tool_scripts = scripts.get("tools") or {}

    async def executor(tool_name: str, _args: dict[str, Any]) -> dict[str, Any]:
        script = tool_scripts.get(tool_name)
        if script is None:
            return {"output": {}, "status": "error", "error": f"no scripted output for tool {tool_name!r}"}
        return {
            "output": script.get("output") or {},
            "status": script.get("status", "completed"),
            "error": script.get("error"),
        }

    return executor


def _scripted_agent_executor(scripts: dict[str, Any]) -> WorkflowAgentExecutor:
    agent_scripts = scripts.get("agents") or {}

    async def executor(agent_id_str: str, _message: str, _budget: AgentBudget) -> dict[str, Any]:
        script = agent_scripts.get(agent_id_str)
        if script is None:
            return {"content": "", "status": "error", "error": f"no scripted output for agent {agent_id_str!r}"}
        return {
            "content": script.get("content", ""),
            "status": script.get("status", "completed"),
            "error": script.get("error"),
        }

    return executor


def _scripted_sub_workflow_executor(scripts: dict[str, Any]) -> WorkflowSubExecutor:
    sub_scripts = scripts.get("sub_workflows") or {}

    async def executor(
        workflow_id_str: str, variables_snapshot: dict[str, Any], _budget: AgentBudget
    ) -> WorkflowRunResult:
        script = sub_scripts.get(workflow_id_str)
        if script is None:
            return WorkflowRunResult(
                status="failed", variables=variables_snapshot,
                error=f"no scripted output for sub-workflow {workflow_id_str!r}",
            )
        variables = {**variables_snapshot, **(script.get("variables") or {})}
        return WorkflowRunResult(
            status=script.get("status", "completed"), variables=variables, error=script.get("error"),
        )

    return executor


def _evaluate(test: WorkflowTest, result: WorkflowRunResult) -> list[str]:
    failures: list[str] = []
    if test.expected_status and result.status != test.expected_status:
        failures.append(f"expected status {test.expected_status!r}, got {result.status!r}")
    for key, value in (test.expected_variables_contains or {}).items():
        actual = result.variables.get(key)
        if actual != value:
            failures.append(f"expected variable {key!r} == {value!r}, got {actual!r}")
    visited = {step.get("node_id") for step in result.steps}
    for node_id in test.expected_visited_node_ids or []:
        if node_id not in visited:
            failures.append(f"expected node {node_id!r} to be visited, it was not")
    return failures


async def _run_one_test(
    session: AsyncSession,
    ctx: OrgContext,
    workflow: Any,
    version: Any,
    test: WorkflowTest,
    *,
    mode: str,
    batch_id: uuid.UUID,
) -> WorkflowTestRun:
    # Local import — mirrors agent_tests.service's own reason: app.workflows.service does not
    # import this module today, but keeping the import local matches the established convention
    # for every cross-module reach into another module's private helpers in this codebase.
    from app.workflows.service import _agent_executor_for, _sub_workflow_executor_for, _tool_executor_for

    t0 = time.perf_counter()
    run = WorkflowTestRun(
        organization_id=ctx.org.id, workflow_id=workflow.id, workflow_test_id=test.id, batch_id=batch_id,
        mode=mode, status="error", actual_variables={}, actual_visited_node_ids=[], failure_reasons=[],
    )
    try:
        budget = default_budget()
        if mode == "cached":
            tool_executor: Any = _scripted_tool_executor(test.scripted_node_outputs)
            agent_executor: Any = _scripted_agent_executor(test.scripted_node_outputs)
            sub_workflow_executor: Any = _scripted_sub_workflow_executor(test.scripted_node_outputs)
        else:
            tool_executor = await _tool_executor_for(session, ctx.org, workflow.agent_id)
            agent_executor = await _agent_executor_for(session, ctx.org)
            sub_workflow_executor = await _sub_workflow_executor_for(session, ctx.org)

        result = await run_workflow(
            version.graph, variables=dict(test.input_variables), budget=budget,
            tool_executor=tool_executor, agent_executor=agent_executor,
            sub_workflow_executor=sub_workflow_executor,
        )
        failures = _evaluate(test, result)

        run.actual_status = result.status
        run.actual_variables = result.variables
        run.actual_visited_node_ids = [s.get("node_id") for s in result.steps]
        run.failure_reasons = failures
        run.status = "failed" if failures else "passed"
        run.cost_usd = budget.consumed_cost_usd
    except Exception as exc:  # one bad test case must not abort the whole batch
        run.status = "error"
        run.error = str(exc)
        log.warning("workflow_test_run_error", workflow_test_id=str(test.id), error=str(exc))

    run.latency_ms = int((time.perf_counter() - t0) * 1000)
    run.completed_at = dt.datetime.now(tz=dt.UTC)
    session.add(run)
    await session.flush()
    return run


# ── CRUD ─────────────────────────────────────────────────────────────────────────────────


async def create_workflow_test(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, data: schemas.CreateWorkflowTestRequest
) -> schemas.WorkflowTestOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    test = WorkflowTest(
        organization_id=ctx.org.id, workflow_id=workflow_id, name=data.name, description=data.description,
        input_variables=data.input_variables, scripted_node_outputs=data.scripted_node_outputs.model_dump(),
        expected_status=data.expected_status, expected_variables_contains=data.expected_variables_contains,
        expected_visited_node_ids=data.expected_visited_node_ids, enabled=data.enabled, created_by=ctx.user.id,
    )
    session.add(test)
    await session.flush()
    return _test_out(test)


async def list_workflow_tests(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID
) -> list[schemas.WorkflowTestOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(WorkflowTest).where(
        WorkflowTest.organization_id == ctx.org.id, WorkflowTest.workflow_id == workflow_id
    ).order_by(WorkflowTest.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_test_out(t) for t in rows]


async def update_workflow_test(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, test_id: uuid.UUID,
    data: schemas.UpdateWorkflowTestRequest,
) -> schemas.WorkflowTestOut:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    test = await _get_test(session, ctx, workflow_id, test_id)
    if data.name is not None:
        test.name = data.name
    if data.description is not None:
        test.description = data.description
    if data.input_variables is not None:
        test.input_variables = data.input_variables
    if data.scripted_node_outputs is not None:
        test.scripted_node_outputs = data.scripted_node_outputs.model_dump()
    if data.expected_status is not None:
        test.expected_status = data.expected_status
    if data.expected_variables_contains is not None:
        test.expected_variables_contains = data.expected_variables_contains
    if data.expected_visited_node_ids is not None:
        test.expected_visited_node_ids = data.expected_visited_node_ids
    if data.enabled is not None:
        test.enabled = data.enabled
    test.updated_at = dt.datetime.now(tz=dt.UTC)
    return _test_out(test)


async def delete_workflow_test(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, test_id: uuid.UUID
) -> None:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    test = await _get_test(session, ctx, workflow_id, test_id)
    await session.delete(test)


# ── Execution ────────────────────────────────────────────────────────────────────────────


async def run_workflow_tests(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, data: schemas.RunWorkflowTestsRequest
) -> list[schemas.WorkflowTestRunOut]:
    rbac.require_permission(ctx.role, rbac.WORKFLOWS_WRITE)
    await usage.require_feature(session, ctx.org, "workflows")
    from app.workflows.service import _get_workflow, _latest_workflow_version

    workflow = await _get_workflow(session, ctx, workflow_id)
    version = await _latest_workflow_version(session, workflow.id)

    stmt = select(WorkflowTest).where(
        WorkflowTest.organization_id == ctx.org.id, WorkflowTest.workflow_id == workflow_id,
        WorkflowTest.enabled.is_(True),
    )
    if data.test_ids:
        stmt = stmt.where(WorkflowTest.id.in_(data.test_ids))
    tests = (await session.execute(stmt)).scalars().all()
    if not tests:
        raise AppError("workflow_tests.none_to_run", "No enabled test cases match.", 400)

    batch_id = uuid.uuid4()
    runs = [
        await _run_one_test(session, ctx, workflow, version, test, mode=data.mode, batch_id=batch_id)
        for test in tests
    ]
    return [_run_out(r) for r in runs]


async def get_workflow_test_run(
    session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID
) -> schemas.WorkflowTestRunOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return _run_out(await _get_run(session, ctx, run_id))


async def list_workflow_test_runs(
    session: AsyncSession, ctx: OrgContext, workflow_id: uuid.UUID, *, limit: int = 50
) -> list[schemas.WorkflowTestRunOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = (
        select(WorkflowTestRun)
        .where(WorkflowTestRun.organization_id == ctx.org.id, WorkflowTestRun.workflow_id == workflow_id)
        .order_by(WorkflowTestRun.started_at.desc(), WorkflowTestRun.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_out(r) for r in rows]


# ── Publish gate (docs/17 Phase 3 DoD, closed for workflows here — ADR-081) ────────────────


async def latest_batch_has_failures(session: AsyncSession, workflow_id: uuid.UUID) -> bool:
    """True only if the most recent test batch for this workflow contains a `failed`/`error`
    result. False if there is no batch at all (opt-in gate, matching
    `app.modules.agent_tests.service.latest_batch_has_failures` exactly) or if every case in the
    latest batch passed. Not scoped to `organization_id` — the caller (`publish_version`)
    already resolved `workflow_id` through its own org-scoped lookup."""
    latest_batch_stmt = (
        select(WorkflowTestRun.batch_id)
        .where(WorkflowTestRun.workflow_id == workflow_id)
        .order_by(WorkflowTestRun.started_at.desc(), WorkflowTestRun.id.desc())
        .limit(1)
    )
    latest_batch_id = (await session.execute(latest_batch_stmt)).scalar_one_or_none()
    if latest_batch_id is None:
        return False
    failures_stmt = (
        select(func.count())
        .select_from(WorkflowTestRun)
        .where(WorkflowTestRun.batch_id == latest_batch_id, WorkflowTestRun.status != "passed")
    )
    count = (await session.execute(failures_stmt)).scalar_one()
    return count > 0
