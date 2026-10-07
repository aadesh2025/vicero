import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COOKIE } from "./config";

// Two browser tabs are two copies of the client module (each with its own in-memory state) that share
// the cookie jar, localStorage and the lock manager. `vi.resetModules()` + a fresh import models a tab.

type ClientModule = typeof import("./client");

// Each "tab" re-imports the client module, which is slow on a loaded CI box; the logic under test is not.
vi.setConfig({ testTimeout: 20_000 });

async function openTab(): Promise<ClientModule> {
  vi.resetModules();
  return import("./client");
}

/** A FIFO lock manager standing in for navigator.locks, shared by every "tab". */
function installFakeLocks() {
  const queues = new Map<string, Promise<unknown>>();
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: (name: string, cb: () => Promise<unknown>) => {
        const prev = queues.get(name) ?? Promise.resolve();
        const run = prev.then(() => cb());
        queues.set(name, run.catch(() => undefined));
        return run;
      },
    },
  });
}

function removeLocks() {
  Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
}

const accessToken = () => document.cookie.match(new RegExp(`${COOKIE.access}=([^;]*)`))?.[1] ?? null;
function setCookieToken(value: string) {
  document.cookie = `${COOKIE.access}=${value}; path=/`;
}

/** The BFF refresh endpoint: counts calls, hands out "fresh" after a short delay. */
function mockNetwork(opts: { refresh?: () => Response | Promise<Response> } = {}) {
  const calls = { refresh: 0, api: 0 };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/api/auth/refresh")) {
      calls.refresh += 1;
      if (opts.refresh) return opts.refresh();
      await new Promise((r) => setTimeout(r, 30));
      return new Response(JSON.stringify({ access_token: "fresh" }), { status: 200 });
    }
    calls.api += 1;
    const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
    return auth === "Bearer fresh"
      ? new Response(JSON.stringify({ ok: true }), { status: 200 })
      : new Response(JSON.stringify({ error: { code: "auth.invalid_token", message: "expired" } }), { status: 401 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

beforeEach(() => {
  document.cookie = `${COOKIE.access}=; path=/; max-age=0`;
  localStorage.clear();
  setCookieToken("stale");
});

afterEach(() => {
  vi.unstubAllGlobals();
  removeLocks();
});

describe("single-flight refresh across tabs (Web Locks)", () => {
  it("two tabs holding the same expired token refresh exactly once and both recover", async () => {
    installFakeLocks();
    const calls = mockNetwork();
    const tabA = await openTab();
    const tabB = await openTab();

    const [a, b] = await Promise.all([tabA.api<{ ok: boolean }>("/v1/ping"), tabB.api<{ ok: boolean }>("/v1/ping")]);

    expect(a.ok).toBe(true);
    expect(b.ok).toBe(true);
    expect(calls.refresh).toBe(1); // the second tab waited, saw the new cookie, and reused it
    expect(accessToken()).toBe("fresh");
  });

  it("many calls inside one tab also share one refresh", async () => {
    installFakeLocks();
    const calls = mockNetwork();
    const tab = await openTab();
    await Promise.all([1, 2, 3, 4].map(() => tab.api("/v1/ping")));
    expect(calls.refresh).toBe(1);
  });
});

describe("single-flight refresh without Web Locks (localStorage lease + BroadcastChannel)", () => {
  it("two tabs still refresh exactly once", async () => {
    removeLocks();
    const calls = mockNetwork();
    const tabA = await openTab();
    const tabB = await openTab();

    const [a, b] = await Promise.all([tabA.api<{ ok: boolean }>("/v1/ping"), tabB.api<{ ok: boolean }>("/v1/ping")]);

    expect(a.ok && b.ok).toBe(true);
    expect(calls.refresh).toBe(1);
    expect(localStorage.getItem("vicero:refresh-lease")).toBeNull(); // lease released
  });

  it("a dead tab's stale lease cannot wedge sign-in", async () => {
    removeLocks();
    localStorage.setItem("vicero:refresh-lease", JSON.stringify({ id: "dead-tab", exp: Date.now() - 1000 }));
    const calls = mockNetwork();
    const tab = await openTab();
    expect((await tab.api<{ ok: boolean }>("/v1/ping")).ok).toBe(true);
    expect(calls.refresh).toBe(1);
  });
});

describe("what the refresh answer does to the session", () => {
  it("409 (lost a rotation race) does NOT log out: reuses the winner's token if it appears", async () => {
    installFakeLocks();
    let n = 0;
    const calls = mockNetwork({
      refresh: () => {
        n += 1;
        setTimeout(() => setCookieToken("fresh"), 100); // the winning request lands its cookie shortly after
        return new Response(JSON.stringify({ error: { code: "auth.refresh_rotated" } }), { status: 409 });
      },
    });
    const tab = await openTab();
    expect((await tab.api<{ ok: boolean }>("/v1/ping")).ok).toBe(true);
    expect(n).toBe(1);
    expect(calls.refresh).toBe(1); // no second spend of the refresh token
  });

  it("409 twice in a row fails the request but keeps the session cookies", async () => {
    installFakeLocks();
    mockNetwork({ refresh: () => new Response("{}", { status: 409 }) });
    const tab = await openTab();
    await expect(tab.api("/v1/ping")).rejects.toMatchObject({ status: 401 });
    expect(accessToken()).toBe("stale"); // not wiped: the next attempt can still recover
  });

  it("401 (token truly dead) clears the session", async () => {
    installFakeLocks();
    mockNetwork({ refresh: () => new Response("{}", { status: 401 }) });
    const tab = await openTab();
    await expect(tab.api("/v1/ping")).rejects.toMatchObject({ status: 401 });
    expect(accessToken()).toBeNull();
  });

  it("503 (API restarting) keeps the session", async () => {
    installFakeLocks();
    mockNetwork({ refresh: () => new Response("{}", { status: 503 }) });
    const tab = await openTab();
    await expect(tab.api("/v1/ping")).rejects.toMatchObject({ status: 401 });
    expect(accessToken()).toBe("stale");
  });
});
