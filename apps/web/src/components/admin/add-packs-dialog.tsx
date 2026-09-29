"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { addPacks, type AdminOrg } from "@/lib/api/admin";
import { getPlans } from "@/lib/api/billing";
import { usd } from "@/lib/utils";

/** Quantity in packs, never a message count — the server computes messages and price from the
 *  org's *current* plan (docs/22 §8.2 rule 6), so the frontend only ever reads the rate to show
 *  a live preview. It never sends a price. */
export function AddPacksDialog({
  org,
  open,
  onOpenChange,
}: {
  org: AdminOrg;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const { data: pricing } = useQuery({ queryKey: ["billing-plans"], queryFn: getPlans, staleTime: Infinity });
  const plan = pricing?.plans.find((p) => p.id === org.plan);

  const [packs, setPacks] = useState(1);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: () => addPacks(org.id, packs, note.trim()),
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
      setPacks(1);
      setNote("");
      setError(null);
    }
    onOpenChange(next);
  }

  const messages = plan ? packs * plan.extra_message_pack_size : 0;
  const cost = plan ? packs * plan.extra_message_pack_usd : 0;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add message packs — {org.name}</DialogTitle>
        </DialogHeader>

        {!plan ? (
          <p className="text-sm text-muted">{org.name} is on {org.plan}, which doesn&apos;t sell packs.</p>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!note.trim()) return;
              setError(null);
              submit.mutate();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="packs-qty">Packs</Label>
              <Input
                id="packs-qty"
                type="number"
                min={1}
                step={1}
                value={packs}
                onChange={(e) => setPacks(Math.max(1, Math.round(Number(e.target.value) || 1)))}
              />
            </div>

            <p className="rounded-md border border-border bg-surface-2/40 p-2.5 text-sm text-text">
              {packs} pack{packs === 1 ? "" : "s"} × {plan.extra_message_pack_size.toLocaleString()} ={" "}
              {messages.toLocaleString()} messages · invoice {usd(cost)} ({plan.name} rate: {usd(plan.extra_message_pack_usd)} /{" "}
              {plan.extra_message_pack_size.toLocaleString()})
            </p>
            <p className="text-xs text-warn-text">Packs do not carry over to the next period.</p>

            <div className="space-y-1.5">
              <Label htmlFor="packs-note">Note</Label>
              <Textarea
                id="packs-note"
                placeholder="Invoice reference"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                aria-invalid={!note.trim()}
              />
            </div>

            {error && (
              <p role="alert" className="text-sm text-error-text">
                {error}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" size="sm" onClick={() => handleOpenChange(false)} disabled={submit.isPending}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" size="sm" disabled={submit.isPending || !note.trim()}>
                {submit.isPending && <Loader2 className="size-4 animate-spin" />} Add packs
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
