import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "./route";
import { POST as logout } from "../logout/route";
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

function apiReply(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}
const setCookies = (res: Response) => res.headers.getSetCookie().join(" | ");

describe("POST /api/auth/refresh — what the API's answer does to the cookie", () => {
  it("a lost rotation race answers 409 and leaves the cookie alone (the 2026-10-07 logout)", async () => {
    vi.stubGlobal("fetch", apiReply(401, { error: { code: "auth.refresh_rotated", message: "just rotated" } }));
    const res = await POST(req("race-loser-1"));
    expect(res.status).toBe(409);
    expect((await res.json()).error.code).toBe("auth.refresh_rotated");
    // A 401 here would make the client log out, and a Set-Cookie would wipe the winner's fresh cookie.
    expect(setCookies(res)).toBe("");
  });

  it("a definitively dead token answers 401 and clears the cookie", async () => {
    vi.stubGlobal("fetch", apiReply(401, { error: { code: "auth.invalid_token", message: "dead" } }));
    const res = await POST(req("dead-token-1"));
    expect(res.status).toBe(401);
    expect(setCookies(res)).toMatch(new RegExp(`${REFRESH_COOKIE}=;`));
  });

  it("an API 5xx never costs the user their session", async () => {
    vi.stubGlobal("fetch", apiReply(500, { error: { code: "internal", message: "boom" } }));
    const res = await POST(req("outage-token-1"));
    expect(res.status).toBe(503);
    expect(setCookies(res)).toBe("");
  });

  it("an unreachable API keeps the cookie too", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await POST(req("outage-token-2"));
    expect(res.status).toBe(503);
    expect(setCookies(res)).toBe("");
  });

  it("success stores the rotated refresh cookie and returns only the access token", async () => {
    vi.stubGlobal("fetch", apiReply(200, { access_token: "acc", refresh_token: "new-refresh" }));
    const res = await POST(req("fresh-token-1"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ access_token: "acc" });
    expect(setCookies(res)).toMatch(new RegExp(`${REFRESH_COOKIE}=new-refresh`));
    expect(setCookies(res)).toMatch(/HttpOnly/i);
  });
});

describe("cookie-authenticated routes reject cross-site requests", () => {
  const withHeaders = (headers: Record<string, string>) =>
    new NextRequest(new URL("/api/auth/refresh", "http://web.example"), {
      method: "POST",
      headers: { cookie: `${REFRESH_COOKIE}=victim-token`, host: "web.example", ...headers },
    });

  it("refuses Sec-Fetch-Site: cross-site and never calls the API", async () => {
    const fetchSpy = apiReply(200, { access_token: "a", refresh_token: "b" });
    vi.stubGlobal("fetch", fetchSpy);
    const res = await POST(withHeaders({ "sec-fetch-site": "cross-site", origin: "https://evil.example" }));
    expect(res.status).toBe(403);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(setCookies(res)).toBe(""); // and does not touch the victim's cookie
  });

  it("refuses a foreign Origin even without Fetch-Metadata", async () => {
    const fetchSpy = apiReply(200, { access_token: "a", refresh_token: "b" });
    vi.stubGlobal("fetch", fetchSpy);
    expect((await POST(withHeaders({ origin: "https://evil.example" }))).status).toBe(403);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses the sandboxed-iframe 'null' Origin and a sibling site", async () => {
    vi.stubGlobal("fetch", apiReply(200, { access_token: "a", refresh_token: "b" }));
    expect((await POST(withHeaders({ origin: "null" }))).status).toBe(403);
    expect((await POST(withHeaders({ "sec-fetch-site": "same-site" }))).status).toBe(403);
  });

  it("allows the app's own page (same-origin) and non-browser callers", async () => {
    vi.stubGlobal("fetch", apiReply(200, { access_token: "a", refresh_token: "b" }));
    expect((await POST(withHeaders({ "sec-fetch-site": "same-origin", origin: "http://web.example" }))).status).toBe(200);
    expect((await POST(withHeaders({}))).status).toBe(200);
  });

  it("trusts the public host Caddy forwards, not the internal one", async () => {
    vi.stubGlobal("fetch", apiReply(200, { access_token: "a", refresh_token: "b" }));
    const ok = withHeaders({ origin: "https://app.example", "x-forwarded-host": "app.example", host: "web:3000" });
    expect((await POST(ok)).status).toBe(200);
  });

  it("logout is guarded the same way", async () => {
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    const res = await logout(
      new NextRequest(new URL("/api/auth/logout", "http://web.example"), {
        method: "POST",
        headers: { cookie: `${REFRESH_COOKIE}=victim-token`, host: "web.example", "sec-fetch-site": "cross-site" },
      }),
    );
    expect(res.status).toBe(403);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

