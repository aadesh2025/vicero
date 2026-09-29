/** The vault's own session — a signed, httpOnly cookie, separate from Vicero auth.
 *
 * Stateless: the cookie carries `{email, exp}` and an HMAC over it. There is no server-side
 * session table, which keeps this free of the API and the database (the vault must keep
 * working when they are down — that is when it is needed), at the price that a cookie
 * cannot be revoked before it expires. Two things make that acceptable:
 *
 *   - the lifetime is short (8 hours), and
 *   - every check re-reads the allow-list, so removing an address from
 *     `VAULT_ADMIN_EMAILS` ends its sessions on the next request, and rotating
 *     `VAULT_SESSION_SECRET` ends everyone's.
 */

import "server-only";

import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";
import { isAllowedEmail, sessionSecret } from "./config";

export const VAULT_COOKIE = "bf_vault";
export const SESSION_TTL_SECONDS = 60 * 60 * 8;

interface Payload {
  e: string; // email
  exp: number; // unix seconds
}

function sign(body: string, secret: string): string {
  return createHmac("sha256", secret).update(body).digest("base64url");
}

export function createSessionToken(email: string, secret: string, now = Date.now()): string {
  const payload: Payload = { e: email.trim().toLowerCase(), exp: Math.floor(now / 1000) + SESSION_TTL_SECONDS };
  const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `${body}.${sign(body, secret)}`;
}

/** The email a token vouches for, or `null`. Pure so it can be tested without cookies. */
export function verifySessionToken(token: string | undefined, secret: string, now = Date.now()): string | null {
  if (!token) return null;
  const [body, mac, extra] = token.split(".");
  if (!body || !mac || extra !== undefined) return null;

  const expected = Buffer.from(sign(body, secret));
  const given = Buffer.from(mac);
  // Length first: timingSafeEqual throws on a mismatch instead of returning false.
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return null;

  try {
    const payload = JSON.parse(Buffer.from(body, "base64url").toString("utf8")) as Partial<Payload>;
    if (typeof payload.e !== "string" || typeof payload.exp !== "number") return null;
    if (payload.exp <= Math.floor(now / 1000)) return null;
    return payload.e;
  } catch {
    return null;
  }
}

/** The signed-in admin's email, or `null`. Every gated page and route calls this. */
export async function getVaultSession(): Promise<string | null> {
  const secret = sessionSecret();
  if (!secret) return null; // unconfigured => nobody is signed in, whatever a cookie says
  const token = (await cookies()).get(VAULT_COOKIE)?.value;
  const email = verifySessionToken(token, secret);
  // Re-check the allow-list on every request, not only at login.
  if (!email || !isAllowedEmail(email)) return null;
  return email;
}
