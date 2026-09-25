/** Password hashing for the private admin area.
 *
 * scrypt from `node:crypto` — no dependency, memory-hard, and the parameters travel inside
 * the stored string so they can be raised later without invalidating an existing hash.
 *
 * Stored form: `scrypt:<N>:<r>:<p>:<salt>:<hash>` with the last two base64url. The
 * separators are `:` and the alphabet has no `$`, `#` or spaces on purpose: this string
 * lives in `.env`, and Docker Compose interpolates `$` in env files while a `#` starts a
 * comment — either would silently corrupt a hash and lock the admin out with no error.
 *
 * `scripts/vault-hash-password.mjs` produces this format; a test round-trips the two so the
 * copies cannot drift.
 */

import { randomBytes, scrypt as scryptCallback, timingSafeEqual } from "node:crypto";

const N = 32768; // 2^15 — ~32 MB, ~100 ms. Raise it; never lower it.
const R = 8;
const P = 1;
const KEY_LENGTH = 32;
// scrypt needs 128 * N * r bytes and Node's default cap sits exactly at N=2^14, so `derive`
// lifts `maxmem` explicitly (to twice the requirement).

function derive(password: string, salt: Buffer, n: number, r: number, p: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    scryptCallback(
      password.normalize("NFKC"),
      salt,
      KEY_LENGTH,
      { N: n, r, p, maxmem: 128 * n * r * 2 },
      (err, key) => (err ? reject(err) : resolve(key)),
    );
  });
}

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const key = await derive(password, salt, N, R, P);
  return ["scrypt", N, R, P, salt.toString("base64url"), key.toString("base64url")].join(":");
}

/** A hash to compare against when there is nothing real to compare, so "unknown email" and
 *  "wrong password" take the same time. Computed once, lazily. */
let decoy: Promise<string> | null = null;
export function decoyHash(): Promise<string> {
  decoy ??= hashPassword(randomBytes(12).toString("hex"));
  return decoy;
}

/**
 * True only for a correct password against a well-formed stored hash.
 *
 * Never throws: a malformed, truncated or foreign-format value is simply `false`. The one
 * thing that must not happen is a bad `.env` value turning into "any password works".
 */
export async function verifyPassword(password: string, stored: string | undefined): Promise<boolean> {
  if (!stored) return false;
  const parts = stored.split(":");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;

  const n = Number(parts[1]);
  const r = Number(parts[2]);
  const p = Number(parts[3]);
  // Bound the cost the stored value can request, so a corrupted or hostile value cannot be
  // used to make this process allocate gigabytes.
  if (![n, r, p].every(Number.isInteger) || n < 2 ** 14 || n > 2 ** 20 || r < 1 || r > 32 || p < 1 || p > 16) {
    return false;
  }

  let salt: Buffer;
  let expected: Buffer;
  try {
    salt = Buffer.from(parts[4], "base64url");
    expected = Buffer.from(parts[5], "base64url");
  } catch {
    return false;
  }
  if (salt.length < 8 || expected.length !== KEY_LENGTH) return false;

  try {
    const actual = await derive(password, salt, n, r, p);
    return timingSafeEqual(actual, expected);
  } catch {
    return false;
  }
}
