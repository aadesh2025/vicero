"""Structural diff between two `WorkflowVersion.graph` documents (docs/17 Phase 4).

Pure static analysis over the graph JSON — no `eval()`, no graph execution, no DB access.
Consumed by `app.workflows.service.diff_versions` for
`GET /v1/workflows/{id}/versions/{a}/diff/{b}`.

The Phase 4 Definition of Done (`docs/17-IMPLEMENTATION-PROMPT.md`) asks for three things,
each its own section below: node/edge changes, tool/MCP-server changes, variable schema
changes. All three are computed from the same two node-id-keyed maps rather than a generic
JSON diff, so a node's config changing (e.g. a tool node's arguments) is reported distinctly
from a node being added or removed — the DoD's own example.

**Per-node-type variable read/write extraction mirrors `app.workflows.graph`'s node handlers
exactly** (which config key each node type reads a variable from / writes one to). This is a
*twin* of that runtime logic, not a call into it: the graph handlers work against a live
`variables` dict during execution, while this needs the same information at rest, from JSON
that has never run. `_COND_RE` and `_VAR_PATTERN` ARE imported from `graph.py` rather than
copied, so condition-expression and `{{var}}` template parsing can't drift from what actually
executes — but the per-node-type key tables below (`_WRITES`, `_READ_VAR_KEYS`,
`_TEMPLATE_KEYS`) are hand-maintained and must be updated by hand alongside any new node type
or renamed config key in `graph.py`.

Tool/MCP-server references: a `tool` node's `config.tool_name` is diffed as-is. Builtin and
MCP-provided tools are dispatched through the same name (`app.tools.service.resolve_agent_tools`
returns both in one `by_name` map when `include_mcp=True`), so one reference list already covers
both without needing to know which is which.
"""

from __future__ import annotations

from typing import Any

from app.workflows.graph import _COND_RE, _VAR_PATTERN

# node type -> config keys naming a variable that node type WRITES (verbatim, not a template).
_WRITES: dict[str, tuple[str, ...]] = {
    "set_variable": ("key",),
    "tool": ("result_variable",),
    "http": ("result_variable",),
    "agent": ("result_variable",),
    "sub_agent": ("result_variable",),
    "loop": ("item_variable", "index_variable"),
}

# node type -> config keys naming a variable that node type READS directly (not a template).
_READ_VAR_KEYS: dict[str, tuple[str, ...]] = {
    "switch": ("variable",),
    "transform": ("variable", "with_variable"),
    "loop": ("list_variable",),
}

# node type -> config keys whose value is a `{{var}}` template (`render_workflow_template`),
# scanned for zero or more variable references.
_TEMPLATE_KEYS: dict[str, tuple[str, ...]] = {
    "message": ("content",),
    "approval": ("message",),
    "agent": ("message",),
    "set_variable": ("value",),
}


def _node_config(node: dict[str, Any]) -> dict[str, Any]:
    config = node.get("config")
    return config if isinstance(config, dict) else {}


def _extract_template_vars(text: Any) -> set[str]:
    if not isinstance(text, str):
        return set()
    return set(_VAR_PATTERN.findall(text))


def _node_reads(node: dict[str, Any]) -> set[str]:
    ntype = node.get("type") or ""
    config = _node_config(node)
    reads: set[str] = set()
    if ntype == "condition":
        match = _COND_RE.match(config.get("expression") or "")
        if match:
            reads.add(match.group(1))
    for key in _READ_VAR_KEYS.get(ntype, ()):
        val = config.get(key)
        if isinstance(val, str) and val:
            reads.add(val)
    for key in _TEMPLATE_KEYS.get(ntype, ()):
        reads |= _extract_template_vars(config.get(key))
    if ntype == "http":
        # Every string in the request template (url, path, query, headers, body) may reference variables;
        # `{{vars.x}}` is the same variable as `{{x}}`.
        for text in _strings_in({k: v for k, v in config.items() if k != "result_variable"}):
            reads |= {r.removeprefix("vars.") for r in _extract_template_vars(text)}
    if ntype == "tool":
        # A tool node's string arguments are template-rendered (graph.py `_run_tool`);
        # non-string arguments are passed through literally and reference nothing.
        for v in (config.get("arguments") or {}).values():
            reads |= _extract_template_vars(v)
    return reads


def _strings_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings_in(v)]
    return []


def _node_writes(node: dict[str, Any]) -> set[str]:
    ntype = node.get("type") or ""
    config = _node_config(node)
    writes: set[str] = set()
    for key in _WRITES.get(ntype, ()):
        val = config.get(key)
        if isinstance(val, str) and val:
            writes.add(val)
    if ntype == "transform":
        # `graph.py::_run_transform`: target = config.target_variable or config.variable.
        target = config.get("target_variable") or config.get("variable")
        if isinstance(target, str) and target:
            writes.add(target)
    return writes


def _node_tool_refs(node: dict[str, Any]) -> set[str]:
    if node.get("type") != "tool":
        return set()
    name = _node_config(node).get("tool_name")
    return {name} if isinstance(name, str) and name else set()


def _nodes_by_id(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return {}
    return {n["id"]: n for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)}


def _edge_key(edge: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (edge.get("source"), edge.get("target"), edge.get("condition"))


def _edge_out(key: tuple[Any, Any, Any]) -> dict[str, Any]:
    source, target, condition = key
    return {"source": source, "target": target, "condition": condition}


def _edge_sort_key(key: tuple[Any, Any, Any]) -> tuple[str, str, str]:
    source, target, condition = key
    return (source or "", target or "", condition or "")


def diff_graphs(old_graph: dict[str, Any], new_graph: dict[str, Any]) -> dict[str, Any]:
    """Returns a plain dict matching `WorkflowVersionDiffOut`'s field names (minus
    `from_version`/`to_version`, which the caller fills in — this function only ever sees the
    two graph documents, never the `WorkflowVersion` rows they came from)."""
    old_nodes = _nodes_by_id(old_graph)
    new_nodes = _nodes_by_id(new_graph)
    old_ids, new_ids = set(old_nodes), set(new_nodes)

    nodes_added = [{"id": i, "type": new_nodes[i].get("type")} for i in sorted(new_ids - old_ids)]
    nodes_removed = [{"id": i, "type": old_nodes[i].get("type")} for i in sorted(old_ids - new_ids)]

    nodes_changed: list[dict[str, Any]] = []
    for node_id in sorted(old_ids & new_ids):
        old_node, new_node = old_nodes[node_id], new_nodes[node_id]
        old_type, new_type = old_node.get("type"), new_node.get("type")
        old_config, new_config = _node_config(old_node), _node_config(new_node)
        type_changed = old_type != new_type
        config_changed = old_config != new_config
        if type_changed or config_changed:
            nodes_changed.append({
                "id": node_id,
                "type_changed": type_changed,
                "old_type": old_type,
                "new_type": new_type,
                "config_changed": config_changed,
                "old_config": old_config,
                "new_config": new_config,
            })

    old_edges = {_edge_key(e) for e in (old_graph.get("edges") or []) if isinstance(e, dict)}
    new_edges = {_edge_key(e) for e in (new_graph.get("edges") or []) if isinstance(e, dict)}
    edges_added = [_edge_out(k) for k in sorted(new_edges - old_edges, key=_edge_sort_key)]
    edges_removed = [_edge_out(k) for k in sorted(old_edges - new_edges, key=_edge_sort_key)]

    old_tools = set().union(*(_node_tool_refs(n) for n in old_nodes.values()))
    new_tools = set().union(*(_node_tool_refs(n) for n in new_nodes.values()))
    old_reads = set().union(*(_node_reads(n) for n in old_nodes.values()))
    new_reads = set().union(*(_node_reads(n) for n in new_nodes.values()))
    old_writes = set().union(*(_node_writes(n) for n in old_nodes.values()))
    new_writes = set().union(*(_node_writes(n) for n in new_nodes.values()))

    return {
        "nodes_added": nodes_added,
        "nodes_removed": nodes_removed,
        "nodes_changed": nodes_changed,
        "edges_added": edges_added,
        "edges_removed": edges_removed,
        "tools_added": sorted(new_tools - old_tools),
        "tools_removed": sorted(old_tools - new_tools),
        "variables_read_added": sorted(new_reads - old_reads),
        "variables_read_removed": sorted(old_reads - new_reads),
        "variables_written_added": sorted(new_writes - old_writes),
        "variables_written_removed": sorted(old_writes - new_writes),
    }
