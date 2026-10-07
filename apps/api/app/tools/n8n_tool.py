"""Execution of n8n-bound tools (docs/07 §1).

Config shape (Tool.config):
    {"workflow_id": "...", "workflow_name": "...", "webhook_url": "...", "mode": "sync"|"async"}

- **sync**: POST the args to the workflow's webhook and feed n8n's "Respond to Webhook" JSON
  straight back to the model.
- **async**: POST args + a `callback_url` carrying this tool_run's id; return an "accepted"
  result immediately. n8n later calls the callback, which resolves the pending tool_run.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.errors import AppError
from app.integrations.n8n_client import callback_token, get_client
from app.tools.base import ToolContext, ToolResult

# The tool schema advertises a single `args` property, but a workflow's real inputs are
# arbitrary, so models produce several shapes for it. Declaring `args` without a JSON-Schema
# type keeps the provider from rejecting the tool call (a rejection ends the turn with an
# empty reply); normalizing here is what makes every shape reach n8n identically.
N8N_ARGS_DESCRIPTION = (
    "A JSON object of arguments to send to the workflow, "
    'e.g. {"customer": "Acme", "priority": "high"}.'
)


def n8n_args_schema() -> dict[str, Any]:
    """The input schema advertised for an n8n tool."""
    return {"type": "object", "properties": {"args": {"description": N8N_ARGS_DESCRIPTION}}}


def relax_n8n_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Drop a `type` constraint on `args` so a non-object tool call still validates.

    Tools bound before this fix stored ``args: {"type": "object"}``; a model that answered
    with a string there had its tool call rejected by the provider, which surfaced to the
    end user as a blank reply. Repairing on read avoids a data migration.
    """
    if not schema:
        return n8n_args_schema()
    props = schema.get("properties")
    if not isinstance(props, dict) or not isinstance(props.get("args"), dict):
        return schema
    args_schema = {k: v for k, v in props["args"].items() if k != "type"}
    args_schema.setdefault("description", N8N_ARGS_DESCRIPTION)
    return {**schema, "properties": {**props, "args": args_schema}}


def normalize_n8n_args(raw: Any) -> dict[str, Any]:
    """Reduce model-produced tool arguments to the flat object the workflow receives.

    Accepts the documented ``{"args": {...}}`` wrapper, the ``{"args": "<json or text>"}`` a
    model often emits instead, and a flat ``{"customer": "Acme"}``. Without this the wrapper
    shape reached n8n double-wrapped as ``{"args": {"args": {...}}}``, which did not match
    what the tool `/test` endpoint sends.
    """
    if not isinstance(raw, dict):
        return {"input": raw}
    if set(raw) != {"args"}:
        return raw
    inner = raw["args"]
    if isinstance(inner, dict):
        return inner
    if isinstance(inner, str):
        try:
            parsed = json.loads(inner)
        except ValueError:
            return {"input": inner}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    return {"input": inner}


async def execute_n8n_tool(config: dict[str, Any], args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    webhook_url = str(config.get("webhook_url", "")).strip()
    mode = str(config.get("mode", "sync")).lower()
    if not webhook_url:
        return ToolResult(output={}, status="error", error="n8n tool has no webhook_url configured")

    payload: dict[str, Any] = {
        "args": normalize_n8n_args(args),
        "mode": mode,
        "run_id": str(ctx.run_id) if ctx.run_id else None,
        "conversation_id": str(ctx.conversation_id) if ctx.conversation_id else None,
    }
    if mode == "async":
        payload["callback_url"] = f"{settings.api_base_url.rstrip('/')}/v1/tools/n8n/callback"
        # The workflow must echo this back; it only works for THIS run in THIS org (R2 callback binding).
        payload["callback_token"] = callback_token(ctx.run_id, ctx.org_id)

    client = get_client()
    try:
        status_code, data = await client.trigger_webhook(webhook_url, payload)
    except AppError as exc:
        return ToolResult(output={}, status="error", error=exc.message)

    if mode == "async":
        return ToolResult(
            output={
                "status": "accepted",
                "run_id": payload["run_id"],
                "note": "n8n is processing asynchronously; the result will be recorded via callback.",
            },
            status="pending",
        )

    if status_code >= 400:
        return ToolResult(output={"response": data}, status="error", error=f"n8n returned {status_code}")
    return ToolResult(output={"response": data}, status="success")
