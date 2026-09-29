import { NextResponse, type NextRequest } from "next/server";
import { REFRESH_COOKIE } from "@/app/api/auth/_bff";

// `/help/:agentKey`, `/docs` and `/vault` are deliberately absent. The first two are public by
// design. `/vault` has its own sign-in and its own session, entirely separate from BotForge
// auth: this file only checks for a BotForge cookie, which is meaningless there, and the
// vault pages enforce their own gate on the server (`lib/vault/session.ts`).
const PROTECTED = [
  "/dashboard",
  "/agents",
  "/knowledge",
  "/inbox",
  "/contacts",
  "/analytics",
  "/automations",
  "/settings",
  "/admin",
  "/billing",
  "/onboarding",
];
const AUTH_ROUTES = ["/login", "/signup"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  // `bf_access` lives ~30 minutes; the httpOnly `bf_refresh` cookie lives 30 days. Checking
  // only `bf_access` bounced returning visitors to /login every time it expired, even though
  // AuthGate (client-side) already knows how to silently mint a fresh access token from the
  // refresh cookie via `api()`'s 401 retry — this redirect ran first, at the edge, before that
  // code ever got a chance to.
  const authed = Boolean(request.cookies.get("bf_access")?.value || request.cookies.get(REFRESH_COOKIE)?.value);

  if (PROTECTED.some((p) => pathname === p || pathname.startsWith(p + "/")) && !authed) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (AUTH_ROUTES.includes(pathname) && authed) {
    const url = request.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/agents/:path*",
    "/knowledge/:path*",
    "/inbox/:path*",
    "/contacts/:path*",
    "/analytics/:path*",
    "/automations/:path*",
    "/settings/:path*",
    "/admin/:path*",
    "/billing/:path*",
    "/onboarding/:path*",
    "/login",
    "/signup",
  ],
};
