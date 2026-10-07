import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "./route";
import { REFRESH_COOKIE } from "../_bff";

function req(cookieValue: string): NextRequest {
  return new NextRequest(new URL("/api/auth/refresh", "http://web"), {
    method: "POST",
    headers: { cookie: `${REFRESH_COOKIE}=${cookieValue}` },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("POST /api/auth/refresh — single-flight rotation", () => {
  it("coalesces two concurrent callers on the same cookie value into one API call", async () => {
    // A real reproduction of the 2026-10-07 incident: two requests racing on the same
    // not-yet-rotated refresh token must not both reach the API — only one rotation may
    // happen, and both callers get its result.
    let apiCalls = 0;
    let resolveApi: (v: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      resolveApi = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        apiCalls += 1;
        return pending;
      }),
    );

    const token = "race-token-1";
    const call1 = POST(req(token));
    const call2 = POST(req(token));

    // Both requests are in flight before the (single) API call resolves.
    await new Promise((r) => setTimeout(r, 0));
    resolveApi!(
      new Response(JSON.stringify({ access_token: "new-access", refresh_token: "new-refresh" }), {
        status: 200,
      }),
    );

    const [res1, res2] = await Promise.all([call1, call2]);
    expect(apiCalls).toBe(1);
    expect(res1.status).toBe(200);
    expect(res2.status).toBe(200);
    expect((await res1.json()).access_token).toBe("new-access");
    expect((await res2.json()).access_token).toBe("new-access");
  });

  it("issues a fresh API call for a different cookie value (no cross-request leakage)", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        calls.push(JSON.parse(init.body as string).refresh_token);
        return new Response(JSON.stringify({ access_token: "a", refresh_token: "b" }), { status: 200 });
      }),
    );

    await POST(req("race-token-2"));
    await POST(req("race-token-3"));
    expect(calls).toEqual(["race-token-2", "race-token-3"]);
  });

  it("rejects a GET — the cookie-authenticated action is reachable only via same-origin POST", async () => {
    // SameSite=Lax cookies are never attached to a cross-site fetch/XHR, and a cross-site
    // top-level navigation here would be a GET — so a route that only exports POST can't be
    // triggered by an <img>/<iframe>/link-click CSRF attempt even under Lax's GET exception.
    const routeModule = await import("./route");
    expect((routeModule as Record<string, unknown>).GET).toBeUndefined();
  });
});
