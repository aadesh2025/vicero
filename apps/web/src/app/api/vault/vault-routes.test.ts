/** The vault's HTTP surface: login, reveal, logout.
 *
 * Login is tested as an attacker would meet it — wrong email, wrong password, right
 * password on the wrong address, a forged cookie — because the properties that matter are
 * the ones a caller can probe: are the failures distinguishable, is guessing bounded, and
 * does the one route that returns a secret refuse everyone it should.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined),
  }),
}));

import { hashPassword } from "@/lib/vault/password";
import { resetAttempts } from "@/lib/vault/attempts";
import { VAULT_COOKIE, createSessionToken } from "@/lib/vault/session";
import { POST as login } from "./login/route";
import { POST as reveal } from "./reveal/route";
import { POST as logout } from "./logout/route";

const SECRET = "s".repeat(48);
const ADMIN = "admin@example.com";
const PASSWORD = "a-long-passphrase-only-the-admin-knows";
const REAL_KEY = "gsk_a_real_looking_key_value_0123456789abcdef";

const keep = { ...process.env };
let hash: string;

function post(path: string, body: unknown, headers: Record<string, string> = {}): Request {
  return new Request(`http://localhost:3001${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", host: "localhost:3001", ...headers },
    body: JSON.stringify(body),
  });
}

async function body(res: Response) {
  return (await res.json()) as { error?: { code: string; message: string }; value?: string; ok?: boolean };
}

beforeEach(async () => {
  process.env = { ...keep };
  hash ??= await hashPassword(PASSWORD);
  process.env.VAULT_ADMIN_EMAILS = ADMIN;
  process.env.VAULT_PASSWORD_HASH = hash;
  process.env.VAULT_SESSION_SECRET = SECRET;
  process.env.GROQ_API_KEY = REAL_KEY;
  jar.clear();
  resetAttempts();
});

describe("POST /api/vault/login", () => {
  it("signs in the allow-listed admin with the right password", async () => {
    const res = await login(post("/api/vault/login", { email: ADMIN, password: PASSWORD }));
    expect(res.status).toBe(200);
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`${VAULT_COOKIE}=`);
    expect(cookie.toLowerCase()).toContain("httponly");
    expect(cookie.toLowerCase()).toContain("samesite=strict");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("accepts the address in a different case", async () => {
    const res = await login(post("/api/vault/login", { email: " Admin@EXAMPLE.com ", password: PASSWORD }));
    expect(res.status).toBe(200);
  });

  it("refuses the right password on an address that is not on the list", async () => {
    const res = await login(post("/api/vault/login", { email: "someone@example.com", password: PASSWORD }));
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("refuses the right address with the wrong password", async () => {
    const res = await login(post("/api/vault/login", { email: ADMIN, password: "nope" }));
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("answers a wrong email and a wrong password identically", async () => {
    const wrongEmail = await login(post("/api/vault/login", { email: "x@example.com", password: PASSWORD }));
    resetAttempts();
    const wrongPass = await login(post("/api/vault/login", { email: ADMIN, password: "wrong" }));
    // Naming which one failed would tell an attacker which addresses are administrators.
    expect(wrongEmail.status).toBe(wrongPass.status);
    expect(await body(wrongEmail)).toEqual(await body(wrongPass));
  });

  it("refuses everyone while unconfigured, rather than accepting anything", async () => {
    delete process.env.VAULT_PASSWORD_HASH;
    const res = await login(post("/api/vault/login", { email: ADMIN, password: PASSWORD }));
    expect(res.status).toBe(503);
    expect((await body(res)).error?.code).toBe("vault.not_configured");
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("refuses when the stored hash is corrupt — never 'any password works'", async () => {
    process.env.VAULT_PASSWORD_HASH = "scrypt:garbage";
    const res = await login(post("/api/vault/login", { email: ADMIN, password: "anything" }));
    expect(res.status).toBe(401);
  });

  it("locks out after repeated failures, even for the correct password afterwards", async () => {
    for (let i = 0; i < 5; i++) {
      const res = await login(post("/api/vault/login", { email: ADMIN, password: `guess-${i}` }, { "x-forwarded-for": "9.9.9.9" }));
      expect(res.status).toBe(401);
    }
    const locked = await login(post("/api/vault/login", { email: ADMIN, password: PASSWORD }, { "x-forwarded-for": "9.9.9.9" }));
    expect(locked.status).toBe(429);
    expect(Number(locked.headers.get("retry-after"))).toBeGreaterThan(0);
    expect(locked.headers.get("set-cookie")).toBeNull();
  }, 20000);

  it.each([
    [{}, "empty body"],
    [{ email: ADMIN }, "no password"],
    [{ password: PASSWORD }, "no email"],
    [{ email: 5, password: PASSWORD }, "email of the wrong type"],
    [{ email: ADMIN, password: { $ne: "" } }, "an object where a password should be"],
    [{ email: "a".repeat(300) + "@example.com", password: PASSWORD }, "an oversized email"],
    [{ email: ADMIN, password: "p".repeat(500) }, "an oversized password"],
  ])("rejects a malformed request: %j (%s)", async (...args) => {
    const res = await login(post("/api/vault/login", args[0]));
    expect(res.status).toBe(400);
  });

  it("refuses a cross-site request", async () => {
    const res = await login(post("/api/vault/login", { email: ADMIN, password: PASSWORD }, { origin: "https://evil.example" }));
    expect(res.status).toBe(403);
  });
});

describe("POST /api/vault/reveal", () => {
  function signIn(email = ADMIN) {
    jar.set(VAULT_COOKIE, createSessionToken(email, SECRET));
  }

  it("returns the real value to a signed-in admin", async () => {
    signIn();
    const res = await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }));
    expect(res.status).toBe(200);
    expect((await body(res)).value).toBe(REAL_KEY);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("refuses with no session, and reveals nothing", async () => {
    const res = await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }));
    expect(res.status).toBe(401);
    expect(JSON.stringify(await body(res))).not.toContain(REAL_KEY);
  });

  it("refuses a Vicero access token — a different door entirely", async () => {
    jar.set("bf_access", "a-perfectly-valid-vicero-jwt");
    jar.set("bf_refresh", "and-a-refresh-token");
    const res = await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }));
    expect(res.status).toBe(401);
  });

  it("refuses a forged cookie signed with the wrong secret", async () => {
    jar.set(VAULT_COOKIE, createSessionToken(ADMIN, "x".repeat(48)));
    const res = await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }));
    expect(res.status).toBe(401);
  });

  it("refuses a valid cookie once the address is removed from the allow-list", async () => {
    signIn();
    process.env.VAULT_ADMIN_EMAILS = "someone-else@example.com";
    const res = await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }));
    expect(res.status).toBe(401);
  });

  it("refuses everything when the session secret is rotated", async () => {
    signIn();
    process.env.VAULT_SESSION_SECRET = "r".repeat(48);
    const res = await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }));
    expect(res.status).toBe(401);
  });

  // Signed in, and still not able to read anything that is not a documented setting.
  it.each(["PATH", "HOME", "VAULT_SESSION_SECRET", "VAULT_PASSWORD_HASH", "NOPE", "", "__proto__"])(
    "will not reveal %j even to a signed-in admin",
    async (name) => {
      signIn();
      const res = await reveal(post("/api/vault/reveal", { name }));
      expect(res.status).toBe(404);
      const text = JSON.stringify(await body(res));
      expect(text).not.toContain(SECRET);
      expect(text).not.toContain(hash);
    },
  );

  it("answers 404 for a known name that is simply not set", async () => {
    signIn();
    delete process.env.ANTHROPIC_API_KEY;
    const res = await reveal(post("/api/vault/reveal", { name: "ANTHROPIC_API_KEY" }));
    expect(res.status).toBe(404);
  });

  it("refuses a cross-site request even with a valid session", async () => {
    signIn();
    const res = await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }, { origin: "https://evil.example" }));
    expect(res.status).toBe(403);
  });

  it("logs who looked at what, and never the value", async () => {
    signIn();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    await reveal(post("/api/vault/reveal", { name: "GROQ_API_KEY" }));
    const logged = info.mock.calls.flat().join(" ");
    expect(logged).toContain("vault_reveal");
    expect(logged).toContain("GROQ_API_KEY");
    expect(logged).toContain(ADMIN);
    expect(logged).not.toContain(REAL_KEY);
    info.mockRestore();
  });
});

describe("POST /api/vault/logout", () => {
  it("clears the cookie", async () => {
    const res = await logout(post("/api/vault/logout", {}));
    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie") ?? "").toMatch(/bf_vault=;|max-age=0/i);
  });
});
