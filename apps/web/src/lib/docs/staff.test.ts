/** The gate in front of the internal documentation.
 *
 * These tests exist because the failure they guard against is silent: a gate that returns
 * "ok" too readily does not crash or look wrong, it just publishes the architecture of the
 * system to whoever asked. Every case below is a way that could happen.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cookieStore = new Map<string, string>();

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = cookieStore.get(name);
      return value === undefined ? undefined : { name, value };
    },
  }),
}));
vi.mock("@/app/api/auth/_bff", () => ({ API_BASE: "http://api.test" }));

import { checkStaff } from "./staff";

function respondWith(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response);
}

const STAFF = { id: "u1", email: "staff@botforge.test", is_staff: true };
const TENANT = { id: "u2", email: "customer@acme.test", is_staff: false };

beforeEach(() => {
  cookieStore.clear();
  cookieStore.set("bf_access", "a-token");
});
afterEach(() => vi.unstubAllGlobals());

describe("checkStaff", () => {
  it("admits a staff account", async () => {
    vi.stubGlobal("fetch", respondWith(200, { user: STAFF }));
    await expect(checkStaff()).resolves.toEqual({ status: "ok", user: STAFF });
  });

  it("refuses a signed-in account that is not staff", async () => {
    vi.stubGlobal("fetch", respondWith(200, { user: TENANT }));
    await expect(checkStaff()).resolves.toEqual({ status: "forbidden" });
  });

  it("refuses when there is no access cookie, without calling the API", async () => {
    cookieStore.clear();
    const fetchMock = respondWith(200, { user: STAFF });
    vi.stubGlobal("fetch", fetchMock);
    await expect(checkStaff()).resolves.toEqual({ status: "forbidden" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports an expired token separately, so a slow reader is not signed out", async () => {
    vi.stubGlobal("fetch", respondWith(401, {}));
    await expect(checkStaff()).resolves.toEqual({ status: "expired" });
  });

  it("fails closed when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    await expect(checkStaff()).resolves.toEqual({ status: "forbidden" });
  });

  it("fails closed on a server error", async () => {
    vi.stubGlobal("fetch", respondWith(503, {}));
    await expect(checkStaff()).resolves.toEqual({ status: "forbidden" });
  });

  // The next three are the shapes a truthiness check would have waved through.
  it("refuses a response with no user", async () => {
    vi.stubGlobal("fetch", respondWith(200, {}));
    await expect(checkStaff()).resolves.toEqual({ status: "forbidden" });
  });

  it("refuses a user whose is_staff is missing", async () => {
    vi.stubGlobal("fetch", respondWith(200, { user: { id: "u3", email: "x@y.z" } }));
    await expect(checkStaff()).resolves.toEqual({ status: "forbidden" });
  });

  it("refuses a user whose is_staff is truthy but not true", async () => {
    vi.stubGlobal("fetch", respondWith(200, { user: { ...TENANT, is_staff: "yes" } }));
    await expect(checkStaff()).resolves.toEqual({ status: "forbidden" });
  });

  it("sends the token as a bearer and never caches the answer", async () => {
    const fetchMock = respondWith(200, { user: STAFF });
    vi.stubGlobal("fetch", fetchMock);
    await checkStaff();
    expect(fetchMock).toHaveBeenCalledWith("http://api.test/v1/auth/me", {
      headers: { Authorization: "Bearer a-token" },
      cache: "no-store",
    });
  });
});
