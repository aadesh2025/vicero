"use client";

import { api } from "./client";

export interface ApiGoogleCalendarStatus {
  /** The server has GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET. */
  configured: boolean;
  connected: boolean;
  /** Google revoked or expired the grant: bookings keep working, without calendar events, until reconnected. */
  needs_reconnect: boolean;
  email: string | null;
  /** The exact URI to register in Google Cloud (Authorized redirect URIs). */
  redirect_uri: string;
}

export function getGoogleCalendarStatus() {
  return api<ApiGoogleCalendarStatus>("/v1/integrations/google-calendar", { orgScoped: true });
}

/** The Google consent URL; the caller sends the browser there. `agentId` is where Google's redirect returns to. */
export function getGoogleCalendarConnectUrl(agentId?: string | null) {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return api<{ url: string }>(`/v1/integrations/google-calendar/connect${q}`, { orgScoped: true });
}

export function disconnectGoogleCalendar() {
  return api<void>("/v1/integrations/google-calendar", { method: "DELETE", orgScoped: true });
}
