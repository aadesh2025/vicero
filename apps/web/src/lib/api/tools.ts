"use client";

import { api } from "./client";

export interface ApiBuiltinTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export type ToolType = "builtin" | "http" | "n8n" | "mcp" | "api";

/** What the visitor is told while a tool runs (docs/28 T4.2). Plain text. */
export interface ToolMessages {
  on_start?: string;
  on_delay?: { after_s: number; text: string };
  on_error?: string;
}

export interface ToolRateLimit {
  per_conversation?: number;
  per_minute?: number;
}

export interface ApiTool {
  id: string;
  agent_id: string | null;
  connection_id: string | null;
  name: string;
  type: ToolType;
  description: string | null;
  enabled: boolean;
  config: Record<string, unknown>;
  input_schema: Record<string, unknown>;
  created_at: string;
  messages: ToolMessages | null;
  approval_required: boolean;
  rate_limit: ToolRateLimit | null;
  mode: "sync" | "async";
}

export interface ApiToolRun {
  id: string;
  tool_id: string;
  tool_name: string | null;
  conversation_id: string | null;
  status: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
  agent_id: string | null;
  provider: string | null;
  /** The request as sent, with credentials masked. */
  request_sanitized: Record<string, unknown> | null;
  attempt: number;
}

export interface ApiToolRunPage {
  items: ApiToolRun[];
  next_cursor: string | null;
}

export interface ApiToolRunSummary {
  tool_id: string;
  calls_7d: number;
  last_status: string | null;
  last_run_at: string | null;
}

export interface ApiToolTestResult {
  status: string;
  output: Record<string, unknown>;
  error: string | null;
  latency_ms: number;
  request: Record<string, unknown> | null;
  attempt: number;
}

export interface ToolBody {
  name: string;
  type: ToolType;
  agent_id?: string;
  connection_id?: string | null;
  description?: string;
  config?: Record<string, unknown>;
  input_schema?: Record<string, unknown>;
  messages?: ToolMessages | null;
  approval_required?: boolean;
  rate_limit?: ToolRateLimit | null;
  mode?: "sync" | "async";
}

export function listBuiltinTools() {
  return api<ApiBuiltinTool[]>("/v1/tools/builtin", { orgScoped: true });
}

export function listTools(agentId?: string) {
  const q = agentId ? `?agent_id=${agentId}` : "";
  return api<ApiTool[]>(`/v1/tools${q}`, { orgScoped: true });
}

export function createTool(body: ToolBody) {
  return api<ApiTool>("/v1/tools", { method: "POST", orgScoped: true, body });
}

export function updateTool(
  id: string,
  body: Partial<Omit<ToolBody, "name" | "type" | "agent_id">> & {
    enabled?: boolean;
  },
) {
  return api<ApiTool>(`/v1/tools/${id}`, {
    method: "PATCH",
    orgScoped: true,
    body,
  });
}

export function deleteTool(id: string) {
  return api<void>(`/v1/tools/${id}`, { method: "DELETE", orgScoped: true });
}

export function testTool(id: string, input: Record<string, unknown>) {
  return api<ApiToolTestResult>(`/v1/tools/${id}/test`, {
    method: "POST",
    orgScoped: true,
    body: { input },
  });
}

export function listToolRuns(conversationId?: string) {
  const q = conversationId ? `?conversation_id=${conversationId}` : "";
  return api<ApiToolRun[]>(`/v1/tools/runs${q}`, { orgScoped: true });
}

export function listToolRunPage(opts: {
  agentId?: string;
  toolId?: string;
  status?: string;
  cursor?: string | null;
  limit?: number;
}) {
  const p = new URLSearchParams();
  if (opts.agentId) p.set("agent_id", opts.agentId);
  if (opts.toolId) p.set("tool_id", opts.toolId);
  if (opts.status) p.set("status", opts.status);
  if (opts.cursor) p.set("cursor", opts.cursor);
  if (opts.limit) p.set("limit", String(opts.limit));
  const q = p.size ? `?${p}` : "";
  return api<ApiToolRunPage>(`/v1/tool-runs${q}`, { orgScoped: true });
}

export function toolRunSummary(agentId?: string) {
  const q = agentId ? `?agent_id=${agentId}` : "";
  return api<ApiToolRunSummary[]>(`/v1/tool-runs/summary${q}`, {
    orgScoped: true,
  });
}

// ── Which tools an agent uses (docs/28 T1.5) ─────────────────────────────────
export interface ToolBinding {
  tool_id: string;
  name: string;
  type: ToolType;
  description: string | null;
  enabled: boolean;
  position: number;
  overrides: Record<string, unknown>;
  /** An MCP tool created by the agent's MCP selection: chosen per server, never through this list. */
  managed: boolean;
}

export const listAgentTools = (agentId: string) =>
  api<ToolBinding[]>(`/v1/agents/${agentId}/tools`, { orgScoped: true });

export const setAgentTools = (
  agentId: string,
  tools: {
    tool_id: string;
    enabled?: boolean;
    overrides?: Record<string, unknown>;
  }[],
) =>
  api<ToolBinding[]>(`/v1/agents/${agentId}/tools`, {
    method: "PUT",
    orgScoped: true,
    body: { tools },
  });

// ── n8n ──────────────────────────────────────────────────────────────────────
export interface ApiN8nWorkflow {
  id: string;
  name: string;
  active: boolean;
  webhook_url: string | null;
}

export function listN8nWorkflows() {
  return api<ApiN8nWorkflow[]>("/v1/tools/n8n/workflows", { orgScoped: true });
}

export function bindN8nWorkflow(body: {
  name: string;
  workflow_id?: string;
  workflow_name?: string;
  webhook_url?: string | null;
  mode: "sync" | "async";
  agent_id?: string;
  description?: string;
}) {
  return api<ApiTool>("/v1/tools/n8n/bind", {
    method: "POST",
    orgScoped: true,
    body,
  });
}
