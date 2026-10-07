import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "./route";
import { REFRESH_COOKIE } from "../_bff";

function req(opts: { cookie?: string; auth?: string; body?: unknown } = {}): NextRequest {
  const headers: Record<string, string> = {};
  if (opts.cookie) headers.cookie = `${REFRESH_COOKIE}=${opts.cookie}`;
  if (opts.auth) headers.authorization = opts.auth;
  return new NextRequest(new URL("/api/auth/logout", "http://web"), {
    method: "POST",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("POST /api/auth/logout", () => {
  it("forwards the caller's Bearer token to the API — without it the API 401s and the revoke never happens", async () => {
    const calls: Array<{ url: string; headers: Record<string, string> }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, headers: init.headers as Record<string, string> });
        return new Response(JSON.stringify({ message: "ok" }), { status: 200 });
      }),
    );

    await POST(req({ cookie: "r1", auth: "Bearer access-token-1" }));
    expect(calls).toHaveLength(1);
    expect(calls[0].headers["Authorization"]).toBe("Bearer access-token-1");
  });

  it("logs out just this device by default: sends the cookie's refresh token", async () => {
    let sentBody: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        sentBody = JSON.parse(init.body as string);
        return new Response(JSON.stringify({ message: "ok" }), { status: 200 });
      }),
    );

    await POST(req({ cookie: "r1", auth: "Bearer t", body: undefined }));
    expect(sentBody).toEqual({ refresh_token: "r1" });
  });

  it('"log out all devices" omits refresh_token so the API revokes every session, not just this one', async () => {
    let sentBody: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        sentBody = JSON.parse(init.body as string);
        return new Response(JSON.stringify({ message: "ok" }), { status: 200 });
      }),
    );

    await POST(req({ cookie: "r1", auth: "Bearer t", body: { all: true } }));
    expect(sentBody).toEqual({});
  });

  it("always clears the local cookie, even if the API call fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network blip");
      }),
    );

    const res = await POST(req({ cookie: "r1", auth: "Bearer t" }));
    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain(`${REFRESH_COOKIE}=;`);
  });
});
