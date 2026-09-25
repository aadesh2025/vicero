import { NextResponse } from "next/server";
import { VAULT_COOKIE } from "@/lib/vault/session";
import { apiError, sameOrigin } from "@/lib/vault/http";

// POST /api/vault/logout — clears the vault cookie. Allowed without a valid session: signing
// out an already-expired session should not be an error.
export async function POST(request: Request) {
  if (!sameOrigin(request)) return apiError(403, "vault.bad_origin", "Cross-site request refused.");
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(VAULT_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
  return response;
}
