"""Agent regression testing (docs/17 Phase 3, ADR-079).

`run_agent_tests` reuses the Playground's own request-building helpers
(`app.modules.agents.service._build_request` / `_retrieve_context` / `_playground_tooling` /
`_resolve_playground_provider`) rather than re-implementing them — a test scenario runs the
agent's real current draft version through the real pipeline (system prompt, RAG retrieval,
tool executor, output guard), the same "not counted as production traffic" pattern the
Playground already established. Imported locally (not at module top) specifically to avoid a
circular import: `app.modules.agents.service.publish_version` calls back into THIS module's
`latest_batch_has_failures` for the publish gate, so the two modules import each other in
different functions — one direction has to be lazy.

Cached mode (the default, and the only mode that may run automatically) never calls a real
model: `app.llm.fake.MultiRoundToolProvider` is built directly from the test case's own
`scripted_tool_calls`/`scripted_final_answer`, so the assertion is really about whether the
REST of the pipeline (tool execution against this agent's real tools, the real L5 output guard)
still behaves correctly against a known, fixed input — see ADR-079 for the full reasoning.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.budget import default_budget
from app.chat.runtime import TurnResult, run_turn
from app.core import rbac
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.fake import MultiRoundToolProvider
from app.llm.types import ToolCall
from app.models import AgentTest, AgentTestRun
from app.modules.agent_tests import schemas
from app.modules.orgs.deps import OrgContext

log = get_logger("agent_tests")


# ── Internals ────────────────────────────────────────────────────────────────────────────


async def _get_test(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, test_id: uuid.UUID) -> AgentTest:
    test = await session.get(AgentTest, test_id)
    if test is None or test.organization_id != ctx.org.id or test.agent_id != agent_id:
        raise AppError("agent_tests.not_found", "Test case not found.", 404)
    return test


async def _get_run(session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID) -> AgentTestRun:
    run = await session.get(AgentTestRun, run_id)
    if run is None or run.organization_id != ctx.org.id:
        raise AppError("agent_tests.run_not_found", "Test run not found.", 404)
    return run


def _test_out(t: AgentTest) -> schemas.AgentTestOut:
    return schemas.AgentTestOut(
        id=t.id, agent_id=t.agent_id, name=t.name, description=t.description,
        input_message=t.input_message, input_history=t.input_history,
        scripted_tool_calls=t.scripted_tool_calls, scripted_final_answer=t.scripted_final_answer,
        expected_tool_calls=t.expected_tool_calls,
        expected_final_answer_contains=t.expected_final_answer_contains,
        enabled=t.enabled, created_at=t.created_at, updated_at=t.updated_at,
    )


def _run_out(r: AgentTestRun) -> schemas.AgentTestRunOut:
    return schemas.AgentTestRunOut(
        id=r.id, agent_id=r.agent_id, agent_test_id=r.agent_test_id, batch_id=r.batch_id,
        mode=r.mode, status=r.status, actual_tool_calls=r.actual_tool_calls,
        actual_final_answer=r.actual_final_answer, failure_reasons=r.failure_reasons,
        latency_ms=r.latency_ms, cost_usd=r.cost_usd, error=r.error,
        started_at=r.started_at, completed_at=r.completed_at,
    )


def _tool_call_matches(actual: list[dict[str, Any]], expected: dict[str, Any]) -> bool:
    for call in actual:
        if call.get("name") != expected.get("name"):
            continue
        wanted_args = expected.get("arguments")
        if wanted_args is None:
            return True  # name-only match
        actual_args = call.get("arguments") or {}
        if all(actual_args.get(k) == v for k, v in wanted_args.items()):
            return True
    return False


def _evaluate(
    test: AgentTest, actual_tool_calls: list[dict[str, Any]], actual_final_answer: str
) -> list[str]:
    failures: list[str] = []
    for expected in test.expected_tool_calls:
        if not _tool_call_matches(actual_tool_calls, expected):
            failures.append(f"expected a call to {expected.get('name')!r} was not observed")
    if test.expected_final_answer_contains:
        if test.expected_final_answer_contains not in (actual_final_answer or ""):
            failures.append(
                f"final answer did not contain {test.expected_final_answer_contains!r}"
            )
    return failures


async def _run_one_test(
    session: AsyncSession,
    ctx: OrgContext,
    agent: Any,
    version: Any,
    test: AgentTest,
    *,
    mode: str,
    batch_id: uuid.UUID,
) -> AgentTestRun:
    # Local import — see the module docstring for why (breaks a circular import with
    # app.modules.agents.service, which calls back into this module for the publish gate).
    from app.modules.agents import schemas as agent_schemas
    from app.modules.agents.service import (
        _build_request,
        _playground_tooling,
        _resolve_playground_provider,
        _retrieve_context,
    )

    t0 = time.perf_counter()
    run = AgentTestRun(
        organization_id=ctx.org.id, agent_id=agent.id, agent_test_id=test.id, batch_id=batch_id,
        mode=mode, status="error", actual_tool_calls=[], failure_reasons=[],
    )
    try:
        history = [
            agent_schemas.PlaygroundMessage(role=h["role"], content=h["content"]) for h in test.input_history
        ]
        data = agent_schemas.PlaygroundRequest(message=test.input_message, history=history, stream=False)
        context_block, citations = await _retrieve_context(session, ctx, version, test.input_message)
        req = _build_request(
            version, data, stream=False, context_block=context_block,
            agent_name=agent.name, business_name=ctx.org.name,
        )

        if mode == "cached":
            scripted_calls = [
                ToolCall(id=str(uuid.uuid4()), name=c["name"], arguments=c.get("arguments") or {})
                for c in test.scripted_tool_calls
            ]
            provider: Any = MultiRoundToolProvider(scripted_calls, answer=test.scripted_final_answer or "")
        else:
            provider_name = (version.model_config_json or {}).get("provider", "fake")
            provider = await _resolve_playground_provider(
                session, ctx, agent, provider_name, version.model_config_json or {}
            )

        specs, executor = await _playground_tooling(session, ctx, agent, version, provider)
        if specs:
            req.tools = specs

        budget = default_budget()
        result = TurnResult()
        # guard_output=True (unlike the Playground's False): a test asserts what a VISITOR
        # would actually see, output guard included — a scripted answer containing
        # unallowlisted PII, redacted in `actual_final_answer`, is exactly the regression this
        # mode exists to catch (ADR-079).
        async for _ev in run_turn(
            provider, req, [c.model_dump(mode="json") for c in citations], result,
            executor=executor, max_iters=settings.tool_max_iterations, budget=budget,
            guard_output=True,
        ):
            pass

        actual_tool_calls = [
            {"name": s["tool_name"], "arguments": s["tool_input"]}
            for s in result.agent_steps
            if s["kind"] == "tool_call"
        ]
        failures = _evaluate(test, actual_tool_calls, result.content)

        run.actual_tool_calls = actual_tool_calls
        run.actual_final_answer = result.content
        run.failure_reasons = failures
        run.status = "failed" if failures else "passed"
        run.cost_usd = (result.cost_micros or 0) / 1_000_000
    except Exception as exc:  # one bad test case must not abort the whole batch
        run.status = "error"
        run.error = str(exc)
        log.warning("agent_test_run_error", agent_test_id=str(test.id), error=str(exc))

    run.latency_ms = int((time.perf_counter() - t0) * 1000)
    run.completed_at = dt.datetime.now(tz=dt.UTC)
    session.add(run)
    await session.flush()
    return run


# ── CRUD ─────────────────────────────────────────────────────────────────────────────────


async def create_agent_test(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.CreateAgentTestRequest
) -> schemas.AgentTestOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    test = AgentTest(
        organization_id=ctx.org.id, agent_id=agent_id, name=data.name, description=data.description,
        input_message=data.input_message, input_history=[h.model_dump() for h in data.input_history],
        scripted_tool_calls=[c.model_dump() for c in data.scripted_tool_calls],
        scripted_final_answer=data.scripted_final_answer,
        expected_tool_calls=[c.model_dump() for c in data.expected_tool_calls],
        expected_final_answer_contains=data.expected_final_answer_contains,
        enabled=data.enabled, created_by=ctx.user.id,
    )
    session.add(test)
    await session.flush()
    return _test_out(test)


async def list_agent_tests(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> list[schemas.AgentTestOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = select(AgentTest).where(
        AgentTest.organization_id == ctx.org.id, AgentTest.agent_id == agent_id
    ).order_by(AgentTest.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_test_out(t) for t in rows]


async def update_agent_test(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, test_id: uuid.UUID,
    data: schemas.UpdateAgentTestRequest,
) -> schemas.AgentTestOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    test = await _get_test(session, ctx, agent_id, test_id)
    if data.name is not None:
        test.name = data.name
    if data.description is not None:
        test.description = data.description
    if data.input_message is not None:
        test.input_message = data.input_message
    if data.input_history is not None:
        test.input_history = [h.model_dump() for h in data.input_history]
    if data.scripted_tool_calls is not None:
        test.scripted_tool_calls = [c.model_dump() for c in data.scripted_tool_calls]
    if data.scripted_final_answer is not None:
        test.scripted_final_answer = data.scripted_final_answer
    if data.expected_tool_calls is not None:
        test.expected_tool_calls = [c.model_dump() for c in data.expected_tool_calls]
    if data.expected_final_answer_contains is not None:
        test.expected_final_answer_contains = data.expected_final_answer_contains
    if data.enabled is not None:
        test.enabled = data.enabled
    test.updated_at = dt.datetime.now(tz=dt.UTC)
    return _test_out(test)


async def delete_agent_test(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, test_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    test = await _get_test(session, ctx, agent_id, test_id)
    await session.delete(test)


# ── Execution ────────────────────────────────────────────────────────────────────────────


async def run_agent_tests(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.RunAgentTestsRequest
) -> list[schemas.AgentTestRunOut]:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    from app.modules.agents.service import check_playground_allowed

    await check_playground_allowed(session, ctx)
    from app.modules.agents.service import _get_agent, _latest_version

    agent = await _get_agent(session, ctx, agent_id)
    version = await _latest_version(session, agent.id)

    stmt = select(AgentTest).where(
        AgentTest.organization_id == ctx.org.id, AgentTest.agent_id == agent_id, AgentTest.enabled.is_(True)
    )
    if data.test_ids:
        stmt = stmt.where(AgentTest.id.in_(data.test_ids))
    tests = (await session.execute(stmt)).scalars().all()
    if not tests:
        raise AppError("agent_tests.none_to_run", "No enabled test cases match.", 400)

    batch_id = uuid.uuid4()
    runs = [
        await _run_one_test(session, ctx, agent, version, test, mode=data.mode, batch_id=batch_id)
        for test in tests
    ]
    return [_run_out(r) for r in runs]


async def get_agent_test_run(session: AsyncSession, ctx: OrgContext, run_id: uuid.UUID) -> schemas.AgentTestRunOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return _run_out(await _get_run(session, ctx, run_id))


async def list_agent_test_runs(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, *, limit: int = 50
) -> list[schemas.AgentTestRunOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = (
        select(AgentTestRun)
        .where(AgentTestRun.organization_id == ctx.org.id, AgentTestRun.agent_id == agent_id)
        .order_by(AgentTestRun.started_at.desc(), AgentTestRun.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_out(r) for r in rows]


# ── Publish gate (docs/17 Phase 3 DoD, ADR-079) ─────────────────────────────────────────────


async def latest_batch_has_failures(session: AsyncSession, agent_id: uuid.UUID) -> bool:
    """True only if the most recent test batch for this agent contains a `failed`/`error`
    result. False if there is no batch at all (opt-in gate — see ADR-079) or if every case in
    the latest batch passed. Not scoped to `organization_id` — the caller (`publish_version`)
    already resolved `agent_id` through its own org-scoped lookup, so a second check here would
    be redundant, not an extra safety margin.
    """
    latest_batch_stmt = (
        select(AgentTestRun.batch_id)
        .where(AgentTestRun.agent_id == agent_id)
        .order_by(AgentTestRun.started_at.desc(), AgentTestRun.id.desc())
        .limit(1)
    )
    latest_batch_id = (await session.execute(latest_batch_stmt)).scalar_one_or_none()
    if latest_batch_id is None:
        return False
    failures_stmt = (
        select(func.count())
        .select_from(AgentTestRun)
        .where(AgentTestRun.batch_id == latest_batch_id, AgentTestRun.status != "passed")
    )
    count = (await session.execute(failures_stmt)).scalar_one()
    return count > 0
