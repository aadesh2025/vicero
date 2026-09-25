/** The gate in front of the internal documentation.
 *
 * ## Why this is server-side
 *
 * The internal docs describe the architecture, the full endpoint surface and every
 * environment variable this system reads. That is a map of the attack surface, and the
 * client-side pattern used elsewhere in this app — render, then `useEffect` a redirect —
 * is not sufficient for it: the content would already be in the JavaScript payload before
 * the redirect ran, readable from devtools on any trial account.
 *
 * So the check runs on the server, before any content is read from disk, and the pages it
 * guards are server components. A visitor who is not staff never receives the bytes.
 *
 * ## Why it reads `bf_access` rather than the refresh cookie
 *
 * `bf_refresh` is httpOnly and would be the obvious thing to use, but exchanging it
 * **rotates** it (`app/api/auth/refresh/route.ts`) — a server render that spent the user's
 * refresh token would invalidate the copy their browser holds and log them out of the tab
 * they are reading in. `bf_access` is a short-lived, JS-readable cookie set with `path=/`,
 * so it rides along on a same-origin request and can be verified without spending
 * anything.
 *
 * Its short life is why `expired` is a distinct outcome from `forbidden`: the two need
 * different handling, and collapsing them would log a staff member out for reading slowly.
 */

import "server-only";

import { cookies } from "next/headers";
import { COOKIE } from "@/lib/api/config";
import { API_BASE } from "@/app/api/auth/_bff";
import type { ApiUser } from "@/lib/api/types";

export type StaffCheck =
  | { status: "ok"; user: ApiUser }
  /** A session exists but its access token has aged out. Recoverable without signing in. */
  | { status: "expired" }
  /** No session at all, or a valid session belonging to someone who is not staff. */
  | { status: "forbidden" };

export async function checkStaff(): Promise<StaffCheck> {
  const token = (await cookies()).get(COOKIE.access)?.value;
  if (!token) return { status: "forbidden" };

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    // The API is unreachable. Fail closed: an outage must not open the internal docs.
    return { status: "forbidden" };
  }

  if (response.status === 401) return { status: "expired" };
  if (!response.ok) return { status: "forbidden" };

  const body = (await response.json()) as { user?: ApiUser };
  const user = body.user;
  // `=== true`, not truthy: a malformed response must not read as authorised.
  if (!user || user.is_staff !== true) return { status: "forbidden" };
  return { status: "ok", user };
}
