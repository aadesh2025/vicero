import { describe, expect, it } from "vitest";
import { clientHeaders } from "./_bff";

const req = (headers: Record<string, string>) => new Request("http://web/api/auth/signup", { headers });

describe("clientHeaders", () => {
  it("passes the visitor's forwarded address and user agent on to the API", () => {
    expect(clientHeaders(req({ "x-forwarded-for": "203.0.113.7", "user-agent": "Mozilla/5.0" }))).toEqual({
      "X-Forwarded-For": "203.0.113.7",
      "User-Agent": "Mozilla/5.0",
    });
  });

  it("keeps the whole chain, so the API can read it from the right", () => {
    expect(clientHeaders(req({ "x-forwarded-for": "198.51.100.1, 203.0.113.7" }))["X-Forwarded-For"]).toBe(
      "198.51.100.1, 203.0.113.7",
    );
  });

  it("sends nothing when there is no proxy in front (plain local dev)", () => {
    expect(clientHeaders(req({}))).toEqual({});
    expect(clientHeaders(undefined)).toEqual({});
  });

  it("forwards the caller's own Bearer token for routes the API authenticates", () => {
    // /v1/auth/logout requires `get_current_user` (Bearer-only, never the httpOnly cookie).
    // Without this, every BFF-routed logout 401'd at the API and the catch swallowed it —
    // the browser's cookies got cleared but the server-side session lived on.
    expect(clientHeaders(req({ authorization: "Bearer abc123" }))).toEqual({ Authorization: "Bearer abc123" });
  });
});
