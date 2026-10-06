"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { GRANTABLE_PLANS, getBillingTotals, listAdminOrgs, type OrgStatusFilter } from "@/lib/api/admin";
import { OrgBillingTable } from "@/components/admin/org-billing-table";
import { formatMoney } from "@/lib/money";
import { cn } from "@/lib/utils";

const CHIPS: { value: OrgStatusFilter; label: string }[] = [
  { value: "payment_pending", label: "Payment pending" },
  { value: "overdue", label: "Overdue" },
  { value: "at_limit", label: "At limit" },
  { value: "near_limit", label: "Near limit (80%+)" },
  { value: "expiring", label: "Expiring in 7 days" },
  { value: "expired", label: "Expired" },
];

/** Header strip: the month at a glance (docs/22 §9.1). One segment per currency from
 *  `GET /v1/admin/billing/totals` — amounts in different currencies are never added together. */
function MonthSummary() {
  const { data: totals } = useQuery({ queryKey: ["admin-billing-totals"], queryFn: getBillingTotals });

  if (!totals || totals.length === 0) {
    return <p className="text-sm font-medium text-muted">No billing cycles this month.</p>;
  }

  return (
    <div className="space-y-1">
      {totals.map((t) => (
        <p key={t.currency} className="text-sm font-medium text-muted">
          <span className="font-bold text-text">{t.currency}</span> ·{" "}
          <span className="font-bold text-success-text">{formatMoney(t.collected_minor, t.currency)} collected</span> ·{" "}
          <span className="font-bold text-warn-text">{formatMoney(t.pending_minor, t.currency)} pending</span> ·{" "}
          <span className={cn("font-bold", t.overdue_count > 0 ? "text-error-text" : "text-muted")}>
            {t.overdue_count} overdue
          </span>
        </p>
      ))}
    </div>
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
