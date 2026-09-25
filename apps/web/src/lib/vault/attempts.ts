/** Brute-force limiting for the vault login.
 *
 * In-memory and per process, which is the honest scope: the web app is one Node process
 * here, and a limiter that needed Redis would mean the vault login depends on the very
 * infrastructure it exists to help you repair. If the web tier is ever scaled to several
 * replicas each keeps its own counters, so the effective limit becomes N times looser —
 * revisit this then.
 *
 * Two independent buckets, because the per-IP one alone is not enough. The client address
 * comes from `X-Forwarded-For`, which a caller can vary freely when the app is not behind a
 * proxy that overwrites it; an attacker rotating that header would get a fresh per-IP
 * allowance every guess. The **global** bucket ignores the address entirely, so however the
 * header is spoofed the total number of guesses is capped. Its cost is that a determined
 * attacker can lock the real admin out for a window — a deliberate trade: locked out for
 * fifteen minutes is recoverable, a guessed password is not.
 */

const WINDOW_MS = 15 * 60 * 1000;
const PER_IP_LIMIT = 5;
const GLOBAL_LIMIT = 30;

const perIp = new Map<string, number[]>();
let global: number[] = [];

function recent(stamps: number[], now: number): number[] {
  return stamps.filter((t) => now - t < WINDOW_MS);
}

/** Seconds to wait, or 0 if an attempt is allowed. */
export function retryAfterSeconds(ip: string, now = Date.now()): number {
  global = recent(global, now);
  const mine = recent(perIp.get(ip) ?? [], now);
  perIp.set(ip, mine);

  const blocked = [
    mine.length >= PER_IP_LIMIT ? mine[0] : null,
    global.length >= GLOBAL_LIMIT ? global[0] : null,
  ].filter((t): t is number => t !== null);
  if (blocked.length === 0) return 0;
  // Wait until the *later* of the two windows frees a slot.
  return Math.max(...blocked.map((oldest) => Math.ceil((oldest + WINDOW_MS - now) / 1000)));
}

export function recordFailure(ip: string, now = Date.now()): void {
  global.push(now);
  perIp.set(ip, [...recent(perIp.get(ip) ?? [], now), now]);
}

/** A success clears that address's own strikes but not the global bucket. */
export function recordSuccess(ip: string): void {
  perIp.delete(ip);
}

/** Test hook. */
export function resetAttempts(): void {
  perIp.clear();
  global = [];
}
