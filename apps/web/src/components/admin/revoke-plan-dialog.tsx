"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { revokePlan, type AdminOrg } from "@/lib/api/admin";

/** Revoke never deletes data (docs/22 §8.2) — it always lands on `plan_expired`: read-only,
 *  bot stops answering, every agent/document/conversation stays intact. Confirmation exists
 *  because "revoke" sounds destructive even though nothing is removed. */
export function RevokePlanDialog({
  org,
  open,
  onOpenChange,
}: {
  org: AdminOrg;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: () => revokePlan(org.id, note.trim()),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin-orgs"] });
      await qc.invalidateQueries({ queryKey: ["admin-orgs-billing"] });
      await qc.invalidateQueries({ queryKey: ["admin-org", org.id] });
      onOpenChange(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  function handleOpenChange(next: boolean) {
    if (!next) {
      setNote("");
      setError(null);
    }
    onOpenChange(next);
  }

  const noteEmpty = !note.trim();

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke {org.name}&apos;s plan?</DialogTitle>
        </DialogHeader>

        <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn/[0.06] p-3 text-sm text-text">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn-text" />
          <span>
            <strong className="font-bold">{org.name}</strong> moves to <strong className="font-bold">plan_expired</strong> —
            read-only, the bot stops replying to visitors. Every agent, document and conversation stays intact; nothing is
            deleted. Any open billing cycle is closed as waived.
          </span>
        </div>

        <div className="mt-4 space-y-1.5">
          <label htmlFor="revoke-note" className="text-sm font-bold text-text">
            Note (required)
          </label>
          <Textarea
            id="revoke-note"
            placeholder="Why is this being revoked?"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-invalid={noteEmpty}
          />
        </div>

        {error && (
          <p role="alert" className="mt-3 text-sm text-error-text">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => handleOpenChange(false)} disabled={submit.isPending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={submit.isPending || noteEmpty}
            onClick={() => {
              setError(null);
              submit.mutate();
            }}
          >
            {submit.isPending && <Loader2 className="size-4 animate-spin" />} OK, revoke it
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
