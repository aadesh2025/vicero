"use client";

import { api } from "./client";

// ── API keys ─────────────────────────────────────────────────────────────────
export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export function listApiKeys() {
  return api<ApiKey[]>("/v1/apikeys", { orgScoped: true });
}

export function createApiKey(name: string, scopes: string[] = []) {
  return api<ApiKeyCreated>("/v1/apikeys", { method: "POST", orgScoped: true, body: { name, scopes } });
}

export function revokeApiKey(id: string) {
  return api<ApiKey>(`/v1/apikeys/${id}/revoke`, { method: "POST", orgScoped: true });
}

export function deleteApiKey(id: string) {
  return api<void>(`/v1/apikeys/${id}`, { method: "DELETE", orgScoped: true });
}

// ── Webhooks ─────────────────────────────────────────────────────────────────
export interface Webhook {
  id: string;
  url: string;
  /** Set when the endpoint only receives one agent's events (docs/28 T3). */
  agent_id?: string | null;
  events: string[];
  enabled: boolean;
  secret: string | null;
  created_at: string;
}

export interface WebhookDelivery {
  id: string;
  event: string;
  status: string;
  attempts: number;
  response_status: number | null;
  next_retry_at: string | null;
  created_at: string;
}

export function listWebhooks() {
  return api<Webhook[]>("/v1/webhooks", { orgScoped: true });
}

/** The endpoints that belong to one agent (they receive only that agent's events). */
export function listAgentWebhooks(agentId: string) {
  return api<Webhook[]>(`/v1/webhooks?agent_id=${agentId}`, { orgScoped: true });
}

export function createAgentWebhook(agentId: string, url: string, events: string[]) {
  return api<Webhook>("/v1/webhooks", { method: "POST", orgScoped: true, body: { url, events, agent_id: agentId } });
}

export function webhookEventCatalog() {
  return api<string[]>("/v1/webhooks/events", { orgScoped: true });
}

export function createWebhook(url: string, events: string[]) {
  return api<Webhook>("/v1/webhooks", { method: "POST", orgScoped: true, body: { url, events } });
}

export function updateWebhook(id: string, body: Record<string, unknown>) {
  return api<Webhook>(`/v1/webhooks/${id}`, { method: "PATCH", orgScoped: true, body });
}

export function deleteWebhook(id: string) {
  return api<void>(`/v1/webhooks/${id}`, { method: "DELETE", orgScoped: true });
}

export function listWebhookDeliveries(id: string) {
  return api<WebhookDelivery[]>(`/v1/webhooks/${id}/deliveries`, { orgScoped: true });
}

export function testWebhook(id: string) {
  return api<WebhookDelivery>(`/v1/webhooks/${id}/test`, { method: "POST", orgScoped: true });
}

// ── Audit ────────────────────────────────────────────────────────────────────
export interface AuditEntry {
  id: string;
  actor_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  meta: Record<string, unknown>;
  ip: string | null;
  created_at: string;
}

export function listAudit(action?: string) {
  const q = action ? `?action=${encodeURIComponent(action)}` : "";
  return api<AuditEntry[]>(`/v1/audit${q}`, { orgScoped: true });
}
