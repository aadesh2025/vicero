import { sessionRoute } from "../_bff";

// POST /api/auth/oauth-exchange → trades the provider redirect's one-time code for a session.
export const POST = sessionRoute("/v1/auth/oauth/exchange");
