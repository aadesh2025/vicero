import { NextRequest, NextResponse } from "next/server";
import { clearRefreshCookie, forward, REFRESH_COOKIE } from "../_bff";

// POST /api/auth/logout → revokes the session at the API and clears the httpOnly refresh cookie.
export async function POST(request: NextRequest) {
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;
  if (refresh) {
    try {
      await forward("/v1/auth/logout", { refresh_token: refresh }, request);
    } catch {
      /* best-effort revoke */
    }
  }
  const res = NextResponse.json({ ok: true });
  clearRefreshCookie(res);
  return res;
}
