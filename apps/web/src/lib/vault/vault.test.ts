/** The private admin area's primitives: password hashing, the signed session, the login
 * limiter and the secrets reader.
 *
 * Nearly every case is a way for the vault to fail *open* — a wrong password accepted, a
 * forged or stale cookie honoured, a full secret leaking into something that gets rendered.
 * Those failures do not crash and do not look wrong; they just hand over the keys. So the
 * tests are written against the failure, not against the happy path.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));

import { decoyHash, hashPassword, verifyPassword } from "./password";
import { SESSION_TTL_SECONDS, createSessionToken, verifySessionToken } from "./session";
import { recordFailure, recordSuccess, resetAttempts, retryAfterSeconds } from "./attempts";
import { allowedEmails, isAllowedEmail, isConfigured } from "./config";
import { isKnownSecret, listSecrets, maskValue, revealSecret } from "./secrets";

const SECRET = "a".repeat(40);

describe("password hashing", () => {
  it("accepts the right password and refuses a wrong one", async () => {
    const hash = await hashPassword("correct horse battery staple");
    await expect(verifyPassword("correct horse battery staple", hash)).resolves.toBe(true);
    await expect(verifyPassword("correct horse battery stapl", hash)).resolves.toBe(false);
    await expect(verifyPassword("", hash)).resolves.toBe(false);
  });

  it("salts: the same password hashes differently each time", async () => {
    expect(await hashPassword("same")).not.toBe(await hashPassword("same"));
  });

  it("stores nothing that Docker Compose or dotenv would mangle", async () => {
    const hash = await hashPassword("pw");
    // `$` is interpolated in compose env files; `#` starts a comment; space ends the value.
    expect(hash).not.toMatch(/[$#\s"']/);
  });

  // The important class: a broken value in `.env` must never become "any password works".
  it.each([
    [undefined, "unset"],
    ["", "empty"],
    ["scrypt", "just the scheme"],
    ["scrypt:32768:8:1:abc:def", "truncated hash"],
    ["bcrypt:32768:8:1:c2FsdHNhbHQ:aGFzaA", "a different scheme"],
    ["scrypt:0:8:1:c2FsdHNhbHQ:" + "A".repeat(43), "zero cost"],
    ["scrypt:1073741824:8:1:c2FsdHNhbHQ:" + "A".repeat(43), "hostile cost (would allocate GBs)"],
    ["scrypt:32768:8:1::" + "A".repeat(43), "empty salt"],
    ["plaintext-password", "a plaintext password pasted by mistake"],
  ])("refuses a malformed stored value: %s (%s)", async (...args) => {
    const stored = args[0] as string | undefined;
    await expect(verifyPassword("anything", stored)).resolves.toBe(false);
    await expect(verifyPassword("", stored)).resolves.toBe(false);
  });

  it("never verifies against the decoy", async () => {
    // The decoy exists to spend the same time as a real check; no password may match it.
    await expect(verifyPassword("password", await decoyHash())).resolves.toBe(false);
  });

  it("treats visually identical unicode as the same password", async () => {
    const hash = await hashPassword("café"); // é precomposed
    await expect(verifyPassword("café", hash)).resolves.toBe(true); // e + combining accent
  });
});

describe("session token", () => {
  const now = 1_800_000_000_000;

  it("round-trips an email", () => {
    const token = createSessionToken("Admin@Example.com", SECRET, now);
    expect(verifySessionToken(token, SECRET, now + 1000)).toBe("admin@example.com");
  });

  it("expires", () => {
    const token = createSessionToken("a@b.co", SECRET, now);
    expect(verifySessionToken(token, SECRET, now + (SESSION_TTL_SECONDS - 5) * 1000)).toBe("a@b.co");
    expect(verifySessionToken(token, SECRET, now + (SESSION_TTL_SECONDS + 5) * 1000)).toBeNull();
  });

  it("is invalid under any other secret", () => {
    const token = createSessionToken("a@b.co", SECRET, now);
    expect(verifySessionToken(token, "b".repeat(40), now)).toBeNull();
  });

  it("rejects a tampered payload, even one that keeps the old signature", () => {
    const [body, mac] = createSessionToken("user@b.co", SECRET, now).split(".");
    const forged = Buffer.from(JSON.stringify({ e: "admin@b.co", exp: 9_999_999_999 })).toString("base64url");
    expect(verifySessionToken(`${forged}.${mac}`, SECRET, now)).toBeNull();
    expect(body).not.toBe(forged);
  });

  it.each([
    [undefined, "no cookie"],
    ["", "empty"],
    ["nodot", "no signature"],
    ["a.b.c", "too many parts"],
    [".sig", "no body"],
    ["body.", "no mac"],
    ["!!!.???", "not base64"],
  ])("rejects a malformed token: %s (%s)", (...args) => {
    expect(verifySessionToken(args[0] as string | undefined, SECRET, now)).toBeNull();
  });

  it("rejects a correctly signed payload of the wrong shape", async () => {
    const { createHmac } = await import("node:crypto");
    const body = Buffer.from(JSON.stringify({ e: 123, exp: "soon" })).toString("base64url");
    const mac = createHmac("sha256", SECRET).update(body).digest("base64url");
    expect(verifySessionToken(`${body}.${mac}`, SECRET, now)).toBeNull();
  });
});

describe("login limiter", () => {
  beforeEach(() => resetAttempts());

  it("allows five failures from one address, then blocks it", () => {
    for (let i = 0; i < 5; i++) {
      expect(retryAfterSeconds("1.1.1.1")).toBe(0);
      recordFailure("1.1.1.1");
    }
    expect(retryAfterSeconds("1.1.1.1")).toBeGreaterThan(0);
  });

  it("does not block a different address on the per-IP bucket", () => {
    for (let i = 0; i < 5; i++) recordFailure("1.1.1.1");
    expect(retryAfterSeconds("2.2.2.2")).toBe(0);
  });

  it("caps total guesses even when the address is rotated every time", () => {
    // The attack the global bucket exists for: a spoofed X-Forwarded-For gives a fresh
    // per-IP allowance on every guess.
    for (let i = 0; i < 30; i++) recordFailure(`10.0.0.${i}`);
    expect(retryAfterSeconds("10.9.9.9")).toBeGreaterThan(0);
  });

  it("frees up after the window", () => {
    const t0 = 1_000_000;
    for (let i = 0; i < 5; i++) recordFailure("1.1.1.1", t0);
    expect(retryAfterSeconds("1.1.1.1", t0 + 1000)).toBeGreaterThan(0);
    expect(retryAfterSeconds("1.1.1.1", t0 + 16 * 60 * 1000)).toBe(0);
  });

  it("a success clears that address's strikes", () => {
    for (let i = 0; i < 4; i++) recordFailure("1.1.1.1");
    recordSuccess("1.1.1.1");
    for (let i = 0; i < 4; i++) recordFailure("1.1.1.1");
    expect(retryAfterSeconds("1.1.1.1")).toBe(0);
  });
});

describe("configuration", () => {
  const keep = { ...process.env };
  beforeEach(() => {
    process.env = { ...keep };
    delete process.env.VAULT_ADMIN_EMAILS;
    delete process.env.VAULT_PASSWORD_HASH;
    delete process.env.VAULT_SESSION_SECRET;
  });

  it("is off until every part is present", () => {
    expect(isConfigured()).toBe(false);
    process.env.VAULT_ADMIN_EMAILS = "a@b.co";
    expect(isConfigured()).toBe(false);
    process.env.VAULT_PASSWORD_HASH = "scrypt:x";
    expect(isConfigured()).toBe(false);
    process.env.VAULT_SESSION_SECRET = "short";
    expect(isConfigured()).toBe(false); // a weak signing secret counts as not configured
    process.env.VAULT_SESSION_SECRET = SECRET;
    expect(isConfigured()).toBe(true);
  });

  it("matches the allow-list exactly, case-insensitively, and only whole addresses", () => {
    process.env.VAULT_ADMIN_EMAILS = " Admin@Example.com , other@example.com ";
    expect(allowedEmails()).toEqual(["admin@example.com", "other@example.com"]);
    expect(isAllowedEmail("ADMIN@example.com")).toBe(true);
    expect(isAllowedEmail("  other@example.com ")).toBe(true);
    // Not a suffix, prefix, domain or substring rule.
    expect(isAllowedEmail("xadmin@example.com")).toBe(false);
    expect(isAllowedEmail("admin@example.com.evil.io")).toBe(false);
    expect(isAllowedEmail("@example.com")).toBe(false);
    expect(isAllowedEmail("")).toBe(false);
  });

  it("an empty allow-list admits nobody", () => {
    process.env.VAULT_ADMIN_EMAILS = "";
    expect(isAllowedEmail("")).toBe(false);
    expect(isAllowedEmail("a@b.co")).toBe(false);
  });
});

describe("secrets reader", () => {
  const keep = { ...process.env };
  beforeEach(() => {
    process.env = { ...keep };
  });

  it("masks so a full value can never be recovered from the preview", () => {
    expect(maskValue("short")).toBe("••••••••");
    expect(maskValue("1234567890123456")).toBe("••••••••3456");
    // Under 16 characters nothing at all is shown: four characters of an eight-character
    // password are half of it.
    expect(maskValue("abcdefgh1234567")).toBe("••••••••");
  });

  it("never puts a full value into the listing the page renders", () => {
    const real = "gsk_THIS_IS_A_LONG_REAL_LOOKING_KEY_0123456789";
    process.env.GROQ_API_KEY = real;
    const listing = JSON.stringify(listSecrets());
    expect(listing).not.toContain(real);
    expect(listing).not.toContain(real.slice(0, -6)); // nor all-but-the-tail
    const entry = listSecrets().flatMap((s) => s.entries).find((e) => e.name === "GROQ_API_KEY")!;
    expect(entry.available).toBe(true);
    expect(entry.masked).toBe("••••••••" + real.slice(-4));
  });

  it("reveals a known name's real value, and reports an unset one as absent", () => {
    process.env.GROQ_API_KEY = "gsk_value";
    expect(revealSecret("GROQ_API_KEY")).toBe("gsk_value");
    delete process.env.OPENAI_API_KEY;
    expect(revealSecret("OPENAI_API_KEY")).toBeNull();
    const entry = listSecrets().flatMap((s) => s.entries).find((e) => e.name === "OPENAI_API_KEY")!;
    expect(entry.available).toBe(false);
    expect(entry.masked).toBeNull();
  });

  // Names come from the generated snapshot, so these are not filtered — they are absent.
  it.each(["PATH", "HOME", "NODE_ENV", "VAULT_SESSION_SECRET", "VAULT_PASSWORD_HASH", "VAULT_ADMIN_EMAILS", "", "__proto__", "constructor"])(
    "will not reveal %j",
    (name) => {
      process.env.VAULT_SESSION_SECRET = SECRET;
      process.env.VAULT_PASSWORD_HASH = "scrypt:secret";
      expect(isKnownSecret(name)).toBe(false);
      expect(revealSecret(name)).toBeNull();
    },
  );

  it("does not list the vault's own credentials at all", () => {
    const names = listSecrets().flatMap((s) => s.entries.map((e) => e.name));
    expect(names.some((n) => n.startsWith("VAULT_"))).toBe(false);
    expect(names).toContain("SECRET_KEY");
  });
});
