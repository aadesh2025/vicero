import { NextResponse } from "next/server";

// Server-side BFF helpers for auth token custody. The long-lived refresh token is kept in an
// httpOnly cookie the browser JS can never read (XSS can't exfiltrate it); the short-lived
// access token is returned to the client, which still needs it for cross-origin Bearer calls,
// SSE streaming, and the operator-inbox WebSocket (`?token=`). See docs/SECURITY.md §1 / ADR-019.

export const REFRESH_COOKIE = "bf_refresh";

// Route handlers run server-side, so they reach the API over its internal URL.
export const API_BASE = (
  process.env.API_INTERNAL_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://localhost:8000"
).replace(/\/$/, "");

export function setRefreshCookie(res: NextResponse, refreshToken: string) {
  res.cookies.set(REFRESH_COOKIE, refreshToken, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30, // 30 days
  });
}

export function clearRefreshCookie(res: NextResponse) {
  res.cookies.set(REFRESH_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
}

/** Headers that tell the API who the visitor really is.
 *
 * This server calls the API itself, so without help the API sees only this server's address and
 * every per-IP limit (signup, login, magic link, reset) counts all visitors as one. The reverse
 * proxy in front of the web app (Caddy in production) puts the visitor's address in
 * `X-Forwarded-For`; pass it on. The API believes it only because this server's address is in its
 * `TRUSTED_PROXIES`, and reads the chain from the right, so a client-supplied value can't win
 * (docs/SECURITY.md §11). With no proxy in front (plain local dev) there is nothing to pass on and
 * the API treats the client as unknown rather than guessing.
 */
export function clientHeaders(request?: Request): Record<string, string> {
  const out: Record<string, string> = {};
  const xff = request?.headers.get("x-forwarded-for");
  if (xff) out["X-Forwarded-For"] = xff;
  const ua = request?.headers.get("user-agent");
  if (ua) out["User-Agent"] = ua;
  return out;
}

/** Forward a JSON body to the API and return the parsed response + status. */
export async function forward(
  path: string,
  body: unknown,
  request?: Request,
): Promise<{ status: number; data: unknown }> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...clientHeaders(request) },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  return { status: res.status, data };
}

/** A BFF route that forwards to an API endpoint returning a session (`AuthResponse`), keeps the
 * refresh token in the httpOnly cookie, and hands the browser only the access token + user. */
export function sessionRoute(apiPath: string) {
  return async function POST(request: Request) {
    const body = await request.json();
    const { status, data } = await forward(apiPath, body, request);
    if (status >= 400 || !data || typeof data !== "object") {
      return NextResponse.json(data ?? { error: { code: "auth.failed", message: "Sign-in failed" } }, { status });
    }
    const { access_token, refresh_token, user } = data as {
      access_token: string;
      refresh_token: string;
      user?: unknown;
    };
    const res = NextResponse.json({ access_token, user });
    setRefreshCookie(res, refresh_token);
    return res;
  };
}
