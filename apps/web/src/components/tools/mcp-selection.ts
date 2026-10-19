/** Which of a server's tools one agent may use, as the list the API takes (docs/28 T2.5, T5.4). Pure so the rules
 *  (empty means none, the 20-tool ceiling, overrides follow their tool) are tested without a browser. */

import {
  MAX_TOOLS_PER_AGENT,
  type McpBinding,
  type McpToolOverride,
} from "@/lib/api/mcp";

export interface BindingPayload {
  server_id: string;
  enabled: boolean;
  enabled_tools: string[];
  tool_overrides: Record<string, McpToolOverride>;
}

export function toPayload(bindings: McpBinding[]): BindingPayload[] {
  return bindings.map((b) => ({
    server_id: b.server_id,
    enabled: b.enabled,
    enabled_tools: [...b.enabled_tools],
    tool_overrides: { ...b.tool_overrides },
  }));
}

/** MCP tools switched on across all servers (a disabled binding offers none). */
export function enabledCount(bindings: McpBinding[]): number {
  return bindings.reduce(
    (n, b) => n + (b.enabled ? b.enabled_tools.length : 0),
    0,
  );
}

/** Room left under the ceiling, given the agent's other enabled tools. May be negative if it is already over. */
export function roomLeft(
  bindings: McpBinding[],
  otherEnabledTools: number,
): number {
  return MAX_TOOLS_PER_AGENT - otherEnabledTools - enabledCount(bindings);
}

/** The payload after switching one tool on or off. A server whose last tool goes off, and has no overrides left, is
 *  unbound. Returns null when switching a tool on would pass the ceiling. */
export function toggleTool(
  bindings: McpBinding[],
  serverId: string,
  tool: string,
  on: boolean,
  otherEnabledTools: number,
): BindingPayload[] | null {
  if (
    on &&
    roomLeft(bindings, otherEnabledTools) <= 0 &&
    !isEnabled(bindings, serverId, tool)
  )
    return null;
  const payload = toPayload(bindings);
  let entry = payload.find((p) => p.server_id === serverId);
  if (!entry) {
    entry = {
      server_id: serverId,
      enabled: true,
      enabled_tools: [],
      tool_overrides: {},
    };
    payload.push(entry);
  }
  const set = new Set(entry.enabled_tools);
  if (on) set.add(tool);
  else {
    set.delete(tool);
    delete entry.tool_overrides[tool];
  }
  entry.enabled_tools = [...set].sort();
  return payload.filter(
    (p) =>
      p.enabled_tools.length > 0 || Object.keys(p.tool_overrides).length > 0,
  );
}

export function isEnabled(
  bindings: McpBinding[],
  serverId: string,
  tool: string,
): boolean {
  const b = bindings.find((x) => x.server_id === serverId);
  return Boolean(b && b.enabled && b.enabled_tools.includes(tool));
}

/** Replace one tool's overrides (empty object clears them). */
export function setOverride(
  bindings: McpBinding[],
  serverId: string,
  tool: string,
  override: McpToolOverride,
): BindingPayload[] {
  const payload = toPayload(bindings);
  const entry = payload.find((p) => p.server_id === serverId);
  if (!entry) return payload;
  const clean: McpToolOverride = {};
  if (override.description?.trim())
    clean.description = override.description.trim();
  if (override.approval_required) clean.approval_required = true;
  if (override.rate_limit && Object.keys(override.rate_limit).length)
    clean.rate_limit = override.rate_limit;
  if (override.messages && Object.keys(override.messages).length)
    clean.messages = override.messages;
  if (Object.keys(clean).length) entry.tool_overrides[tool] = clean;
  else delete entry.tool_overrides[tool];
  return payload;
}
