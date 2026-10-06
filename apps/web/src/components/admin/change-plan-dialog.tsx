"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { GRANTABLE_PLANS, PAID_PLANS, grantPlan, type AdminOrg } from "@/lib/api/admin";
import { CurrencySelect, useAdminPricing } from "@/components/admin/currency-select";
import { formatMoney } from "@/lib/money";

type ExpiryMode = "30" | "90" | "custom" | "none";

function fmtLimit(n: number | null): string {
  return n === null ? "unlimited" : n.toLocaleString();
}

function fmtDate(iso: string | null): string {
  if (!iso) return "no expiry";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function ChangePlanDialog({
  org,
  open,
  onOpenChange,
}: {
  org: AdminOrg;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const [currency, setCurrency] = useState("USD");
  const { data: pricing } = useAdminPricing(currency);
  // Captured once via the lazy initializer rather than calling Date.now() during render
  // (react-hooks/purity) — this only labels a preview, so it never needs to be live-ticking.
  const [now] = useState(() => Date.now());

  const [plan, setPlan] = useState(org.plan);
  const [expiryMode, setExpiryMode] = useState<ExpiryMode>("30");
  const [customDate, setCustomDate] = useState("");
  const [paymentReceived, setPaymentReceived] = useState(true);
  const [method, setMethod] = useState("bank transfer");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: () => {
      let expires_at: string | null;
      if (expiryMode === "none") expires_at = null;
      else if (expiryMode === "30") expires_at = new Date(Date.now() + 30 * 86_400_000).toISOString();
      else if (expiryMode === "90") expires_at = new Date(Date.now() + 90 * 86_400_000).toISOString();
      else expires_at = customDate ? new Date(`${customDate}T00:00:00`).toISOString() : null;

      return grantPlan(org.id, {
        plan,
        expires_at,
        note: note.trim(),
        payment_received: PAID_PLANS.includes(plan as (typeof PAID_PLANS)[number]) ? paymentReceived : false,
        method: paymentReceived ? method : null,
        reference: paymentReceived ? reference.trim() || null : null,
        currency,
      });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin-orgs"] });
      await qc.invalidateQueries({ queryKey: ["admin-orgs-billing"] });
      await qc.invalidateQueries({ queryKey: ["admin-billing-totals"] });
      await qc.invalidateQueries({ queryKey: ["admin-org", org.id] });
      onOpenChange(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const currentPlanInfo = pricing?.plans.find((p) => p.id === org.plan);
  const nextPlanInfo = pricing?.plans.find((p) => p.id === plan);
  const isPaid = PAID_PLANS.includes(plan as (typeof PAID_PLANS)[number]);

  const diff = useMemo(() => {
    const expiresText =
      expiryMode === "none"
        ? "no expiry"
        : expiryMode === "custom"
          ? customDate
            ? `expires ${fmtDate(new Date(`${customDate}T00:00:00`).toISOString())}`
            : "pick a date"
          : `expires ${fmtDate(new Date(now + Number(expiryMode) * 86_400_000).toISOString())}`;
    // /v1/billing/plans only prices the paid tiers — trial and legacy are granted, never sold,
    // so there is no limits table for them here. Show the full limits diff when both sides have
    // one; otherwise still show a plan-and-expiry line rather than nothing (never a bare "are you
    // sure").
    if (!currentPlanInfo || !nextPlanInfo) {
      return `${org.plan} → ${plan} · ${expiresText}`;
    }
    return (
      `${org.plan} → ${plan} · agents ${fmtLimit(currentPlanInfo.limits.agents)} → ${fmtLimit(nextPlanInfo.limits.agents)} · ` +
      `messages ${fmtLimit(currentPlanInfo.limits.messages)} → ${fmtLimit(nextPlanInfo.limits.messages)} · ` +
      `n8n ${currentPlanInfo.features.n8n ? "ON" : "OFF"} → ${nextPlanInfo.features.n8n ? "ON" : "OFF"} · ${expiresText}`
    );
  }, [currentPlanInfo, nextPlanInfo, org.plan, plan, expiryMode, customDate, now]);

  function handleOpenChange(next: boolean) {
    if (!next) {
      setPlan(org.plan);
      setCurrency("USD");
      setExpiryMode("30");
      setCustomDate("");
      setPaymentReceived(true);
      setMethod("bank transfer");
      setReference("");
      setNote("");
      setError(null);
    }
    onOpenChange(next);
  }

  const noteEmpty = !note.trim();

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Change plan — {org.name}</DialogTitle>
          <DialogDescription>Takes effect on the client&apos;s very next request.</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (noteEmpty) return;
            setError(null);
            submit.mutate();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="cp-plan">Plan</Label>
            <Select value={plan} onValueChange={setPlan}>
              <SelectTrigger id="cp-plan">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {GRANTABLE_PLANS.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p.charAt(0).toUpperCase() + p.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {isPaid && (
            <div className="space-y-1.5">
              <CurrencySelect id="cp-currency" value={currency} onChange={setCurrency} disabled={submit.isPending} />
              {nextPlanInfo && (
                <p className="text-xs text-muted">
                  Cycle amount: {formatMoney(nextPlanInfo.price_minor, currency)} / month ({pricing?.tax_note})
                </p>
              )}
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="cp-expiry">Expiry</Label>
            <Select value={expiryMode} onValueChange={(v) => setExpiryMode(v as ExpiryMode)}>
              <SelectTrigger id="cp-expiry">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="30">30 days (default)</SelectItem>
                <SelectItem value="90">90 days</SelectItem>
                <SelectItem value="custom">Custom date</SelectItem>
                <SelectItem value="none">No expiry</SelectItem>
              </SelectContent>
            </Select>
            {expiryMode === "custom" && (
              <Input
                type="date"
                value={customDate}
                onChange={(e) => setCustomDate(e.target.value)}
                min={new Date(now + 86_400_000).toISOString().slice(0, 10)}
              />
            )}
          </div>

          {isPaid && (
            <div className="space-y-3 rounded-lg border border-border bg-surface-2/40 p-3">
              <label className="flex items-center gap-2 text-sm font-medium text-text">
                <input
                  type="checkbox"
                  checked={paymentReceived}
                  onChange={(e) => setPaymentReceived(e.target.checked)}
                  className="size-4 rounded border-border-strong"
                />
                Payment already received
              </label>
              {paymentReceived ? (
                <div className="grid grid-cols-2 gap-2">
                  <Select value={method} onValueChange={setMethod}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bank transfer">Bank transfer</SelectItem>
                      <SelectItem value="upi">UPI</SelectItem>
                      <SelectItem value="paypal">PayPal</SelectItem>
                      <SelectItem value="other">Other</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input
                    placeholder="Reference (UTR, invoice no.)"
                    value={reference}
                    onChange={(e) => setReference(e.target.value)}
                  />
                </div>
              ) : (
                <p className="text-xs text-muted">
                  The cycle opens as pending and the workspace appears in the collections list.
                </p>
              )}
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="cp-note">Note (required)</Label>
            <Textarea
              id="cp-note"
              placeholder='Acme Corp — paid $99 via bank transfer, invoice INV-004'
              value={note}
              onChange={(e) => setNote(e.target.value)}
              aria-invalid={noteEmpty}
            />
          </div>

          {diff && <p className="rounded-md border border-border bg-surface-2/40 p-2.5 text-xs text-muted">{diff}</p>}

          {error && (
            <p role="alert" className="text-sm text-error-text">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => handleOpenChange(false)} disabled={submit.isPending}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="sm" disabled={submit.isPending || noteEmpty}>
              {submit.isPending && <Loader2 className="size-4 animate-spin" />} Confirm change
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
