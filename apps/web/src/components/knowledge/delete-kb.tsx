"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Trash2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { deleteKnowledgeBase } from "@/lib/api/knowledge";
import type { ApiAttachedAgent } from "@/lib/api/types";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";

/** Danger zone for a knowledge base.
 *
 * The endpoint has existed since Phase 7 with nothing calling it, so a KB could be created but
 * never removed. It soft-deletes, and `retrieve_for_version` already filters `deleted_at`, so
 * an agent still pointing at this KB does not break — it just silently stops retrieving. That
 * is precisely why the confirmation lists those agents: an agent with no context block answers
 * from general knowledge and invents specifics, which this project has already shipped once
 * (see CLAUDE.md, 2026-08-02). Deleting the KB a live agent depends on should feel like a
 * decision, not a tidy-up.
 */
export function DeleteKnowledgeBase({
  kbId,
  name,
  documentCount,
  attachedAgents,
}: {
  kbId: string;
  name: string;
  documentCount: number;
  attachedAgents: ApiAttachedAgent[];
}) {
  const router = useRouter();
  const qc = useQueryClient();
  const orgId = useSession((s) => s.activeOrgId);
  const canManage = useCan("kb:manage");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const remove = useMutation({
    mutationFn: () => deleteKnowledgeBase(kbId),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["knowledge-bases", orgId] });
      router.push("/knowledge");
    },
    onError: (e: Error) => setError(e.message),
  });

  // The server enforces kb:manage regardless; hiding it keeps a viewer from a guaranteed 403.
  if (!canManage) return null;

  const one = attachedAgents.length === 1;
  const docs =
    documentCount === 0
      ? "It has no documents yet."
      : `Its ${documentCount} ${documentCount === 1 ? "document" : "documents"} and everything indexed from ${documentCount === 1 ? "it" : "them"} go too.`;

  return (
    <section className="rounded-lg border border-error/30 bg-surface">
      <div className="border-b border-error/20 p-5">
        <h3 className="font-display text-base font-semibold text-error-text">Danger zone</h3>
        <p className="mt-0.5 text-sm text-muted">
          Deleting this knowledge base is permanent. {docs}
        </p>
      </div>
      <div className="p-5">
        <Button variant="destructive" size="sm" onClick={() => setConfirming(true)}>
          <Trash2 /> Delete knowledge base
        </Button>
      </div>

      <Dialog open={confirming} onOpenChange={(v) => (v ? setConfirming(true) : setConfirming(false))}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {name}?</DialogTitle>
          </DialogHeader>

          <p className="text-sm text-muted">{docs} This cannot be undone.</p>

          {attachedAgents.length > 0 && (
            <div className="mt-4 rounded-md border border-warn/30 bg-warn/[0.06] p-3">
              <p className="flex items-start gap-2 text-sm text-text">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn-text" />
                {/* One expression rather than text interleaved with ternaries: JSX drops the
                    whitespace around an expression in some positions, which silently produced
                    "keepsanswering" here. */}
                <span>
                  {one
                    ? "An agent uses this knowledge base. It keeps answering after it's gone — but with nothing to ground it, so answers may be invented."
                    : `${attachedAgents.length} agents use this knowledge base. They keep answering after it's gone — but with nothing to ground them, so answers may be invented.`}
                </span>
              </p>
              <ul className="mt-2 space-y-1 pl-6">
                {attachedAgents.map((a) => (
                  <li key={a.id} className="text-sm text-muted">
                    {a.name}
                    {a.is_live && <span className="ml-1.5 text-xs text-warn-text">· live</span>}
                  </li>
                ))}
              </ul>
              <p className="mt-2 pl-6 text-xs text-faint">
                {one
                  ? "Detach it in the agent's Knowledge tab first, or attach a replacement."
                  : "Detach it in each agent's Knowledge tab first, or attach a replacement."}
              </p>
            </div>
          )}

          {error && (
            <p role="alert" className="mt-3 text-sm text-error-text">
              {error}
            </p>
          )}

          <div className="mt-5 flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirming(false)}
              disabled={remove.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => {
                setError(null);
                remove.mutate();
              }}
              disabled={remove.isPending}
            >
              {remove.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
              {remove.isPending ? "Deleting…" : "OK, delete it"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
