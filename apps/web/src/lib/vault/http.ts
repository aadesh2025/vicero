/** Small request helpers shared by the vault's route handlers. */

import "server-only";

import { NextResponse } from "next/server";

/** Every vault response is uncacheable: anything here may carry a secret, and a shared cache
 *  or the browser's back/forward cache must never be able to replay it. */
export function json(body: unknown, init: { status?: number; headers?: Record<string, string> } = {}) {
  return NextResponse.json(body, {
    status: init.status ?? 200,
    headers: { "Cache-Control": "no-store", ...init.headers },
  });
}

export function apiError(status: number, code: string, message: string, headers?: Record<string, string>) {
  return json({ error: { code, message } }, { status, headers });
}

/** The address to rate-limit on. The **last** `X-Forwarded-For` entry, because that is the
 *  one the nearest proxy appended; earlier ones are whatever the caller chose to send.
 *  Without a proxy there is no trustworthy value, and the global limiter in `attempts.ts`
 *  is what actually bounds guessing. */
export function clientIp(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (!forwarded) return "unknown";
  const last = forwarded.split(",").pop()?.trim();
  return last || "unknown";
}

/** Cross-site request check for the state-changing routes.
 *
 * The session cookie is `SameSite=Strict`, which already stops the browser attaching it to a
 * cross-site request; this is the second layer, for the case where that is ever loosened.
 * A request with no `Origin` header (curl, server-to-server) is allowed through — it cannot
 * be a cross-site browser request — and one whose origin names a different host is refused.
 */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
