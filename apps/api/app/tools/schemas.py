"""Tool, tool-run, and tool-test schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field


class BuiltinToolOut(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class CreateToolRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # "mcp" requires config.server_id + config.tool_name (an already-registered MCP server —
    # see POST /v1/mcp/servers). Live discovery happens via the server's test-connection
    # endpoint, not here, so creating a tool never makes an outbound call of its own.
    type: str = Field(pattern="^(builtin|http|mcp|api)$")
    agent_id: uuid.UUID | None = None
    # `api` tools only: the saved Connection that supplies base URL + auth (docs/28 §5.2).
    connection_id: uuid.UUID | None = None
    description: str | None = None
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    # Spoken to the visitor while the tool runs: {on_start, on_delay: {after_s, text}, on_error}. Plain text.
    messages: dict[str, Any] | None = None
    # A person must approve each call (a pending request appears in the Inbox) before it runs.
    approval_required: bool = False
    # {per_conversation, per_minute}: calls past the limit come back to the model as "rate_limited".
    rate_limit: dict[str, int] | None = None
    # `async` (API tools): return at once, run in the background, and tell the visitor when it finishes.
    mode: str = Field(default="sync", pattern="^(sync|async)$")


class UpdateToolRequest(BaseModel):
    description: str | None = None
    # Send null to detach the connection; leave out to keep it.
    connection_id: uuid.UUID | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    input_schema: dict[str, Any] | None = None
    # Send null to clear `messages` / `rate_limit`; leave out to keep them.
    messages: dict[str, Any] | None = None
    approval_required: bool | None = None
    rate_limit: dict[str, int] | None = None
    mode: str | None = Field(default=None, pattern="^(sync|async)$")


class ToolOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    connection_id: uuid.UUID | None = None
    name: str
    type: str
    description: str | None
    enabled: bool
    config: dict[str, Any]
    input_schema: dict[str, Any]
    created_at: dt.datetime
    messages: dict[str, Any] | None = None
    approval_required: bool = False
    rate_limit: dict[str, int] | None = None
    mode: str = "sync"


class TestToolRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class TestToolResponse(BaseModel):
    status: str
    output: dict[str, Any]
    error: str | None
    latency_ms: int
    # The request as it went out, with credentials masked (API tools). The test panel shows it beside the response.
    request: dict[str, Any] | None = None
    attempt: int = 1


class N8nWorkflowOut(BaseModel):
    id: str
    name: str
    active: bool
    webhook_url: str | None


class BindN8nRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    workflow_id: str | None = None
    workflow_name: str | None = None
    webhook_url: str | None = None
    mode: str = Field(default="sync", pattern="^(sync|async)$")
    agent_id: uuid.UUID | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None


class N8nCallbackRequest(BaseModel):
    run_id: uuid.UUID
    # Echo of the `callback_token` Vicero sent with the call (R2): proves this callback is for this run.
    callback_token: str = Field(min_length=16, max_length=128)
    output: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="success", pattern="^(success|error)$")
    error: str | None = None


class ToolRunPage(BaseModel):
    items: list[ToolRunOut]
    #: Pass back as `cursor` for the next (older) page; null on the last page.
    next_cursor: str | None = None


class ToolRunSummary(BaseModel):
    """One tool's recent activity, for the cards on the Tools tab."""

    tool_id: uuid.UUID
    calls_7d: int
    last_status: str | None
    last_run_at: dt.datetime | None


class ToolRunOut(BaseModel):
    id: uuid.UUID
    tool_id: uuid.UUID
    tool_name: str | None = None
    conversation_id: uuid.UUID | None
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    latency_ms: int | None
    error: str | None
    created_at: dt.datetime
    agent_id: uuid.UUID | None = None
    provider: str | None = None
    request_sanitized: dict[str, Any] | None = None
    attempt: int = 1


# ── Agent tool bindings (docs/28 T1.5) ───────────────────────────────────────────────────


class ToolBindingIn(BaseModel):
    tool_id: uuid.UUID
    enabled: bool = True
    # Per-agent overrides. T1 supports `description` (what the model reads for this agent).
    overrides: dict[str, Any] = Field(default_factory=dict)


class SetAgentToolsRequest(BaseModel):
    # The list order is the order the model sees the tools in.
    tools: list[ToolBindingIn] = Field(default_factory=list, max_length=100)


class ToolBindingOut(BaseModel):
    tool_id: uuid.UUID
    name: str
    type: str
    description: str | None
    enabled: bool
    position: int
    overrides: dict[str, Any]
    #: True for an MCP tool the agent's MCP binding created: it is chosen per server, not through this list.
    managed: bool = False


# ── MCP servers (docs/17 Phase 1 §4) ────────────────────────────────────────────────────


class CreateMCPServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # streamable_http is the current MCP standard and the default; sse is the older one. stdio is staff only.
    transport: str = Field(default="streamable_http", pattern="^(stdio|sse|streamable_http)$")
    # stdio: the command to spawn. sse / streamable_http: the server URL.
    url_or_command: str = Field(min_length=1)
    args: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    # Credentials from a saved Connection (header auth only). Its allowed hosts must include the server's host.
    connection_id: uuid.UUID | None = None
    enabled: bool = True


class UpdateMCPServerRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    url_or_command: str | None = Field(default=None, min_length=1)
    # Replaces the stored headers when sent (an empty object clears them). Never returned.
    headers: dict[str, str] | None = None
    # Send null to detach the connection; leave out to keep it.
    connection_id: uuid.UUID | None = None
    enabled: bool | None = None


class MCPCachedTool(BaseModel):
    name: str
    description: str


class MCPServerOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str | None = None
    transport: str
    url_or_command: str
    enabled: bool
    created_at: dt.datetime
    connection_id: uuid.UUID | None = None
    tools: list[MCPCachedTool] = Field(default_factory=list)
    tools_cached_at: dt.datetime | None = None
    last_error: str | None = None
    agents_count: int = 0


class MCPRefreshResponse(BaseModel):
    ok: bool
    tools: list[MCPCachedTool] = Field(default_factory=list)
    error: str | None = None


class MCPToolOverride(BaseModel):
    description: str | None = Field(default=None, max_length=1000)
    messages: dict[str, Any] | None = None
    approval_required: bool | None = None
    rate_limit: dict[str, int] | None = None


class MCPBindingIn(BaseModel):
    server_id: uuid.UUID
    enabled: bool = True
    # Only these tools reach the model. Empty means none, never all.
    enabled_tools: list[str] = Field(default_factory=list, max_length=100)
    tool_overrides: dict[str, MCPToolOverride] = Field(default_factory=dict)


class SetAgentMCPRequest(BaseModel):
    servers: list[MCPBindingIn] = Field(default_factory=list, max_length=50)


class MCPAvailableTool(BaseModel):
    name: str
    description: str
    exposed_as: str
    enabled: bool


class MCPBindingOut(BaseModel):
    server_id: uuid.UUID
    server_name: str
    slug: str | None
    enabled: bool
    enabled_tools: list[str]
    tool_overrides: dict[str, MCPToolOverride]
    available_tools: list[MCPAvailableTool]
    tools_cached_at: dt.datetime | None = None
    cache_stale: bool = False


class MCPToolInfo(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPTestConnectionResponse(BaseModel):
    ok: bool
    tools: list[MCPToolInfo] = Field(default_factory=list)
    error: str | None = None
