/** Configuration for the private admin area ("the vault").
 *
 * Three variables, all read from the server environment, none from `NEXT_PUBLIC_*`:
 *
 *   VAULT_ADMIN_EMAILS   comma-separated allow-list. The only addresses that may sign in.
 *   VAULT_PASSWORD_HASH  output of `make vault-password`. Never the password itself.
 *   VAULT_SESSION_SECRET random, 32+ characters. Signs the session cookie.
 *
 * The area is **off** until all three are present and well-formed. A half-configured vault
 * must not fall back to something permissive, so every accessor returns "nothing" rather
 * than a default, and `isConfigured()` is what login checks first.
 *
 * This is deliberately independent of Vicero's own accounts: no `users` row, no JWT, no
 * `is_staff`. Compromising or being granted a Vicero account gets you nothing here.
 */

import "server-only";

const MIN_SECRET_LENGTH = 32;

export function allowedEmails(): string[] {
  return (process.env.VAULT_ADMIN_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter((e) => e.includes("@"));
}

export function isAllowedEmail(email: string): boolean {
  const candidate = email.trim().toLowerCase();
  // `includes` over an exact-match list, not a suffix or domain rule: an allow-list entry
  // names one mailbox.
  return candidate.length > 0 && allowedEmails().includes(candidate);
}

export function passwordHash(): string | undefined {
  const value = process.env.VAULT_PASSWORD_HASH?.trim();
  return value ? value : undefined;
}

export function sessionSecret(): string | undefined {
  const value = process.env.VAULT_SESSION_SECRET;
  return value && value.length >= MIN_SECRET_LENGTH ? value : undefined;
}

export function isConfigured(): boolean {
  return allowedEmails().length > 0 && Boolean(passwordHash()) && Boolean(sessionSecret());
}
