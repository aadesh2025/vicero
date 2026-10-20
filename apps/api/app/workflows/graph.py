"""The workflow graph execution engine (docs/17 Phase 2 §3.1, ADR-074).

A workflow version's `graph` is `{"nodes": [{"id","type","config"}], "edges": [{"source",
"target","condition"}]}`. `run_workflow()` walks it node by node — synchronously, in one call,
the same shape `app.chat.runtime.run_turn` uses for its own step loop — and reuses
`app.chat.budget.AgentBudget` rather than inventing a second bounding mechanism: a workflow
run is bounded exactly the way an agentic chat turn is (steps/tool-calls/runtime/cost), and a
Sub-Agent node (not built this slice) sharing the SAME budget instance as its parent workflow
run gets the inheritance rule from docs/17 §2 rule 3 for free.

**Node types:** start, end, message, condition, set_variable, approval, tool, switch, loop,
transform, agent, sub_agent, delay. `get_variable` is still deliberately not shipped —
redundant with template substitution, a Message/Condition/Transform node already reads any
variable by name. See docs/PROGRESS.md's Phase 2 gap-closure entry for the reasoning behind
each of the newer types.

- **switch** is `condition` generalised to N-way: a literal-value → branch-name mapping
  (`config.cases`), never `eval()`, falling back to `config.default_branch` (default
  `"default"`) when nothing matches.
- **loop** iterates a list variable via a GRAPH CYCLE, not recursion: the loop node emits
  branch `"body"` (with the next item written to `config.item_variable`) or `"done"` once the
  list is exhausted, and the graph author wires the loop body's last node back to the loop
  node's own id. This means the existing single node-at-a-time walker handles it with no new
  execution model — and it means `AgentBudget.max_steps` is *already* a hard iteration cap
  with no extra plumbing: every revisit of the loop node is a node visit like any other, so
  `run_workflow`'s existing `budget.can_continue()` check (top of the loop, below) catches an
  unbounded list exactly the way it catches any other runaway graph. `config.max_iterations`
  is an optional second, loop-local cap for when the workflow's shared step budget is too
  coarse to bound one specific loop.
- **transform** is a whitelist of named string/number operations (uppercase, lowercase, trim,
  to_number, length, concat) — same no-`eval()` rule as `evaluate_condition`. `concat` takes
  its second operand from `config.with_variable` XOR `config.with_literal`, deliberately not a
  single ambiguous `with` key that would have to guess whether the value names a variable or
  is one.
- **agent** invokes an existing published `Agent` through the real agentic runtime
  (`app.chat.runtime.run_turn`), via an injected `agent_executor` built by the service layer
  (this module stays DB-free by design — the executor is how it reaches the DB without
  importing `app.workflows.service`, mirroring `tool_executor`). Passes the SAME `budget`
  instance into `run_turn`, so a multi-step think→act→observe cycle inside the invoked agent
  decrements the workflow run's own ceiling, per docs/17 §2 rule 3 — never a fresh budget for
  the nested call. The agent's reply is untrusted (its own KB or tools may be attacker-
  influenced) and is `neutralize_injections()`-ed before it can reach a variable.
- **sub_agent** invokes another `Workflow` by id as a nested run, via an injected
  `sub_workflow_executor`, sharing the same `budget`. Call depth is tracked on the budget
  itself (`call_depth`/`max_call_depth`) — the one object every level of nesting already
  shares — so a cycle of sub-agent nodes (direct self-call or indirect A→B→A) fails loudly
  once the cap is hit rather than recursing until something else gives out. A nested run that
  itself pauses on approval or exhausts the shared budget is surfaced as a failed node, not
  silently swallowed — synchronous cross-run approval propagation is out of scope this slice.
- **delay** pauses the run (`status: "paused_delay"`) and reports `config.duration_seconds`
  from now as `resume_at` — the service layer schedules a Celery task at that `eta` rather
  than blocking a worker, the same "pause, persist, resume later" shape `approval` already
  uses. Refuses outright under eager/synchronous execution (no worker to wake up later) and
  above `WORKFLOW_MAX_DELAY_SECONDS`, both loudly rather than silently no-opping.
  **`max_runtime_s` deliberately does NOT span the real wait** — resuming reconstructs the
  budget fresh (`_budget_from_dict`, no `started_at` in the serialized form), exactly like an
  approval pause already does, and for the same reason: an operator can legitimately take an
  hour to click Approve, and a workflow's own "wait 2 hours, then follow up" is deliberate
  elapsed time, not runaway compute. `max_steps`/`max_tool_calls`/`max_cost_usd` still
  accumulate correctly across the pause (they ARE serialized), so the ceiling on total *work*
  survives even though the wall-clock-since-start figure resets. See ADR-076.

**Security, not incidental:**
- `evaluate_condition()` is a narrow hand-rolled parser (`var OP literal`, five operators, no
  boolean chaining) — never `eval()`. docs/17 §11 rules out arbitrary code execution outright;
  a condition node is exactly the place a careless implementation would reach for `eval` and
  exactly the place that decision would matter.
- `render_workflow_template()` reuses `app.chat.variables.escape_value()` (Phase F) for the
  same reason Phase F needed it: a workflow variable's value can originate from a visitor
  (e.g. a Set Variable node fed by a form field), so it is escaped and length-capped before
  ever appearing in a Message node's rendered text, single-pass, unknown vars render empty.
- A `tool` node's result is untrusted input, no exception for being "one of ours" — it goes
  through `neutralize_injections()` before it can be stored into a variable or referenced by a
  later Message node, the identical rule docs/17 §6 already enforces in `run_turn`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.chat.budget import AgentBudget
from app.chat.guardrails import neutralize_injections
from app.chat.variables import escape_value
from app.core.config import settings
from app.tools import api_tool

# (tool_name, arguments) -> {"output": dict, "status": str, "error": str | None} — identical
# shape to app.chat.runtime.ToolExecutor, deliberately, so the same tool dispatch used for
# chat can be reused here without an adapter.
WorkflowToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# (agent_id, rendered_message, budget) -> {"content": str, "status": "completed"|"error",
# "error": str | None, "cost_usd": float}. `budget` is passed through so the executor can
# forward the SAME instance into `run_turn` (docs/17 §2 rule 3).
WorkflowAgentExecutor = Callable[[str, str, "AgentBudget"], Awaitable[dict[str, Any]]]

# (workflow_id, variables_snapshot, budget) -> WorkflowRunResult of the nested run.
WorkflowSubExecutor = Callable[[str, dict[str, Any], "AgentBudget"], Awaitable["WorkflowRunResult"]]

NODE_TYPES = frozenset({
    "start", "end", "message", "condition", "set_variable", "approval", "tool",
    "switch", "loop", "transform", "agent", "sub_agent", "delay", "http",
})

# An `http` node reaches the executor under this name so one executor serves tools and HTTP calls, and a
# cached workflow test can script it like any tool (`scripted_node_outputs.tools["http:<node_id>"]`).
HTTP_NODE_PREFIX = "http:"
_TERMINAL_TYPES = frozenset({"end"})
_TRANSFORM_OPS = frozenset({"uppercase", "lowercase", "trim", "to_number", "length", "concat"})


class WorkflowError(Exception):
    """A malformed graph or an unrecoverable execution error."""


@dataclass
class NodeResult:
    status: str  # completed | failed | awaiting_approval | awaiting_delay
    output: dict[str, Any] | None = None
    error: str | None = None
    cost_usd: float = 0.0
    # The edge-selection label a condition/approval node produced (e.g. "true"/"false",
    # "approved"/"rejected"). None for nodes with a single unconditional outgoing edge.
    branch: str | None = None


@dataclass
class WorkflowRunResult:
    status: str  # completed | paused_approval | paused_delay | failed | budget_exceeded
    variables: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    current_node_id: str | None = None
    error: str | None = None


# ── Graph validation (docs/17 §3: validated at save time, not accepted as opaque JSON) ────


def validate_graph(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not nodes:
        return ["graph.nodes must be a non-empty list"]
    ids = [n.get("id") for n in nodes]
    if len(ids) != len(set(ids)):
        errors.append("duplicate node id")
    node_ids = set(ids)
    for n in nodes:
        if n.get("type") not in NODE_TYPES:
            errors.append(f"unknown node type {n.get('type')!r} (node {n.get('id')!r})")
    starts = [n for n in nodes if n.get("type") == "start"]
    if len(starts) != 1:
        errors.append(f"graph must have exactly one start node, found {len(starts)}")
    ends = [n for n in nodes if n.get("type") == "end"]
    if not ends:
        errors.append("graph must have at least one end node")
    for n in nodes:
        if n.get("type") == "http":
            errors.extend(_http_node_errors(n))
    for e in edges:
        if e.get("source") not in node_ids:
            errors.append(f"edge source {e.get('source')!r} is not a node in this graph")
        if e.get("target") not in node_ids:
            errors.append(f"edge target {e.get('target')!r} is not a node in this graph")
    return errors


def _http_node_errors(node: dict[str, Any]) -> list[str]:
    """Save-time checks for an `http` node, so a typo fails at publish and not on a live run."""
    nid = node.get("id")
    config = node.get("config") or {}
    if not isinstance(config, dict):
        return [f"http node {nid!r} needs a config object"]
    errors: list[str] = []
    url, path = config.get("url"), config.get("path")
    if not url and not path:
        errors.append(f"http node {nid!r} requires config.url, or config.path with a connection_id")
    if path and not config.get("connection_id"):
        errors.append(f"http node {nid!r} uses config.path, which needs a config.connection_id")
    if url and api_tool.template_has_host_placeholder(str(url)):
        errors.append(f"http node {nid!r}: the host of config.url cannot contain placeholders")
    if str(config.get("method", "GET")).upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        errors.append(f"http node {nid!r}: unsupported method {config.get('method')!r}")
    response = config.get("response")
    pick = response.get("pick") if isinstance(response, dict) else None
    if pick is not None and (problem := api_tool.validate_selectors(pick)):
        errors.append(f"http node {nid!r}: {problem}")
    return errors


# ── Condition evaluation: a narrow parser, never eval() (docs/17 §11) ─────────────────────

_COND_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*(==|!=|>=|<=|>|<|contains)\s*(.+?)\s*$"
)


def _coerce_literal(raw: str) -> Any:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def evaluate_condition(expression: str, variables: dict[str, Any]) -> bool:
    """`var.path OP literal` only — five operators, no boolean chaining, no eval().

    Matches the shape docs/17's own examples use (`intent == 'booking'`, `lead.score > 50`).
    An expression that doesn't parse is a `WorkflowError`, not a silently-false condition —
    a workflow author needs to see that their expression is malformed, not watch every branch
    silently take the same path.
    """
    match = _COND_RE.match(expression or "")
    if not match:
        raise WorkflowError(f"unsupported condition expression: {expression!r}")
    var_path, op, rhs_raw = match.groups()
    left = variables.get(var_path)
    right = _coerce_literal(rhs_raw)
    if op == "==":
        return bool(left == right)
    if op == "!=":
        return bool(left != right)
    if op == "contains":
        return right in (left or "")
    if left is None:
        return False
    try:
        if op == ">":
            return bool(left > right)
        if op == "<":
            return bool(left < right)
        if op == ">=":
            return bool(left >= right)
        if op == "<=":
            return bool(left <= right)
    except TypeError:
        return False
    return False


# ── Template substitution: reuses Phase F's escaping discipline ───────────────────────────

_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


def render_workflow_template(template: str | None, variables: dict[str, Any]) -> str:
    """Single-pass `{{var.path}}` substitution. Unknown/unset variables render empty, never
    the literal placeholder — same rule `app.chat.variables.render()` uses, for the same
    reason: a missing value should read as a gap in the sentence, not as visible machinery.
    """
    if not template:
        return template or ""
    if "{{" not in template:
        return template

    def _replace(match: re.Match[str]) -> str:
        value = variables.get(match.group(1))
        if value is None:
            return ""
        return escape_value(str(value))

    return _VAR_PATTERN.sub(_replace, template)


# ── Node handlers ──────────────────────────────────────────────────────────────────────────


async def _run_start(node: dict[str, Any], variables: dict[str, Any], _executor: Any) -> NodeResult:
    return NodeResult(status="completed", output={})


async def _run_end(node: dict[str, Any], variables: dict[str, Any], _executor: Any) -> NodeResult:
    return NodeResult(status="completed", output={})


async def _run_message(node: dict[str, Any], variables: dict[str, Any], _executor: Any) -> NodeResult:
    content = render_workflow_template((node.get("config") or {}).get("content"), variables)
    return NodeResult(status="completed", output={"content": content})


async def _run_set_variable(
    node: dict[str, Any], variables: dict[str, Any], _executor: Any
) -> NodeResult:
    config = node.get("config") or {}
    key = config.get("key")
    if not key or not isinstance(key, str):
        return NodeResult(status="failed", error="set_variable requires config.key")
    rendered = render_workflow_template(str(config.get("value", "")), variables)
    variables[key] = rendered
    return NodeResult(status="completed", output={key: rendered})


async def _run_condition(
    node: dict[str, Any], variables: dict[str, Any], _executor: Any
) -> NodeResult:
    expression = (node.get("config") or {}).get("expression", "")
    try:
        outcome = evaluate_condition(expression, variables)
    except WorkflowError as exc:
        return NodeResult(status="failed", error=str(exc))
    return NodeResult(status="completed", output={"result": outcome}, branch="true" if outcome else "false")


async def _run_approval(
    node: dict[str, Any],
    variables: dict[str, Any],
    _executor: Any,
    *,
    resume_input: dict[str, Any] | None = None,
) -> NodeResult:
    if resume_input is None:
        # First visit: pause. The caller persists `current_node_id` (this node) and resumes
        # later by calling run_workflow again with resume_input={"decision": "approved"|...}.
        message = render_workflow_template((node.get("config") or {}).get("message"), variables)
        return NodeResult(status="awaiting_approval", output={"message": message})
    decision = resume_input.get("decision") or "rejected"
    if decision not in ("approved", "rejected"):
        decision = "rejected"
    return NodeResult(status="completed", output={"decision": decision}, branch=decision)


async def _run_tool(
    node: dict[str, Any],
    variables: dict[str, Any],
    executor: WorkflowToolExecutor | None,
) -> NodeResult:
    config = node.get("config") or {}
    tool_name = config.get("tool_name")
    if not tool_name:
        return NodeResult(status="failed", error="tool node requires config.tool_name")
    if executor is None:
        return NodeResult(status="failed", error="no tool executor configured for this run")
    raw_args = config.get("arguments") or {}
    rendered_args = {
        k: (render_workflow_template(v, variables) if isinstance(v, str) else v)
        for k, v in raw_args.items()
    }
    out = await executor(tool_name, rendered_args)
    # Untrusted, no exception for tool source (docs/17 §6) — identical treatment to
    # `run_turn`'s `neutralize_injections(json.dumps(out.get("output") or {}))` call.
    sanitized_text = neutralize_injections(json.dumps(out.get("output") or {}))
    result_variable = config.get("result_variable")
    if result_variable:
        variables[result_variable] = sanitized_text
    return NodeResult(
        status="failed" if out.get("status") == "error" else "completed",
        output={"sanitized_text": sanitized_text},
        error=out.get("error"),
    )


async def _run_http(
    node: dict[str, Any],
    variables: dict[str, Any],
    executor: WorkflowToolExecutor | None,
) -> NodeResult:
    """An HTTP request made with the same executor as the API tool (docs/28 T1.6). The workflow's variables are
    the template arguments (`{{order_id}}`); the response is untrusted exactly like a tool result."""
    config = node.get("config") or {}
    if executor is None:
        return NodeResult(status="failed", error="no tool executor configured for this run")
    out = await executor(f"{HTTP_NODE_PREFIX}{node['id']}", {"config": config, "variables": dict(variables)})
    sanitized_text = neutralize_injections(json.dumps(out.get("output") or {}))
    result_variable = config.get("result_variable")
    if result_variable:
        variables[result_variable] = sanitized_text
    return NodeResult(
        status="failed" if out.get("status") == "error" else "completed",
        output={"sanitized_text": sanitized_text},
        error=out.get("error"),
    )


async def _run_switch(node: dict[str, Any], variables: dict[str, Any], _executor: Any) -> NodeResult:
    """`condition` generalised to N-way — a literal-value → branch-name mapping, never
    `eval()`. `config.cases` keys are always strings (JSON object keys), so the compared
    variable value is stringified before lookup rather than attempting type-aware matching."""
    config = node.get("config") or {}
    var_path = config.get("variable")
    if not var_path or not isinstance(var_path, str):
        return NodeResult(status="failed", error="switch requires config.variable")
    cases = config.get("cases") or {}
    if not isinstance(cases, dict):
        return NodeResult(status="failed", error="switch requires config.cases to be an object")
    value = variables.get(var_path)
    value_str = "" if value is None else str(value)
    branch = cases.get(value_str)
    if branch is None:
        branch = config.get("default_branch") or "default"
    return NodeResult(status="completed", output={"value": value_str, "branch": branch}, branch=str(branch))


async def _run_loop(node: dict[str, Any], variables: dict[str, Any], _executor: Any) -> NodeResult:
    """Iterates via a graph cycle (see module docstring), not recursion — the loop node's own
    revisits are ordinary node visits, so `AgentBudget.max_steps` already hard-caps it."""
    config = node.get("config") or {}
    list_var = config.get("list_variable")
    item_var = config.get("item_variable")
    if not list_var or not isinstance(list_var, str) or not item_var or not isinstance(item_var, str):
        return NodeResult(
            status="failed", error="loop requires config.list_variable and config.item_variable"
        )
    items = variables.get(list_var)
    if not isinstance(items, list):
        return NodeResult(status="failed", error=f"loop variable {list_var!r} is not a list")

    state_key = f"__loop_index__{node['id']}"
    index = variables.get(state_key, 0)
    if not isinstance(index, int):
        index = 0
    max_iterations = config.get("max_iterations")
    capped = isinstance(max_iterations, int) and index >= max_iterations

    if index >= len(items) or capped:
        variables.pop(state_key, None)
        return NodeResult(status="completed", output={"done": True, "count": index}, branch="done")

    variables[item_var] = items[index]
    index_var = config.get("index_variable")
    if index_var and isinstance(index_var, str):
        variables[index_var] = index
    variables[state_key] = index + 1
    return NodeResult(status="completed", output={"item": items[index], "index": index}, branch="body")


async def _run_transform(node: dict[str, Any], variables: dict[str, Any], _executor: Any) -> NodeResult:
    """A whitelist of named string/number operations — never `eval()`, same rule as
    `evaluate_condition`. `concat`'s second operand is `with_variable` XOR `with_literal`,
    deliberately not one ambiguous key that would have to guess variable-name vs. literal."""
    config = node.get("config") or {}
    op = config.get("operation")
    var_path = config.get("variable")
    if op not in _TRANSFORM_OPS or not var_path or not isinstance(var_path, str):
        return NodeResult(
            status="failed",
            error=f"transform requires config.variable and one of {sorted(_TRANSFORM_OPS)} (got {op!r})",
        )
    value = variables.get(var_path)
    target = config.get("target_variable") or var_path
    try:
        if op == "uppercase":
            result: Any = str(value if value is not None else "").upper()
        elif op == "lowercase":
            result = str(value if value is not None else "").lower()
        elif op == "trim":
            result = str(value if value is not None else "").strip()
        elif op == "length":
            result = len(value) if value is not None else 0
        elif op == "to_number":
            result = float(value) if value is not None else 0.0
            if isinstance(result, float) and result.is_integer():
                result = int(result)
        else:  # concat
            with_var = config.get("with_variable")
            other = variables.get(with_var, "") if isinstance(with_var, str) else config.get("with_literal", "")
            result = f"{value if value is not None else ''}{other if other is not None else ''}"
    except (TypeError, ValueError) as exc:
        return NodeResult(status="failed", error=f"transform {op!r} failed: {exc}")
    variables[target] = result
    return NodeResult(status="completed", output={target: result})


async def _run_delay(
    node: dict[str, Any],
    variables: dict[str, Any],
    _executor: Any,
    *,
    resume_input: dict[str, Any] | None = None,
) -> NodeResult:
    """Pauses the run rather than blocking the worker — same first-visit-vs-resume split as
    `_run_approval`. On first visit, computes `resume_at` and reports `awaiting_delay`; the
    caller (the service layer, not this module — see `app.workflows.service
    ._schedule_delay_resume`) is responsible for actually scheduling a Celery task at that
    time and has no obligation to do so synchronously with this call. On resume (`resume_input`
    is any non-`None` dict — its contents are irrelevant here, only its presence signals "this
    is the wake-up, not the original visit"), completes immediately.

    Refuses outright rather than pausing when `settings.celery_task_always_eager` is on: eager
    mode has no worker to wake up later, so pausing would just strand the run forever. Failing
    loudly here is the same choice the original shape-only placeholder made — never silently
    no-op a "wait N seconds" step.
    """
    if resume_input is not None:
        return NodeResult(status="completed", output={})

    if settings.celery_task_always_eager:
        return NodeResult(
            status="failed",
            error="delay nodes cannot run under eager/synchronous execution — there is no "
            "worker to wake up later. Real wait semantics only work through the async "
            "(Celery) dispatch path.",
        )

    config = node.get("config") or {}
    duration = config.get("duration_seconds")
    if not isinstance(duration, int | float) or isinstance(duration, bool) or duration <= 0:
        return NodeResult(status="failed", error="delay requires a positive config.duration_seconds")
    if duration > settings.workflow_max_delay_seconds:
        return NodeResult(
            status="failed",
            error=f"delay duration {duration}s exceeds the maximum of "
            f"{settings.workflow_max_delay_seconds}s (WORKFLOW_MAX_DELAY_SECONDS)",
        )
    resume_at = dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=duration)
    return NodeResult(
        status="awaiting_delay",
        output={"resume_at": resume_at.isoformat(), "duration_seconds": duration},
    )


async def _run_agent(
    node: dict[str, Any],
    variables: dict[str, Any],
    agent_executor: WorkflowAgentExecutor | None,
    budget: AgentBudget,
) -> NodeResult:
    config = node.get("config") or {}
    agent_id = config.get("agent_id")
    if not agent_id or not isinstance(agent_id, str):
        return NodeResult(status="failed", error="agent node requires config.agent_id")
    if agent_executor is None:
        return NodeResult(status="failed", error="no agent executor configured for this run")
    message = render_workflow_template(config.get("message"), variables)
    out = await agent_executor(agent_id, message, budget)
    # Untrusted — the invoked agent's own KB or tools may be attacker-influenced (docs/17 §6,
    # no exception for "it's our own agent").
    sanitized_text = neutralize_injections(out.get("content") or "")
    result_variable = config.get("result_variable")
    if result_variable and isinstance(result_variable, str):
        variables[result_variable] = sanitized_text
    return NodeResult(
        status="failed" if out.get("status") == "error" else "completed",
        output={"sanitized_text": sanitized_text},
        error=out.get("error"),
    )


async def _run_sub_agent(
    node: dict[str, Any],
    variables: dict[str, Any],
    sub_workflow_executor: WorkflowSubExecutor | None,
    budget: AgentBudget,
) -> NodeResult:
    config = node.get("config") or {}
    workflow_id = config.get("workflow_id")
    if not workflow_id or not isinstance(workflow_id, str):
        return NodeResult(status="failed", error="sub_agent node requires config.workflow_id")
    if sub_workflow_executor is None:
        return NodeResult(status="failed", error="no sub-workflow executor configured for this run")
    if budget.call_depth >= budget.max_call_depth:
        return NodeResult(
            status="failed",
            error=f"sub-workflow call depth exceeded ({budget.max_call_depth}) — "
            "a direct or indirect cycle of sub_agent nodes is the likely cause",
        )
    budget.call_depth += 1
    try:
        nested = await sub_workflow_executor(workflow_id, dict(variables), budget)
    finally:
        budget.call_depth -= 1

    if nested.status == "paused_approval":
        return NodeResult(
            status="failed",
            error="nested workflow paused on an approval node — sub_agent does not yet "
            "support pausing the parent run on a nested approval",
        )
    if nested.status != "completed":
        return NodeResult(
            status="failed",
            error=nested.error or f"nested workflow ended with status {nested.status!r}",
        )
    # Only a sanitized summary crosses back — the nested run's raw variable bag is untrusted
    # output from the caller's perspective, identical treatment to a tool result.
    sanitized_text = neutralize_injections(json.dumps(nested.variables))
    result_variable = config.get("result_variable")
    if result_variable and isinstance(result_variable, str):
        variables[result_variable] = sanitized_text
    return NodeResult(status="completed", output={"sanitized_text": sanitized_text})


_HANDLERS: dict[str, Callable[..., Awaitable[NodeResult]]] = {
    "start": _run_start,
    "end": _run_end,
    "message": _run_message,
    "set_variable": _run_set_variable,
    "condition": _run_condition,
    "approval": _run_approval,
    "tool": _run_tool,
    "http": _run_http,
    "switch": _run_switch,
    "loop": _run_loop,
    "transform": _run_transform,
    "delay": _run_delay,
    # "agent"/"sub_agent" are dispatched specially below (they need `budget`, which this
    # dict-based signature doesn't carry) — registered here only so the unconditional
    # `_HANDLERS[node["type"]]` lookup above the dispatch doesn't KeyError.
    "agent": _run_agent,
    "sub_agent": _run_sub_agent,
}


def _next_node(
    node_id: str, edges: list[dict[str, Any]], branch: str | None
) -> str | None:
    candidates = [e for e in edges if e.get("source") == node_id]
    if not candidates:
        return None
    if branch is not None:
        for e in candidates:
            if e.get("condition") == branch:
                return e.get("target")
        return None  # no matching branch edge — the graph author didn't wire this outcome
    return candidates[0].get("target")


# ── The loop ─────────────────────────────────────────────────────────────────────────────


async def run_workflow(
    graph: dict[str, Any],
    *,
    variables: dict[str, Any],
    budget: AgentBudget,
    tool_executor: WorkflowToolExecutor | None = None,
    agent_executor: WorkflowAgentExecutor | None = None,
    sub_workflow_executor: WorkflowSubExecutor | None = None,
    start_node_id: str | None = None,
    resume_input: dict[str, Any] | None = None,
    on_step: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> WorkflowRunResult:
    """Execute `graph` starting at `start_node_id` (or the graph's `start` node), until it
    reaches an `end` node, pauses on an `approval` node, fails, or exhausts `budget`.

    `budget` is the SAME `AgentBudget` instance a caller may already be spending from (docs/17
    §2 rule 3) — this function never constructs its own. Passing a fresh one is the caller's
    decision to make, not this function's.

    Resuming a paused run: call again with `start_node_id` set to the `current_node_id` the
    previous `WorkflowRunResult` returned, and `resume_input={"decision": "approved"|
    "rejected"}`. Everything upstream of that node is not re-executed — this function has no
    memory of it, which is why the caller (the service layer, not this module) is responsible
    for persisting `variables` and `budget` between calls.

    `on_step`, if given, is awaited once per node visit, immediately after that node's step
    dict is recorded — before the next node runs. This is what lets a caller persist progress
    incrementally (docs/17 Phase 2 item 2: `GET .../steps` should show a long-running
    workflow's progress while it is still running, not only once it finishes). `result.steps`
    still accumulates the full list regardless, for callers that only want the final tally.
    """
    errors = validate_graph(graph)
    if errors:
        return WorkflowRunResult(status="failed", variables=variables, error="; ".join(errors))

    nodes_by_id = {n["id"]: n for n in graph["nodes"]}
    edges = graph.get("edges") or []
    current = start_node_id or next(n["id"] for n in graph["nodes"] if n["type"] == "start")
    steps: list[dict[str, Any]] = []
    first_iteration = True

    while True:
        node = nodes_by_id.get(current)
        if node is None:
            return WorkflowRunResult(
                status="failed", variables=variables, steps=steps, current_node_id=current,
                error=f"unknown node {current!r}",
            )
        if not budget.can_continue():
            return WorkflowRunResult(
                status="budget_exceeded", variables=variables, steps=steps, current_node_id=current,
            )

        handler = _HANDLERS[node["type"]]
        t0 = time.perf_counter()
        if node["type"] == "approval" or node["type"] == "delay":
            result = await handler(
                node, variables, tool_executor,
                resume_input=resume_input if first_iteration else None,
            )
        elif node["type"] in ("tool", "http"):
            budget.record_tool_calls(1)
            result = await handler(node, variables, tool_executor)
        elif node["type"] == "agent":
            result = await _run_agent(node, variables, agent_executor, budget)
        elif node["type"] == "sub_agent":
            result = await _run_sub_agent(node, variables, sub_workflow_executor, budget)
        else:
            result = await handler(node, variables, tool_executor)
        first_iteration = False
        latency_ms = int((time.perf_counter() - t0) * 1000)
        budget.record_step(cost_usd=result.cost_usd)

        step_record = {
            "node_id": current, "node_type": node["type"],
            "status": "awaiting_approval" if result.status == "awaiting_approval" else result.status,
            "input": None, "output": result.output, "latency_ms": latency_ms,
            "cost_usd": result.cost_usd, "error": result.error,
        }
        steps.append(step_record)
        if on_step is not None:
            await on_step(step_record)

        if result.status == "failed":
            return WorkflowRunResult(
                status="failed", variables=variables, steps=steps, current_node_id=current,
                error=result.error,
            )
        if result.status == "awaiting_approval":
            return WorkflowRunResult(
                status="paused_approval", variables=variables, steps=steps, current_node_id=current,
            )
        if result.status == "awaiting_delay":
            return WorkflowRunResult(
                status="paused_delay", variables=variables, steps=steps, current_node_id=current,
            )
        if node["type"] in _TERMINAL_TYPES:
            return WorkflowRunResult(status="completed", variables=variables, steps=steps, current_node_id=None)

        nxt = _next_node(current, edges, result.branch)
        if nxt is None:
            return WorkflowRunResult(
                status="failed", variables=variables, steps=steps, current_node_id=current,
                error=f"node {current!r} has no outgoing edge for branch {result.branch!r}",
            )
        current = nxt
