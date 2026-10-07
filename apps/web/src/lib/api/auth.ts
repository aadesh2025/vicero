"use client";

import { api, ApiError } from "./client";
import { clearAuth, setAccessToken } from "./tokens";
import type { ApiSession, AuthResponse, MeResponse } from "./types";

// Auth goes through the same-origin Next BFF (`/api/auth/*`) so the refresh token is stored in
// an httpOnly cookie the browser JS can't read. Only the access token comes back to the client.

async function bff<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } })?.error;
    throw new ApiError(res.status, err?.code ?? "auth.error", err?.message ?? "Authentication failed");
  }
  return data as T;
}

export async function signup(email: string, password: string, fullName?: string) {
  const res = await bff<AuthResponse>("/api/auth/signup", { email, password, full_name: fullName });
  setAccessToken(res.access_token);
  return res;
}

export async function login(email: string, password: string) {
  const res = await bff<AuthResponse>("/api/auth/login", { email, password });
  setAccessToken(res.access_token);
  return res;
}

export async function me() {
  return api<MeResponse>("/v1/auth/me");
}

/** Devices with a live refresh token. Not org-scoped — sessions belong to the user. */
export async function listSessions() {
  return api<ApiSession[]>("/v1/auth/sessions");
}

export async function revokeSession(id: string) {
  return api<void>(`/v1/auth/sessions/${id}`, { method: "DELETE" });
}

export async function logout(opts?: { all?: boolean }) {
  try {
    await bff("/api/auth/logout", opts?.all ? { all: true } : undefined);
  } finally {
    clearAuth();
  }
}

// ── Self-serve (docs/18) ─────────────────────────────────────────────────────
export type OAuthProvider = "google" | "facebook";

/** Passwordless: emails a single-use sign-in link. The answer is identical for every address. */
export function requestMagicLink(email: string) {
  return api<{ message: string }>("/v1/auth/magic-link", { method: "POST", body: { email } });
}

/** Redeem a magic link. Through the BFF so the refresh token lands in the httpOnly cookie. */
export async function verifyMagicLink(token: string) {
  const res = await bff<AuthResponse>("/api/auth/magic", { token });
  setAccessToken(res.access_token);
  return res;
}

export function verifyEmail(token: string) {
  return api<{ message: string }>("/v1/auth/verify-email", { method: "POST", body: { token } });
}

export function resendVerification(email: string) {
  return api<{ message: string }>("/v1/auth/verify-email/resend", { method: "POST", body: { email } });
}

export function forgotPassword(email: string) {
  return api<{ message: string }>("/v1/auth/password/forgot", { method: "POST", body: { email } });
}

export function resetPassword(token: string, password: string) {
  return api<{ message: string }>("/v1/auth/password/reset", { method: "POST", body: { token, password } });
}

/** URL to send the browser to for a provider sign-in. `redirect=web` makes the API's callback
 * bounce back to `/oauth/callback` instead of answering with JSON. */
export async function oauthAuthorizeUrl(provider: OAuthProvider) {
  const res = await api<{ authorize_url: string }>(`/v1/auth/oauth/${provider}/authorize?redirect=web`);
  return res.authorize_url;
}

/** Trade the one-time code from the provider redirect for a session (via the BFF). */
export async function exchangeOAuthCode(code: string) {
  const res = await bff<AuthResponse>("/api/auth/oauth-exchange", { code });
  setAccessToken(res.access_token);
  return res;
}

/** Provider gave no verified email: ask us to mail a link to the one the user typed. */
export function requestOAuthEmail(pendingToken: string, email: string) {
  return api<{ message: string }>("/v1/auth/oauth/email", {
    method: "POST",
    body: { pending_token: pendingToken, email },
  });
}

export async function verifyOAuthEmail(token: string) {
  const res = await bff<AuthResponse>("/api/auth/oauth-verify", { token });
  setAccessToken(res.access_token);
  return res;
}
