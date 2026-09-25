import { sessionRoute } from "../_bff";

// POST /api/auth/magic → redeems a magic-link token; the refresh token goes to the httpOnly cookie.
export const POST = sessionRoute("/v1/auth/magic-link/verify");
