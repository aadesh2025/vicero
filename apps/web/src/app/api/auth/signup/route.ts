import { NextRequest, NextResponse } from "next/server";
import { forward, setRefreshCookie } from "../_bff";

// POST /api/auth/signup → same custody as login: refresh token → httpOnly cookie.
export async function POST(request: NextRequest) {
  const body = await request.json();
  const { status, data } = await forward("/v1/auth/signup", body, request);
  if (status >= 400 || !data || typeof data !== "object") {
    return NextResponse.json(data ?? { error: { code: "auth.failed", message: "Signup failed" } }, { status });
  }
  const { access_token, refresh_token, user } = data as {
    access_token: string;
    refresh_token: string;
    user?: unknown;
  };
  const res = NextResponse.json({ access_token, user });
  setRefreshCookie(res, refresh_token);
  return res;
}
