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

/** CSRF guard for routes that act on the httpOnly refresh cookie (`refresh`, `logout`).
 *
 * SameSite=Lax already keeps the cookie off cross-site fetch/XHR and form POSTs; this refuses the
 * request outright as a second layer, so a sibling site, a mis-set cookie attribute or an old browser
 * cannot turn "the cookie rode along" into "the action ran". A browser always sends `Sec-Fetch-Site`
 * and, on POST, `Origin`; a non-browser caller (curl, a health check) sends neither and is let through,
 * because CSRF is a browser-only attack. Returns the 403 to send, or null to carry on. */
export function rejectCrossSite(request: Request): NextResponse | null {
  const refuse = () =>
    NextResponse.json(
      { error: { code: "auth.cross_site", message: "Cross-site request refused" } },
      { status: 403 },
    );
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return refuse();
  const origin = request.headers.get("origin");
  if (origin) {
    // Behind Caddy the public host arrives as Host (and X-Forwarded-Host); compare against it.
    const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
    let originHost: string;
    try {
      originHost = new URL(origin).host; // the literal "null" Origin (sandboxed iframes) throws here
    } catch {
      return refuse();
    }
    if (!host || originHost !== host) return refuse();
  }
  return null;
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
  // Routes that call an API endpoint requiring `get_current_user` (logout, today) need the
  // browser's own Bearer token passed through — the API only ever trusts this header, never
  // the httpOnly cookie. Signup/login/magic/oauth calls have no Authorization header to begin
  // with, so this is a no-op for them.
  const auth = request?.headers.get("authorization");
  if (auth) out["Authorization"] = auth;
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
