"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { me } from "@/lib/api/auth";
import { createOrg, listOrgs } from "@/lib/api/orgs";
import { getAccessToken, getActiveOrgId, setActiveOrgId, clearAuth } from "@/lib/api/tokens";
import { useSession } from "@/lib/store/session";
import { LogoMark } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Bootstraps the session on the client: loads /me + orgs, or bounces to /login. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { ready, orgs, setSession } = useSession();

  useEffect(() => {
    if (!getAccessToken()) {
      router.replace("/login");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [profile, loaded] = await Promise.all([me(), listOrgs()]);
        if (cancelled) return;
        const stored = getActiveOrgId();
        const active = loaded.find((o) => o.id === stored)?.id ?? loaded[0]?.id ?? null;
        if (active) setActiveOrgId(active);
        setSession(profile.user, loaded, active);
      } catch (err) {
        if (cancelled) return;
        // Only a genuine auth failure may destroy the session. This bootstrap runs on every
        // page load, and navigating while it's still in flight **aborts** its requests — the
        // browser rejects them with a TypeError, not a 401. Treating that as "your token is
        // bad" logged people out for the crime of clicking a link too quickly, moments after
        // signing in. Anything that isn't a 401 (abort, offline, a 500) leaves the tokens
        // alone; the next load re-runs this and succeeds.
        if (err instanceof ApiError && err.status === 401) {
          clearAuth();
          router.replace("/login");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, setSession]);

  if (!ready) {
    return <Splash label="Loading your workspace…" />;
  }
  if (orgs.length === 0) {
    return <NoWorkspace />;
  }
  return <>{children}</>;
}

function Splash({ label }: { label: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="flex items-center gap-2 text-muted">
        <LogoMark className="animate-pulse" />
        <span className="text-sm">{label}</span>
      </div>
    </div>
  );
}

/** Shown to a signed-in account that belongs to no organization.
 *
 * With self-serve on, they can create their one trial workspace right here (normally signup did
 * it already; this serves someone whose invitation lapsed). With it off — the operator-provisioned
 * deployment — creating one is staff-only server-side (`orgs.create_forbidden`), so the screen
 * falls back to naming the address an invitation has to be sent to.
 */
function NoWorkspace() {
  const user = useSession((s) => s.user);
  const setSession = useSession((s) => s.setSession);
  const [name, setName] = useState(user?.full_name ? `${user.full_name.split(" ")[0]}'s workspace` : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inviteOnly, setInviteOnly] = useState(false);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createOrg(name.trim());
      const orgs = await listOrgs();
      setActiveOrgId(created.id);
      setSession(user, orgs, created.id);
    } catch (err) {
      if (err instanceof ApiError && err.code === "orgs.create_forbidden") setInviteOnly(true);
      else setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="glow-accent pointer-events-none absolute inset-0" />
      <div className="relative w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-pop">
        {inviteOnly ? (
          <>
            <h1 className="font-display text-xl font-semibold text-text">No workspace yet</h1>
            <p className="mt-2 text-sm text-muted">
              Your account isn&apos;t linked to an organization. If you&apos;re expecting access, ask whoever invited
              you to send a fresh invite link to this email
              {user?.email ? (
                <>
                  {" "}
                  (<span className="font-mono text-text">{user.email}</span>)
                </>
              ) : null}{" "}
              — or contact your BotForge rep.
            </p>
            <p className="mt-4 text-xs text-faint">
              Already have an invite link? Open it while signed in and you&apos;ll join straight away.
            </p>
          </>
        ) : (
          <form onSubmit={onCreate} className="space-y-4">
            <div>
              <h1 className="font-display text-xl font-semibold text-text">Name your workspace</h1>
              <p className="mt-1 text-sm text-muted">
                You don&apos;t belong to a workspace yet. Create yours to start your free trial.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="workspace-name">Workspace name</Label>
              <Input id="workspace-name" required maxLength={255} value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            {error && (
              <p role="alert" className="rounded-md border border-error/30 bg-error/10 px-3 py-2 text-sm text-error">
                {error}
              </p>
            )}
            <Button type="submit" variant="primary" className="w-full" disabled={busy || !name.trim()}>
              Create workspace
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
