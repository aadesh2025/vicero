"use client";

import { api } from "./client";

// Inbound trigger hooks and tool approvals (docs/28 T3, T4).

export type HookAction =
  "send_message" | "run_workflow" | "set_variables" | "agent_turn";
export type HookAuth = "hmac" | "bearer";

export interface InboundHook {
  id: string;
  agent_id: string;
  name: string;
  action: HookAction;
  action_config: Record<string, unknown>;
  payload_mapping: Record<string, string>;
  auth_mode: HookAuth;
  enabled: boolean;
  /** Path on the API host; prefix it with your API base URL. */
  url_path: string;
  last_called_at: string | null;
  created_at: string;
  /** Only on create and rotate-secret: shown once. */
  secret: string | null;
}

export interface InboundHookBody {
  name: string;
  action: HookAction;
  action_config: Record<string, unknown>;
  payload_mapping: Record<string, string>;
  auth_mode: HookAuth;
  enabled?: boolean;
}

export interface InboundHookEvent {
  id: string;
  status: string;
  http_status: number | null;
  latency_ms: number | null;
  payload_sanitized: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
}

export const listInboundHooks = (agentId: string) =>
  api<InboundHook[]>(`/v1/agents/${agentId}/inbound-hooks`, {
    orgScoped: true,
  });

export const createInboundHook = (agentId: string, body: InboundHookBody) =>
  api<InboundHook>(`/v1/agents/${agentId}/inbound-hooks`, {
    method: "POST",
    orgScoped: true,
    body,
  });

export const updateInboundHook = (
  id: string,
  body: Partial<
    Pick<
      InboundHookBody,
      "name" | "action_config" | "payload_mapping" | "enabled"
    >
  >,
) =>
  api<InboundHook>(`/v1/inbound-hooks/${id}`, {
    method: "PATCH",
    orgScoped: true,
    body,
  });

export const deleteInboundHook = (id: string) =>
  api<void>(`/v1/inbound-hooks/${id}`, { method: "DELETE", orgScoped: true });

export const rotateInboundHookSecret = (id: string) =>
  api<InboundHook>(`/v1/inbound-hooks/${id}/rotate-secret`, {
    method: "POST",
    orgScoped: true,
  });

export const listInboundHookEvents = (id: string, limit = 20) =>
  api<InboundHookEvent[]>(`/v1/inbound-hooks/${id}/events?limit=${limit}`, {
    orgScoped: true,
  });

// ── Tool approvals (Inbox) ───────────────────────────────────────────────────
export interface ToolApproval {
  handoff_id: string;
  conversation_id: string | null;
  tool: string;
  /** Redacted: secret-looking keys are masked. */
  args: Record<string, unknown>;
  status: string;
  decision: string | null;
  created_at: string;
  resolved_at: string | null;
}

export const listToolApprovals = (includeResolved = false) =>
  api<ToolApproval[]>(
    `/v1/inbox/tool-approvals${includeResolved ? "?include_resolved=true" : ""}`,
    { orgScoped: true },
  );

export const decideToolApproval = (
  handoffId: string,
  decision: "approved" | "rejected",
) =>
  api<ToolApproval>(`/v1/inbox/tool-approvals/${handoffId}/decide`, {
    method: "POST",
    orgScoped: true,
    body: { decision },
  });
