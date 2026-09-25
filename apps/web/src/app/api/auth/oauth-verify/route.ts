import { sessionRoute } from "../_bff";

// POST /api/auth/oauth-verify → finishes a provider sign-in once the emailed link proved the address.
export const POST = sessionRoute("/v1/auth/oauth/email/verify");
