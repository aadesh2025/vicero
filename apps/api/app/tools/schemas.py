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
    type: str = Field(pattern="^(builtin|http|mcp)$")
    agent_id: uuid.UUID | None = None
    description: str | None = None
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class UpdateToolRequest(BaseModel):
    description: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    input_schema: dict[str, Any] | None = None


class ToolOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    name: str
    type: str
    description: str | None
    enabled: bool
    config: dict[str, Any]
    input_schema: dict[str, Any]
    created_at: dt.datetime


class TestToolRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class TestToolResponse(BaseModel):
    status: str
    output: dict[str, Any]
    error: str | None
    latency_ms: int


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


class ToolRunOut(BaseModel):
    id: uuid.UUID
    tool_id: uuid.UUID
    conversation_id: uuid.UUID | None
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    latency_ms: int | None
    error: str | None
    created_at: dt.datetime


# ── MCP servers (docs/17 Phase 1 §4) ────────────────────────────────────────────────────


class CreateMCPServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    transport: str = Field(pattern="^(stdio|sse)$")
    # stdio: the command to spawn. sse: the server URL.
    url_or_command: str = Field(min_length=1)
    args: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    enabled: bool = True


class MCPServerOut(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    url_or_command: str
    enabled: bool
    created_at: dt.datetime


class MCPToolInfo(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPTestConnectionResponse(BaseModel):
    ok: bool
    tools: list[MCPToolInfo] = Field(default_factory=list)
    error: str | None = None
