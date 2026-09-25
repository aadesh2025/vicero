import { NextResponse, type NextRequest } from "next/server";

// `/help/:agentKey` is deliberately absent: the Help Center is public by design.
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
  const authed = Boolean(request.cookies.get("bf_access")?.value);

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
