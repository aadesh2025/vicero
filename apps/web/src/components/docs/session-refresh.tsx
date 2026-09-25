"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

/**
 * Recovers a server render that found an expired access token.
 *
 * The internal docs are gated on the server, which reads the short-lived `bf_access`
 * cookie. Leave a tab open for half an hour and that cookie ages out, so the next
 * navigation is refused even though the session behind it is perfectly good. Rather than
 * show a staff member a permission error for reading slowly, exchange the httpOnly refresh
 * cookie through the existing BFF and re-render.
 *
 * One attempt only. If the refresh fails the session really is gone, and retrying in a
 * loop would just spin against a 401.
 */
export function SessionRefresh() {
  const router = useRouter();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/auth/refresh", { method: "POST" });
        if (cancelled) return;
        if (res.ok) router.refresh();
        else setFailed(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="grid min-h-[40vh] place-items-center text-sm text-muted">
      {failed ? "Your session has expired. Sign in again to continue." : "Restoring your session…"}
    </div>
  );
}
