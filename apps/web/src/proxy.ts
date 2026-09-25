import { NextResponse, type NextRequest } from "next/server";

// `/help/:agentKey` and `/docs` are deliberately absent: the Help Center and the product
// documentation are public by design.
//
// `/internal-docs` is listed, but only as a courtesy — it bounces a signed-out visitor to
// the login page instead of showing them a permission error. It is NOT the access control
// for that section: this runs on cookie *presence* and cannot read a role, so any signed-in
// account passes it. The real gate is the server-side staff check in `lib/docs/staff.ts`,
// which runs before any content is read.
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
  "/internal-docs",
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
    "/internal-docs/:path*",
    "/billing/:path*",
    "/onboarding/:path*",
    "/login",
    "/signup",
  ],
};
