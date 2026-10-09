"use client";

import { api } from "./client";

export type ChannelType = "telegram" | "whatsapp" | "instagram" | "facebook" | "slack" | "discord";

export interface ApiChannel {
  id: string;
  agent_id: string;
  type: ChannelType;
  name: string | null;
  enabled: boolean;
  config: Record<string, unknown>;
  webhook_url: string | null;
  created_at: string;
  /** "manual" (pasted tokens) or "meta_oauth" (one-click connect). Absent on older API builds. */
  connection_source?: "manual" | "meta_oauth";
  /** Anything but "active" never sends (docs/26). */
  status?: "active" | "needs_reconnect" | "disconnected";
  external_id?: string | null;
  external_parent_id?: string | null;
  token_expires_at?: string | null;
  last_health_check_at?: string | null;
}

export function listChannels(agentId?: string) {
  const q = agentId ? `?agent_id=${agentId}` : "";
  return api<ApiChannel[]>(`/v1/channels${q}`, { orgScoped: true });
}

export function createChannel(body: {
  agent_id: string;
  type: ChannelType;
  name?: string;
  config: Record<string, unknown>;
}) {
  return api<ApiChannel>("/v1/channels", { method: "POST", orgScoped: true, body });
}

export function updateChannel(id: string, body: Record<string, unknown>) {
  return api<ApiChannel>(`/v1/channels/${id}`, { method: "PATCH", orgScoped: true, body });
}

export function deleteChannel(id: string) {
  return api<void>(`/v1/channels/${id}`, { method: "DELETE", orgScoped: true });
}

export function enableChannel(id: string) {
  return api<ApiChannel>(`/v1/channels/${id}/enable`, { method: "POST", orgScoped: true });
}

export function disableChannel(id: string) {
  return api<ApiChannel>(`/v1/channels/${id}/disable`, { method: "POST", orgScoped: true });
}
