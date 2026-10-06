"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { listBillingCycles, markCyclePaid, waiveCycle, type BillingCycle } from "@/lib/api/admin";
import { PaymentBadge } from "@/components/admin/billing-shared";
import { CurrencySelect, useAdminPricing } from "@/components/admin/currency-select";
import { formatMoney } from "@/lib/money";

type DialogState = { cycle: BillingCycle; type: "paid" | "waive" } | null;

function daysOverdue(periodEnd: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(periodEnd).getTime()) / 86_400_000));
}

/** The ledger from `GET /v1/admin/billing/cycles`, grouped by state (docs/22 §9.5) —
 *  overdue first, since that's the collections list. */
export function PaymentsTab() {
  const { data: cycles, isLoading } = useQuery({ queryKey: ["admin-billing-cycles", "all"], queryFn: () => listBillingCycles() });
  const [dialog, setDialog] = useState<DialogState>(null);

  const overdue = (cycles ?? []).filter((c) => c.payment_state === "overdue");
  const pending = (cycles ?? []).filter((c) => c.payment_state === "pending");
  const paid = (cycles ?? [])
    .filter((c) => c.payment_state === "paid")
    .sort((a, b) => new Date(b.paid_at ?? b.created_at).getTime() - new Date(a.paid_at ?? a.created_at).getTime())
    .slice(0, 20);

  if (isLoading) {
    return <p className="p-8 text-center text-sm text-muted">Loading the payment ledger…</p>;
  }

  if ((cycles ?? []).length === 0) {
    return <p className="p-8 text-center text-sm text-muted">No billing cycles yet — grant a paid plan to open one.</p>;
  }

  return (
    <div className="space-y-6">
      <CycleGroup
        title="Overdue"
        tone="error"
        cycles={overdue}
        empty="Nothing overdue."
        renderMeta={(c) => `${daysOverdue(c.period_end)} days overdue`}
        onMarkPaid={(c) => setDialog({ cycle: c, type: "paid" })}
        onWaive={(c) => setDialog({ cycle: c, type: "waive" })}
      />
      <CycleGroup
        title="Pending"
        tone="warn"
        cycles={pending}
        empty="Nothing pending."
        renderMeta={(c) => `due ${new Date(c.period_end).toLocaleDateString()}`}
        onMarkPaid={(c) => setDialog({ cycle: c, type: "paid" })}
        onWaive={(c) => setDialog({ cycle: c, type: "waive" })}
      />
      <CycleGroup
        title="Paid this month"
        tone="success"
        cycles={paid}
        empty="Nothing marked paid yet."
        renderMeta={(c) => `${c.paid_at ? new Date(c.paid_at).toLocaleDateString() : "—"}${c.method ? ` · ${c.method}` : ""}${c.reference ? ` · ${c.reference}` : ""}`}
      />

      {dialog?.type === "paid" && <MarkPaidDialog cycle={dialog.cycle} open onOpenChange={(v) => !v && setDialog(null)} />}
      {dialog?.type === "waive" && <WaiveDialog cycle={dialog.cycle} open onOpenChange={(v) => !v && setDialog(null)} />}
    </div>
  );
}

function CycleGroup({
  title,
  tone,
  cycles,
  empty,
  renderMeta,
  onMarkPaid,
  onWaive,
}: {
  title: string;
  tone: "error" | "warn" | "success";
  cycles: BillingCycle[];
  empty: string;
  renderMeta: (c: BillingCycle) => string;
  onMarkPaid?: (c: BillingCycle) => void;
  onWaive?: (c: BillingCycle) => void;
}) {
  const border = tone === "error" ? "border-error/30" : tone === "warn" ? "border-warn/30" : "border-border";
  return (
    <section className={`rounded-lg border ${border} bg-surface`}>
      <div className="border-b border-border p-4">
        <h3 className="font-display text-sm font-bold text-text">
          {title} <span className="ml-1 text-xs font-medium text-faint">({cycles.length})</span>
        </h3>
      </div>
      {cycles.length === 0 ? (
        <p className="p-5 text-sm text-muted">{empty}</p>
      ) : (
        <ul className="divide-y divide-border">
          {cycles.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <div>
                <div className="text-sm font-medium text-text">{c.organization_name ?? c.organization_id}</div>
                <div className="text-xs text-faint">
                  {c.plan} · {formatMoney(c.amount_minor, c.currency)} · {renderMeta(c)}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <PaymentBadge state={c.payment_state} />
                {onMarkPaid && (
                  <Button size="sm" variant="primary" onClick={() => onMarkPaid(c)}>
                    Mark paid
                  </Button>
                )}
                {onWaive && (
                  <Button size="sm" variant="outline" onClick={() => onWaive(c)}>
                    Waive
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function MarkPaidDialog({ cycle, open, onOpenChange }: { cycle: BillingCycle; open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const [method, setMethod] = useState("bank transfer");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [currency, setCurrency] = useState(cycle.currency);
  const [error, setError] = useState<string | null>(null);
  const { data: pricing } = useAdminPricing(currency);
  // Paid in a different currency than invoiced: the server re-prices the pending cycle from the
  // plan table. Preview from that same table; the amount itself is never sent.
  const repriced = currency !== cycle.currency;
  const planInfo = pricing?.plans.find((p) => p.id === cycle.plan);
  const shownAmount = repriced ? planInfo?.price_minor : cycle.amount_minor;

  const submit = useMutation({
    mutationFn: (renew: boolean) =>
      markCyclePaid(cycle.id, {
        method,
        reference: reference.trim() || null,
        note: note.trim() || null,
        renew,
        currency: repriced ? currency : null,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin-billing-cycles"] });
      await qc.invalidateQueries({ queryKey: ["admin-billing-totals"] });
      await qc.invalidateQueries({ queryKey: ["admin-orgs-billing"] });
      onOpenChange(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Mark paid</DialogTitle>
        </DialogHeader>

        <p className="rounded-md border border-border bg-surface-2/40 p-2.5 text-sm text-text">
          {cycle.organization_name ?? cycle.organization_id} · {cycle.plan} · {shownAmount === undefined ? "…" : formatMoney(shownAmount, currency)} · cycle{" "}
          {new Date(cycle.period_start).toLocaleDateString()} – {new Date(cycle.period_end).toLocaleDateString()}
        </p>

        <div className="mt-4 space-y-3">
          <CurrencySelect id="mp-currency" value={currency} onChange={setCurrency} disabled={submit.isPending} />
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1.5">
              <Label htmlFor="mp-method">Method</Label>
              <Select value={method} onValueChange={setMethod}>
                <SelectTrigger id="mp-method">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="bank transfer">Bank transfer</SelectItem>
                  <SelectItem value="upi">UPI</SelectItem>
                  <SelectItem value="paypal">PayPal</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mp-ref">Reference</Label>
              <Input id="mp-ref" placeholder="UTR, invoice no." value={reference} onChange={(e) => setReference(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="mp-note">Note</Label>
            <Textarea id="mp-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>

        {error && (
          <p role="alert" className="mt-3 text-sm text-error-text">
            {error}
          </p>
        )}

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={submit.isPending}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={submit.isPending}
            onClick={() => {
              setError(null);
              submit.mutate(false);
            }}
          >
            {submit.isPending && <Loader2 className="size-4 animate-spin" />} Mark paid
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={submit.isPending}
            onClick={() => {
              setError(null);
              submit.mutate(true);
            }}
          >
            {submit.isPending && <Loader2 className="size-4 animate-spin" />} Mark paid &amp; renew
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function WaiveDialog({ cycle, open, onOpenChange }: { cycle: BillingCycle; open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: () => waiveCycle(cycle.id, note.trim()),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin-billing-cycles"] });
      await qc.invalidateQueries({ queryKey: ["admin-billing-totals"] });
      await qc.invalidateQueries({ queryKey: ["admin-orgs-billing"] });
      onOpenChange(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const noteEmpty = !note.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Waive this cycle?</DialogTitle>
        </DialogHeader>

        <p className="text-sm text-muted">
          For comps, demos and your own workspaces — keeps {cycle.organization_name ?? "this workspace"} out of the overdue
          list without recording a payment.
        </p>

        <div className="mt-4 space-y-1.5">
          <Label htmlFor="waive-note">Note (required)</Label>
          <Textarea id="waive-note" value={note} onChange={(e) => setNote(e.target.value)} aria-invalid={noteEmpty} />
        </div>

        {error && (
          <p role="alert" className="mt-3 text-sm text-error-text">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={submit.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={submit.isPending || noteEmpty}
            onClick={() => {
              setError(null);
              submit.mutate();
            }}
          >
            {submit.isPending && <Loader2 className="size-4 animate-spin" />} Waive
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
