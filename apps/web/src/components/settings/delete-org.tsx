"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { deleteOrg, listOrgs } from "@/lib/api/orgs";
import { setActiveOrgId } from "@/lib/api/tokens";
import { useSession, activeOrg } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";

/** Delete the current organization. Owner-only, behind a type-the-name confirmation.
 *
 * The awkward part isn't the DELETE, it's what the app is standing on afterwards: the org
 * being deleted is the *active* one, so its id is in a cookie that every subsequent request
 * sends as `X-Org-Id`. Leaving it there points the whole session at a deleted org, which
 * fails on the next call rather than here, where it would be explicable. So on success the
 * cookie, the Zustand store and the query cache are all moved off it before navigating.
 */
export function DeleteOrg() {
  const router = useRouter();
  const qc = useQueryClient();
  const org = useSession(activeOrg);
  const orgs = useSession((s) => s.orgs);
  const setOrgs = useSession((s) => s.setOrgs);
  const setActiveOrg = useSession((s) => s.setActiveOrg);
  // ORG_MANAGE is owner-only in the matrix (docs/02 §6) — admins deliberately can't do this.
  const canManageOrg = useCan("org:manage");

  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!org || !canManageOrg) return null;

  const confirmed = typed.trim() === org.name;
  const isLastOrg = orgs.length <= 1;

  function close() {
    setOpen(false);
    setTyped("");
    setError(null);
  }

  async function onDelete() {
    if (!org || !confirmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await deleteOrg(org.id);

      // Re-read from the server rather than filtering locally: it's the same list the rest of
      // the app trusts, and it confirms the delete actually took.
      const remaining = await listOrgs();
      setOrgs(remaining);
      const next = remaining[0] ?? null;
      if (next) {
        setActiveOrgId(next.id); // cookie — what `X-Org-Id` is built from
        setActiveOrg(next.id); // store — what the UI renders
      }
      // Everything cached is scoped to the org that just went away.
      qc.clear();
      close();
      // No orgs left: AuthGate takes over with the create-first-org prompt.
      router.push("/dashboard");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this organization.");
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-error/30 bg-error/[0.04]">
      <div className="border-b border-error/20 p-5">
        <h2 className="font-display text-base font-semibold text-error-text">Danger zone</h2>
        <p className="mt-0.5 text-sm text-muted">
          Deleting <span className="text-text">{org.name}</span> removes its agents, conversations,
          knowledge bases and channel connections from Vicero. Members lose access immediately.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 p-5">
        <p className="text-xs text-faint">
          {isLastOrg
            ? "This is your only workspace — you'll be asked to create a new one straight after."
            : `You'll be switched to ${orgs.find((o) => o.id !== org.id)?.name ?? "another workspace"}.`}
        </p>
        <Button variant="destructive" size="sm" onClick={() => setOpen(true)}>
          <Trash2 /> Delete organization
        </Button>
      </div>

      <Dialog open={open} onOpenChange={(v) => (v ? setOpen(true) : close())}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {org.name}?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted">
            This affects everyone in the workspace, not just you. Agents stop answering, embedded
            widgets stop responding, and connected channels go quiet.
          </p>
          <label className="mt-4 block text-xs text-muted">
            Type <span className="font-mono text-text">{org.name}</span> to confirm
          </label>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={org.name}
            autoFocus
            aria-label={`Type ${org.name} to confirm deletion`}
          />
          {error && <p className="mt-3 text-sm text-error-text">{error}</p>}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={close} disabled={busy}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" onClick={onDelete} disabled={!confirmed || busy}>
              {busy ? <Loader2 className="animate-spin" /> : <Trash2 />}
              {busy ? "Deleting…" : "Delete organization"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
