"use client";

import { API_BASE } from "./config";
import { withRefreshLock } from "./refresh-lock";
import { clearAuth, getAccessToken, getActiveOrgId, setAccessToken } from "./tokens";

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  orgScoped?: boolean; // attach the X-Org-Id header
  signal?: AbortSignal;
}

async function toError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = res.statusText;
  let details: unknown;
  try {
    const data = await res.json();
    if (data?.error) {
      code = data.error.code ?? code;
      message = data.error.message ?? message;
      details = data.error.details;
    }
  } catch {
    /* non-JSON body */
  }
  return new ApiError(res.status, code, message, details);
}

let refreshing: Promise<boolean> | null = null;

/** How long to wait, after losing a rotation race, for the winner's new access token to show up. */
const ROTATED_RETRY_MS = 400;
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Refresh through the same-origin BFF; the httpOnly refresh cookie rides along automatically. */
async function callRefresh(): Promise<boolean | "rotated"> {
  const res = await fetch(`/api/auth/refresh`, { method: "POST" });
  if (res.ok) {
    const data = await res.json();
    setAccessToken(data.access_token);
    return true;
  }
  // 409: another request rotated this token a moment ago. That is a lost race, not a logout, and the
  // BFF deliberately left the cookie alone.
  if (res.status === 409) return "rotated";
  // Only give up the session when the refresh token is genuinely rejected. A 502 or a restarting API
  // is transient: dropping the tokens there turns a blip into a logout.
  if (res.status === 401) clearAuth();
  return false;
}

async function refreshOnce(sentToken: string | null): Promise<boolean> {
  // Tabs share the access-token cookie. If it is no longer the token that was just rejected, another
  // tab already refreshed while we waited for the lock: reuse its result instead of spending the
  // refresh token a second time.
  if (getAccessToken() !== sentToken) return true;
  const first = await callRefresh();
  if (first !== "rotated") return first;
  await sleep(ROTATED_RETRY_MS);
  if (getAccessToken() !== sentToken) return true;
  const second = await callRefresh();
  return second === true;
}

/** Refresh the session once, however many callers ask and in however many tabs: calls in this tab share
 * one promise, and tabs take turns through the lock in `refresh-lock.ts`. `sentToken` is the access
 * token the rejected request carried. */
export function tryRefresh(sentToken: string | null = getAccessToken()): Promise<boolean> {
  if (!refreshing) {
    refreshing = withRefreshLock(() => refreshOnce(sentToken)).finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

function buildHeaders(orgScoped?: boolean): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (orgScoped) {
    const orgId = getActiveOrgId();
    if (orgId) headers["X-Org-Id"] = orgId;
  }
  return headers;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, orgScoped, signal } = opts;
  const init: RequestInit = {
    method,
    headers: buildHeaders(orgScoped),
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  };

  const sentToken = getAccessToken();
  let res = await fetch(`${API_BASE}${path}`, init);
  if (res.status === 401) {
    if (await tryRefresh(sentToken)) {
      init.headers = buildHeaders(orgScoped);
      res = await fetch(`${API_BASE}${path}`, init);
    }
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Upload multipart form data (used for document file uploads). */
export async function apiForm<T>(path: string, form: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const orgId = getActiveOrgId();
  if (orgId) headers["X-Org-Id"] = orgId;
  // Note: no Content-Type — the browser sets the multipart boundary itself.

  const send = () => fetch(`${API_BASE}${path}`, { method: "POST", headers, body: form });
  const sentToken = getAccessToken();
  let res = await send();
  if (res.status === 401) {
    if (await tryRefresh(sentToken)) {
      const token2 = getAccessToken();
      if (token2) headers.Authorization = `Bearer ${token2}`;
      res = await send();
    }
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Open an SSE stream (used by the playground). Yields parsed data objects. */
export async function* apiStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: buildHeaders(true),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw await toError(res);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try {
        yield JSON.parse(payload);
      } catch {
        /* skip malformed chunk */
      }
    }
  }
}
