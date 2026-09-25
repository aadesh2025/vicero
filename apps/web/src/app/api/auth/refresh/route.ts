import { NextRequest, NextResponse } from "next/server";
import { clearRefreshCookie, forward, REFRESH_COOKIE, setRefreshCookie } from "../_bff";

// POST /api/auth/refresh → reads the httpOnly refresh cookie, rotates it against the API, and
// returns a fresh access token. The client never sees or handles the refresh token.
export async function POST(request: NextRequest) {
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;
  if (!refresh) {
    return NextResponse.json({ error: { code: "auth.no_session", message: "No session" } }, { status: 401 });
  }
  let status: number;
  let data: unknown;
  try {
    ({ status, data } = await forward("/v1/auth/refresh", { refresh_token: refresh }, request));
  } catch {
    // The API is unreachable (restarting, network blip). That says nothing about whether the
    // refresh token is still good, so keep the cookie and let the client try again.
    return NextResponse.json(
      { error: { code: "auth.refresh_unavailable", message: "Auth service unavailable" } },
      { status: 503 },
    );
  }
  if (status >= 400 || !data || typeof data !== "object") {
    const rejected = status === 401 || status === 400;
    const res = NextResponse.json(
      { error: { code: "auth.refresh_failed", message: "Refresh failed" } },
      // Only a rejection of the token itself is a real logout; a 5xx is the server's problem
      // and must not cost the user their session.
      { status: rejected ? 401 : 503 },
    );
    if (rejected) clearRefreshCookie(res); // stale/rotated refresh — drop it
    return res;
  }
  const { access_token, refresh_token } = data as { access_token: string; refresh_token: string };
  const res = NextResponse.json({ access_token });
  setRefreshCookie(res, refresh_token); // rotation: store the new refresh token
  return res;
}
