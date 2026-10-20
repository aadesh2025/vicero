"use client";

import { api } from "./client";
import type { ToolMessages, ToolRateLimit } from "./tools";

// Per-agent MCP (docs/28 T2). A server is registered once for the workspace; each agent gets an explicit allowlist.

export type McpTransport = "streamable_http" | "sse" | "stdio";

export interface McpCachedTool {
  name: string;
  description: string;
}

export interface McpServer {
  id: string;
  name: string;
  slug: string | null;
  transport: McpTransport;
  url_or_command: string;
  enabled: boolean;
  created_at: string;
  connection_id: string | null;
  tools: McpCachedTool[];
  tools_cached_at: string | null;
  last_error: string | null;
  agents_count: number;
}

export interface McpServerBody {
  name: string;
  transport?: McpTransport;
  url_or_command: string;
  headers?: Record<string, string>;
  connection_id?: string | null;
  enabled?: boolean;
}

export interface McpRefreshResult {
  ok: boolean;
  tools: McpCachedTool[];
  error: string | null;
}

export interface McpToolOverride {
  description?: string | null;
  messages?: ToolMessages | null;
  approval_required?: boolean | null;
  rate_limit?: ToolRateLimit | null;
}

export interface McpBinding {
  server_id: string;
  server_name: string;
  slug: string | null;
  enabled: boolean;
  enabled_tools: string[];
  tool_overrides: Record<string, McpToolOverride>;
  available_tools: {
    name: string;
    description: string;
    exposed_as: string;
    enabled: boolean;
  }[];
  tools_cached_at: string | null;
  cache_stale: boolean;
}

/** More than this and the model picks worse and the prompt costs more (docs/28 §5.3). */
export const MAX_TOOLS_PER_AGENT = 20;

export const listMcpServers = () =>
  api<McpServer[]>("/v1/mcp/servers", { orgScoped: true });

export const createMcpServer = (body: McpServerBody) =>
  api<McpServer>("/v1/mcp/servers", { method: "POST", orgScoped: true, body });

export const updateMcpServer = (
  id: string,
  body: Partial<Omit<McpServerBody, "transport">>,
) =>
  api<McpServer>(`/v1/mcp/servers/${id}`, {
    method: "PATCH",
    orgScoped: true,
    body,
  });

export const deleteMcpServer = (id: string) =>
  api<void>(`/v1/mcp/servers/${id}`, { method: "DELETE", orgScoped: true });

export const refreshMcpTools = (id: string) =>
  api<McpRefreshResult>(`/v1/mcp/servers/${id}/refresh-tools`, {
    method: "POST",
    orgScoped: true,
  });

export const testMcpServer = (id: string) =>
  api<{
    ok: boolean;
    tools: { name: string; description: string }[];
    error: string | null;
  }>(`/v1/mcp/servers/${id}/test-connection`, {
    method: "POST",
    orgScoped: true,
  });

export const listAgentMcp = (agentId: string) =>
  api<McpBinding[]>(`/v1/agents/${agentId}/mcp-servers`, { orgScoped: true });

export const setAgentMcp = (
  agentId: string,
  servers: {
    server_id: string;
    enabled?: boolean;
    enabled_tools: string[];
    tool_overrides?: Record<string, McpToolOverride>;
  }[],
) =>
  api<McpBinding[]>(`/v1/agents/${agentId}/mcp-servers`, {
    method: "PUT",
    orgScoped: true,
    body: { servers },
  });
