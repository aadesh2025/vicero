"use client";

import { api, apiStream } from "./client";

export interface ApiAttachment {
  media_id: string;
  kind: "image";
  direction: "in" | "out";
  description: string | null;
}

export interface ApiMessage {
  id: string;
  role: string;
  content: string | null;
  tool_calls: Record<string, unknown> | null;
  tool_call_id: string | null;
  citations: unknown[];
  /** Photos on this message (docs/26). Absent on older API responses. */
  attachments?: ApiAttachment[];
  provider: string | null;
  model: string | null;
  tokens_prompt: number;
  tokens_completion: number;
  cost_micros: number;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface ApiConversationContact {
  id: string;
  display_name: string | null;
  avatar_url: string | null;
  /** Platform handle without the "@" (Instagram), when known. */
  username?: string | null;
  crm_contact_id?: string | null;
}

export interface ApiConversation {
  id: string;
  agent_id: string;
  channel: string;
  status: string;
  title: string | null;
  /** Who is on the other end. Null for widget/playground chats and threads that predate contact resolution. */
  contact?: ApiConversationContact | null;
  channel_user_id?: string | null;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiConversationDetail extends ApiConversation {
  memory_summary: string | null;
  messages: ApiMessage[];
}

export function listConversations(agentId?: string) {
  const q = agentId ? `?agent_id=${agentId}` : "";
  return api<ApiConversation[]>(`/v1/conversations${q}`, { orgScoped: true });
}

export function getConversation(cid: string) {
  return api<ApiConversationDetail>(`/v1/conversations/${cid}`, { orgScoped: true });
}

export function updateConversation(cid: string, body: { title?: string; status?: string }) {
  return api<ApiConversation>(`/v1/conversations/${cid}`, { method: "PATCH", orgScoped: true, body });
}

export function deleteConversation(cid: string) {
  return api<void>(`/v1/conversations/${cid}`, { method: "DELETE", orgScoped: true });
}

/** Stream a persisted chat turn. Yields parsed StreamEvents. */
export function chatStream(
  agentId: string,
  message: string,
  conversationId?: string,
  signal?: AbortSignal,
) {
  return apiStream(
    `/v1/agents/${agentId}/chat`,
    { message, conversation_id: conversationId, stream: true },
    signal,
  );
}
