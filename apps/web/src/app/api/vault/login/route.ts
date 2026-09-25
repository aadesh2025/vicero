import { NextResponse } from "next/server";
import { isAllowedEmail, isConfigured, passwordHash, sessionSecret } from "@/lib/vault/config";
import { decoyHash, verifyPassword } from "@/lib/vault/password";
import { recordFailure, recordSuccess, retryAfterSeconds } from "@/lib/vault/attempts";
import { SESSION_TTL_SECONDS, VAULT_COOKIE, createSessionToken } from "@/lib/vault/session";
import { apiError, clientIp, sameOrigin } from "@/lib/vault/http";

// POST /api/vault/login — the private admin area's own sign-in.
//
// Not a BotForge login: no user row, no JWT, no call to the API. Fails closed — with the
// vault unconfigured it refuses everyone rather than accepting anything.

const MAX_EMAIL = 254;
const MAX_PASSWORD = 256; // scrypt cost is fixed, but there is no reason to hash a megabyte

/** A wrong guess costs the caller a beat. Cheap for a human who mistyped, and it multiplies
 *  the time an automated guesser needs on top of the rate limit. */
const FAILURE_DELAY_MS = 350;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function POST(request: Request) {
  if (!sameOrigin(request)) return apiError(403, "vault.bad_origin", "Cross-site request refused.");

  if (!isConfigured()) {
    // Say so plainly to the operator; there is nothing here worth hiding, and a silent
    // failure would look like a wrong password forever.
    return apiError(503, "vault.not_configured", "The private area is not set up on this server.");
  }

  const ip = clientIp(request);
  const wait = retryAfterSeconds(ip);
  if (wait > 0) {
    return apiError(429, "vault.rate_limited", "Too many attempts. Try again later.", {
      "Retry-After": String(wait),
    });
  }

  let email = "";
  let password = "";
  try {
    const body = (await request.json()) as { email?: unknown; password?: unknown };
    if (typeof body.email === "string") email = body.email;
    if (typeof body.password === "string") password = body.password;
  } catch {
    return apiError(400, "vault.bad_request", "Invalid request.");
  }
  if (!email || !password || email.length > MAX_EMAIL || password.length > MAX_PASSWORD) {
    return apiError(400, "vault.bad_request", "Invalid request.");
  }

  // Always run a full scrypt, even for an address that is not on the list, so "not an admin"
  // and "wrong password" cost the same and cannot be told apart by timing.
  const allowed = isAllowedEmail(email);
  const hash = allowed ? passwordHash() : await decoyHash();
  const passwordOk = await verifyPassword(password, hash);

  if (!allowed || !passwordOk) {
    recordFailure(ip);
    await sleep(FAILURE_DELAY_MS);
    // One message for both causes. Naming which was wrong tells an attacker which
    // addresses are administrators.
    return apiError(401, "vault.invalid_credentials", "Invalid email or password.");
  }

  recordSuccess(ip);
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(VAULT_COOKIE, createSessionToken(email, sessionSecret()!), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    // Strict, not Lax: nothing legitimate navigates *into* the vault from another site
    // carrying this cookie, and Strict is what keeps a forged cross-site POST from riding it.
    sameSite: "strict",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  });
  return response;
}
