import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";

function req(path: string, cookie = ""): NextRequest {
  return new NextRequest(new URL(path, "http://web"), { headers: cookie ? { cookie } : {} });
}

function redirectPath(res: ReturnType<typeof proxy>): string | null {
  const location = res.headers.get("location");
  return location ? new URL(location).pathname : null;
}

describe("proxy auth redirects", () => {
  it("bounces a protected route to /login with no cookies at all", () => {
    expect(redirectPath(proxy(req("/dashboard")))).toBe("/login");
  });

  it("lets a protected route through on the short-lived access-token cookie alone", () => {
    expect(redirectPath(proxy(req("/dashboard", "bf_access=abc")))).toBeNull();
  });

  it("lets a protected route through on the httpOnly refresh cookie alone", () => {
    // The bug this pins: bf_access lives ~30 minutes, bf_refresh lives 30 days. Checking only
    // bf_access bounced a returning visitor to /login every time it expired, even though the
    // client-side AuthGate already knows how to silently mint a fresh access token from the
    // refresh cookie — this redirect ran first, at the edge, before that code got a chance to.
    expect(redirectPath(proxy(req("/dashboard", "bf_refresh=xyz")))).toBeNull();
  });

  it("still bounces a protected route to /login once both cookies are gone", () => {
    expect(redirectPath(proxy(req("/settings/profile")))).toBe("/login");
  });

  it("sends a signed-in visitor away from /login to /dashboard", () => {
    expect(redirectPath(proxy(req("/login", "bf_refresh=xyz")))).toBe("/dashboard");
  });

  it("leaves /login alone for a genuinely signed-out visitor", () => {
    expect(redirectPath(proxy(req("/login")))).toBeNull();
  });

  it("never touches /help, /docs or /vault — those gate themselves", () => {
    expect(redirectPath(proxy(req("/help/some-agent")))).toBeNull();
    expect(redirectPath(proxy(req("/docs")))).toBeNull();
    expect(redirectPath(proxy(req("/vault/login")))).toBeNull();
  });
});
