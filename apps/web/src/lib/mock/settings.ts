import type { Provider, Role } from "./types";

export interface Member {
  id: string;
  name: string;
  email: string;
  role: Role;
  initials: string;
  status: "active" | "invited";
}

export const members: Member[] = [
  { id: "u1", name: "Aadesh Sree", email: "aadesh@aurozen.ai", role: "owner", initials: "AS", status: "active" },
  { id: "u2", name: "Priya N.", email: "priya@aurozen.ai", role: "admin", initials: "PN", status: "active" },
  { id: "u3", name: "Karthik R.", email: "karthik@aurozen.ai", role: "editor", initials: "KR", status: "active" },
  { id: "u4", name: "Support Desk", email: "desk@aurozen.ai", role: "operator", initials: "SD", status: "active" },
  { id: "u5", name: "—", email: "newhire@aurozen.ai", role: "viewer", initials: "?", status: "invited" },
];

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  lastUsed: string | null;
  createdAt: string;
}

export const apiKeys: ApiKey[] = [
  { id: "k1", name: "Production widget", prefix: "bf_live_9x2", scopes: ["chat:write", "agents:read"], lastUsed: "2026-07-17T06:35:00Z", createdAt: "2026-06-01T00:00:00Z" },
  { id: "k2", name: "n8n integration", prefix: "bf_live_4kd", scopes: ["chat:write", "webhooks:read"], lastUsed: "2026-07-17T05:02:00Z", createdAt: "2026-06-10T00:00:00Z" },
  { id: "k3", name: "Analytics export", prefix: "bf_live_7mz", scopes: ["analytics:read"], lastUsed: null, createdAt: "2026-07-14T00:00:00Z" },
];

export interface Credential {
  id: string;
  provider: Provider;
  label: string;
  masked: string;
  status: "valid" | "untested" | "invalid";
}

export const credentials: Credential[] = [
  { id: "c1", provider: "groq", label: "Groq — org default", masked: "gsk_••••••••7ap2", status: "valid" },
  { id: "c2", provider: "openai", label: "OpenAI — billing", masked: "sk-••••••••Xq9f", status: "valid" },
  { id: "c3", provider: "gemini", label: "Gemini free", masked: "AIza••••••••_kd", status: "untested" },
];

export interface WebhookEndpoint {
  id: string;
  url: string;
  events: string[];
  status: "active" | "failing" | "disabled";
  lastDelivery: string | null;
}

export const webhooks: WebhookEndpoint[] = [
  { id: "w1", url: "https://n8n.local/webhook/vicero", events: ["message.created", "handoff.requested"], status: "active", lastDelivery: "2026-07-17T06:38:00Z" },
  { id: "w2", url: "https://hooks.aurozen.ai/analytics", events: ["conversation.closed"], status: "failing", lastDelivery: "2026-07-17T03:10:00Z" },
];

export const roleMeta: Record<Role, { label: string; variant: "accent" | "info" | "default" }> = {
  owner: { label: "Owner", variant: "accent" },
  admin: { label: "Admin", variant: "info" },
  editor: { label: "Editor", variant: "default" },
  viewer: { label: "Viewer", variant: "default" },
  operator: { label: "Operator", variant: "default" },
};
