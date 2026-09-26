"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Copy, Loader2, Trash2 } from "lucide-react";
import { Field, SectionCard } from "@/components/builder/field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useBuilder } from "@/lib/store/builder";
import { deleteAgent } from "@/lib/api/agents";
import { useCan } from "@/lib/rbac";

export function SettingsTab() {
  const router = useRouter();
  const qc = useQueryClient();
  const draft = useBuilder((s) => s.draft);
  const update = useBuilder((s) => s.update);
  // The store's agentId is the agent, not the version — the delete target.
  const agentId = useBuilder((s) => s.agentId);
  // Matches the server: `delete_agent` requires AGENTS_WRITE, so viewer/operator get no button.
  const canWrite = useCan("agents:write");

  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!draft) return null;

  function closeConfirm() {
    if (deleting) return; // don't let a click-away abandon an in-flight delete
    setConfirming(false);
    setError(null);
  }

  async function onDelete() {
    if (!agentId || deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteAgent(agentId);
      // This agent is in every cached agents list, the dashboard panels and the analytics
      // pickers — drop them all rather than trying to splice one id out of each.
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.removeQueries({ queryKey: ["agent", agentId] });
      // Leave before rendering a builder whose agent no longer exists. Navigating also
      // unmounts the page holding the debounced autosave timer, so no stray PATCH follows.
      router.push("/agents");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this agent.");
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionCard title="General" description="Basic details for this agent.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Agent name" htmlFor="agentName">
            <Input
              id="agentName"
              value={draft.name}
              onChange={(e) => update((d) => void (d.name = e.target.value))}
            />
          </Field>
          <Field label="Agent ID">
            <Input value={draft.id} readOnly className="font-mono text-muted" />
          </Field>
        </div>
      </SectionCard>

      <SectionCard title="Duplicate" description="Clone this agent's full configuration into a new draft.">
        <Button variant="outline" size="sm">
          <Copy /> Duplicate agent
        </Button>
      </SectionCard>

      {canWrite && (
        <section className="rounded-lg border border-error/30 bg-error/[0.04]">
          <div className="border-b border-error/20 p-5">
            <h3 className="font-display text-base font-semibold text-error-text">Danger zone</h3>
            <p className="mt-0.5 text-sm text-muted">
              Deleting an agent removes its versions, conversations, and channel connections. This cannot be
              undone.
            </p>
          </div>
          <div className="p-5">
            <Button variant="destructive" size="sm" onClick={() => setConfirming(true)}>
              <Trash2 /> Delete agent
            </Button>
          </div>

          <Dialog open={confirming} onOpenChange={(v) => (v ? setConfirming(true) : closeConfirm())}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Delete {draft.name}?</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted">
                Press OK to delete this agent. Its versions, conversations and channel connections go
                with it, any embedded widget stops answering, and this cannot be undone.
              </p>
              {error && <p className="mt-3 text-sm text-error-text">{error}</p>}
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={closeConfirm} disabled={deleting}>
                  Cancel
                </Button>
                <Button variant="destructive" size="sm" onClick={onDelete} disabled={deleting}>
                  {deleting ? <Loader2 className="animate-spin" /> : <Trash2 />}
                  {deleting ? "Deleting…" : "OK, delete agent"}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </section>
      )}
    </div>
  );
}
