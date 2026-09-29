"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { GRANTABLE_PLANS, listAdminOrgs, listBillingCycles, type OrgStatusFilter } from "@/lib/api/admin";
import { OrgBillingTable } from "@/components/admin/org-billing-table";
import { centsToUsd } from "@/components/admin/billing-shared";
import { cn, usd } from "@/lib/utils";

const CHIPS: { value: OrgStatusFilter; label: string }[] = [
  { value: "payment_pending", label: "Payment pending" },
  { value: "overdue", label: "Overdue" },
  { value: "at_limit", label: "At limit" },
  { value: "near_limit", label: "Near limit (80%+)" },
  { value: "expiring", label: "Expiring in 7 days" },
  { value: "expired", label: "Expired" },
];

/** Header strip: the month at a glance (docs/22 §9.1) — computed client-side from the billing
 *  cycles the ledger endpoint already returns, scoped to cycles whose period started this
 *  calendar month, rather than a new backend aggregate (the admin API is frozen this phase). */
function MonthSummary() {
  const { data: cycles } = useQuery({ queryKey: ["admin-billing-cycles", "all"], queryFn: () => listBillingCycles() });

  const summary = useMemo(() => {
    const now = new Date();
    const thisMonth = (cycles ?? []).filter((c) => {
      const d = new Date(c.period_start);
      return d.getUTCFullYear() === now.getUTCFullYear() && d.getUTCMonth() === now.getUTCMonth();
    });
    const collected = thisMonth.filter((c) => c.payment_state === "paid").reduce((s, c) => s + c.amount_usd_cents, 0);
    const pending = thisMonth.filter((c) => c.payment_state === "pending").reduce((s, c) => s + c.amount_usd_cents, 0);
    const overdue = thisMonth.filter((c) => c.payment_state === "overdue").length;
    return { collected, pending, overdue };
  }, [cycles]);

  return (
    <p className="text-sm font-medium text-muted">
      <span className="font-bold text-success-text">{usd(centsToUsd(summary.collected))} collected</span> ·{" "}
      <span className="font-bold text-warn-text">{usd(centsToUsd(summary.pending))} pending</span> ·{" "}
      <span className={cn("font-bold", summary.overdue > 0 ? "text-error-text" : "text-muted")}>
        {summary.overdue} overdue
      </span>
    </p>
  );
}

export function BillingTab() {
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState<string>("all");
  const [status, setStatus] = useState<OrgStatusFilter | null>(null);

  const { data: orgs, isLoading } = useQuery({
    queryKey: ["admin-orgs-billing", q, plan, status],
    queryFn: () => listAdminOrgs({ q: q || undefined, plan: plan === "all" ? undefined : plan, status: status ?? undefined }),
  });

  return (
    <div className="space-y-4">
      <MonthSummary />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <Input
            placeholder="Search name or owner email"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-64 pl-8"
          />
        </div>
        <Select value={plan} onValueChange={setPlan}>
          <SelectTrigger className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All plans</SelectItem>
            {GRANTABLE_PLANS.map((p) => (
              <SelectItem key={p} value={p}>
                {p.charAt(0).toUpperCase() + p.slice(1)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {CHIPS.map((chip) => (
          <button
            key={chip.value}
            type="button"
            onClick={() => setStatus((s) => (s === chip.value ? null : chip.value))}
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-bold transition-colors",
              status === chip.value
                ? "border-accent bg-accent-soft text-accent"
                : "border-border bg-surface-2/40 text-muted hover:text-text",
            )}
          >
            {chip.label}
          </button>
        ))}
      </div>

      <OrgBillingTable orgs={orgs ?? []} isLoading={isLoading} />
    </div>
  );
}
