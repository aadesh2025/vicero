"use client";

import { api } from "./client";

// Saved external-API connections (docs/28 T1). Credentials go in as `auth` and come back masked.

export type AuthType =
  | "none"
  | "api_key"
  | "bearer"
  | "basic"
  | "custom_headers"
  | "oauth2_client_credentials";

export interface ApiConnection {
  id: string;
  name: string;
  base_url: string;
  auth_type: AuthType;
  /** Non-secret fields as saved; secrets as `••••last4`. Never the real value. */
  auth: Record<string, unknown>;
  default_headers: Record<string, string>;
  allowed_hosts: string[];
  timeout_s: number;
  health_path: string | null;
  enabled: boolean;
  tools_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConnectionBody {
  name: string;
  base_url: string;
  auth_type: AuthType;
  auth: Record<string, unknown>;
  default_headers?: Record<string, string>;
  allowed_hosts?: string[] | null;
  timeout_s?: number;
  health_path?: string | null;
  enabled?: boolean;
}

export interface ConnectionTestResult {
  ok: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
}

export const listConnections = () =>
  api<ApiConnection[]>("/v1/connections", { orgScoped: true });

export const createConnection = (body: ConnectionBody) =>
  api<ApiConnection>("/v1/connections", {
    method: "POST",
    orgScoped: true,
    body,
  });

export const updateConnection = (id: string, body: Partial<ConnectionBody>) =>
  api<ApiConnection>(`/v1/connections/${id}`, {
    method: "PATCH",
    orgScoped: true,
    body,
  });

export const deleteConnection = (id: string) =>
  api<void>(`/v1/connections/${id}`, { method: "DELETE", orgScoped: true });

export const testConnection = (id: string) =>
  api<ConnectionTestResult>(`/v1/connections/${id}/test`, {
    method: "POST",
    orgScoped: true,
  });

/** Which fields each auth type takes, and which of them are secrets (mirrors the server's AUTH_FIELDS). */
export const AUTH_FIELDS: Record<
  AuthType,
  {
    label: string;
    fields: {
      key: string;
      label: string;
      secret: boolean;
      placeholder?: string;
      choices?: string[];
    }[];
  }
> = {
  none: { label: "No authentication", fields: [] },
  api_key: {
    label: "API key",
    fields: [
      {
        key: "name",
        label: "Header or parameter name",
        secret: false,
        placeholder: "X-API-Key",
      },
      {
        key: "in",
        label: "Send it in",
        secret: false,
        choices: ["header", "query"],
      },
      { key: "value", label: "API key", secret: true },
    ],
  },
  bearer: {
    label: "Bearer token",
    fields: [{ key: "token", label: "Token", secret: true }],
  },
  basic: {
    label: "Basic (username and password)",
    fields: [
      { key: "username", label: "Username", secret: false },
      { key: "password", label: "Password", secret: true },
    ],
  },
  custom_headers: { label: "Custom headers", fields: [] },
  oauth2_client_credentials: {
    label: "OAuth 2.0 client credentials",
    fields: [
      {
        key: "token_url",
        label: "Token URL",
        secret: false,
        placeholder: "https://auth.example.com/oauth/token",
      },
      { key: "client_id", label: "Client ID", secret: false },
      { key: "client_secret", label: "Client secret", secret: true },
      { key: "scope", label: "Scope (optional)", secret: false },
    ],
  },
};
