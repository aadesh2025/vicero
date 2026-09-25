import { getVaultSession } from "@/lib/vault/session";
import { isKnownSecret, revealSecret } from "@/lib/vault/secrets";
import { apiError, json, sameOrigin } from "@/lib/vault/http";

// POST /api/vault/reveal {name} — the real value of one configuration entry.
//
// The only route that ever returns a secret, and it re-checks the session every time: a
// page that was rendered while signed in grants nothing to a later request.

export async function POST(request: Request) {
  if (!sameOrigin(request)) return apiError(403, "vault.bad_origin", "Cross-site request refused.");

  const email = await getVaultSession();
  if (!email) return apiError(401, "vault.unauthenticated", "Sign in to continue.");

  let name = "";
  try {
    const body = (await request.json()) as { name?: unknown };
    if (typeof body.name === "string") name = body.name;
  } catch {
    return apiError(400, "vault.bad_request", "Invalid request.");
  }

  // 404 for an unknown name and for an unset one alike — and for the vault's own
  // credentials, which are not in the known set at all.
  const value = isKnownSecret(name) ? revealSecret(name) : null;
  if (value === null) return apiError(404, "vault.not_found", "No such value.");

  // Audit trail: who looked at what, never the value. This goes to the server log only.
  console.info(JSON.stringify({ event: "vault_reveal", name, by: email, at: new Date().toISOString() }));
  return json({ value });
}
