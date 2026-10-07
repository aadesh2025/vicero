import { NextRequest, NextResponse } from "next/server";
import { clearRefreshCookie, forward, REFRESH_COOKIE } from "../_bff";

// POST /api/auth/logout → revokes the session at the API and clears the httpOnly refresh cookie.
// Body `{ all: true }` revokes every session for the account instead of just this device's
// (the API already supports this: `LogoutRequest.refresh_token` omitted = revoke-all). Either
// way this needs the caller's own Bearer token forwarded — `/v1/auth/logout` is authenticated,
// the httpOnly cookie alone was never enough (`clientHeaders` in `_bff.ts` carries it through).
export async function POST(request: NextRequest) {
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;
  let all = false;
  try {
    const body = await request.json();
    all = Boolean(body?.all);
  } catch {
    /* no/empty body — plain single-device logout */
  }
  if (refresh || all) {
    try {
      await forward("/v1/auth/logout", all ? {} : { refresh_token: refresh }, request);
    } catch {
      /* best-effort revoke */
    }
  }
  const res = NextResponse.json({ ok: true });
  clearRefreshCookie(res);
  return res;
}
