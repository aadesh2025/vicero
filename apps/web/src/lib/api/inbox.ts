"use client";

import { api } from "./client";
import { API_BASE } from "./config";
import { getAccessToken, getActiveOrgId } from "./tokens";
import type { ApiMessage } from "./conversations";

export interface ApiHandoff {
  id: string;
  status: string;
  requested_by: string;
  reason: string | null;
  assigned_to: string | null;
  notes: { by: string; text: string; at: string }[];
  tags: string[];
  created_at: string;
  resolved_at: string | null;
}

export interface ApiContact {
  id: string;
  display_name: string | null;
  avatar_url: string | null;
  /** Platform handle without the "@" (Instagram), when known. */
  username?: string | null;
  /** Set once this handle is matched to a CRM person — the link target for the name. */
  crm_contact_id: string | null;
}

export interface ApiInboxItem {
  id: string;
  agent_id: string;
  channel: string;
  status: string;
  title: string | null;
  channel_user_id: string | null;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
  handoff: ApiHandoff | null;
  contact: ApiContact | null;
  /** Open review severity (docs/11 §L6). Independent of `status`: an `elevated` conversation
   *  is still `active` and the bot is still replying. */
  attention_level: "mild" | "elevated" | "crisis" | null;
}

export interface ApiConversationFlag {
  id: string;
  kind: string;
  severity: "mild" | "elevated" | "crisis";
  /** Short spans the classifier quoted — never the whole message. */
  signals: string[];
  created_at: string;
  resolved_at: string | null;
}

export interface ApiAttentionItem extends ApiInboxItem {
  /** Full flag history, so the queue shows a trajectory rather than a final state. */
  flags: ApiConversationFlag[];
  recent_messages: ApiMessage[];
  /** False once a human takes over. Drives the "AI is still responding" indicator. */
  bot_still_answering: boolean;
}

/** Platform limits on replying right now. Null on channels that impose none. */
export interface ApiSendWindow {
  open: boolean;
  closes_at: string | null;
  /** Pre-approved template names, usable when the window is shut. */
  templates: string[];
}

export interface ApiInboxDetail extends ApiInboxItem {
  messages: ApiMessage[];
  send_window: ApiSendWindow | null;
}

/** `includeBot` is the "All chats" view: also the conversations the bot is handling, for triage. */
export function listInbox(status?: string, channel?: string, includeBot = false) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (channel) params.set("channel", channel);
  if (includeBot) params.set("include_bot", "true");
  const q = params.size ? `?${params}` : "";
  return api<ApiInboxItem[]>(`/v1/inbox/conversations${q}`, { orgScoped: true });
}

export function getInboxDetail(cid: string) {
  return api<ApiInboxDetail>(`/v1/inbox/conversations/${cid}`, { orgScoped: true });
}

export function takeover(cid: string) {
  return api<ApiInboxItem>(`/v1/inbox/conversations/${cid}/takeover`, { method: "POST", orgScoped: true });
}

export function handback(cid: string) {
  return api<ApiInboxItem>(`/v1/inbox/conversations/${cid}/handback`, { method: "POST", orgScoped: true });
}

export function closeConversation(cid: string) {
  return api<ApiInboxItem>(`/v1/inbox/conversations/${cid}/close`, { method: "POST", orgScoped: true });
}

export function replyInbox(cid: string, text: string) {
  return api<ApiMessage>(`/v1/inbox/conversations/${cid}/messages`, {
    method: "POST",
    orgScoped: true,
    body: { text },
  });
}

/** Re-open a WhatsApp conversation whose 24-hour free-form window has closed. */
export function sendTemplate(cid: string, template: string, params: string[] = []) {
  return api<ApiMessage>(`/v1/inbox/conversations/${cid}/template`, {
    method: "POST",
    orgScoped: true,
    body: { template, params },
  });
}

export function setTags(cid: string, tags: string[]) {
  return api<ApiHandoff>(`/v1/inbox/conversations/${cid}/tags`, { method: "POST", orgScoped: true, body: { tags } });
}

export function addNote(cid: string, text: string) {
  return api<ApiHandoff>(`/v1/inbox/conversations/${cid}/notes`, { method: "POST", orgScoped: true, body: { text } });
}

/** Open the operator inbox realtime stream. Returns the WebSocket (or null if not authed). */
export function openInboxSocket(): WebSocket | null {
  const token = getAccessToken();
  const org = getActiveOrgId();
  if (!token || !org) return null;
  const wsBase = API_BASE.replace(/^http/, "ws");
  return new WebSocket(`${wsBase}/v1/inbox/ws?token=${encodeURIComponent(token)}&org_id=${org}`);
}

/** Conversations flagged for a human to look at (docs/11 §L6).
 *
 * Deliberately a different endpoint from `listInbox`, not a filter on it: that one is the
 * handoff queue and selects conversations where the bot is *paused*, whereas an attention
 * conversation usually has no handoff at all because the bot is still answering.
 */
export function listAttention() {
  return api<ApiAttentionItem[]>("/v1/inbox/attention", { orgScoped: true });
}

/** Clear the flags — the only way a conversation's severity goes down. */
export function resolveAttention(cid: string) {
  return api<ApiInboxItem>(`/v1/inbox/conversations/${cid}/attention/resolve`, {
    method: "POST",
    orgScoped: true,
  });
}
