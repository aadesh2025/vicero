"use client";

import { api } from "./client";
import type { ApiChannel } from "./channels";

/** Public Meta settings, fetched at runtime so enabling one-click connect needs no web rebuild. */
export interface MetaConfig {
  /** Messenger + Instagram can be connected (app id, secret and verify token are set). */
  enabled: boolean;
  /** WhatsApp Embedded Signup is configured too. */
  whatsapp_enabled: boolean;
  app_id: string | null;
  config_id: string | null;
  graph_version: string;
  /** False until Meta approves the app: only people listed as testers can connect. */
  app_live: boolean;
}

export interface MetaPage {
  page_id: string;
  name: string;
  picture: string | null;
  instagram: { id: string; username: string | null } | null;
}

export interface MetaPagesResult {
  session_id: string;
  pages: MetaPage[];
}

export type MetaKind = "messenger" | "instagram";

export function getMetaConfig() {
  return api<MetaConfig>("/v1/channels/meta/config", { orgScoped: true });
}

export function connectWhatsApp(body: { agent_id: string; code: string; waba_id: string; phone_number_id: string }) {
  return api<ApiChannel>("/v1/channels/meta/whatsapp/connect", { method: "POST", orgScoped: true, body });
}

export function listFacebookPages(userAccessToken: string) {
  return api<MetaPagesResult>("/v1/channels/meta/facebook/pages", {
    method: "POST",
    orgScoped: true,
    body: { user_access_token: userAccessToken },
  });
}

export function connectFacebookPages(body: {
  session_id: string;
  agent_id: string;
  page_ids: string[];
  kinds: MetaKind[];
}) {
  return api<ApiChannel[]>("/v1/channels/meta/facebook/connect", { method: "POST", orgScoped: true, body });
}

export function disconnectMetaChannel(channelId: string) {
  return api<ApiChannel>(`/v1/channels/${channelId}/meta/disconnect`, { method: "POST", orgScoped: true });
}
