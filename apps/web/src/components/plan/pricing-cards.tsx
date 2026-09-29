"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Minus, TriangleAlert } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { getPlans, type Plan } from "@/lib/api/billing";
import { ContactUsButtons } from "@/components/plan/contact-us";
import { usd } from "@/lib/utils";

/** The three paid plans from `GET /v1/billing/plans` (docs/22 §3) — no number here is
 *  hardcoded, all of it is rendered straight from the pricing table's own numbers. Used on the
 *  public /pricing page and the in-app /billing/upgrade landing target (docs/22 §10.1). */
export function PricingCards() {
  const { data, isLoading, isError } = useQuery({ queryKey: ["billing-plans"], queryFn: getPlans, staleTime: Infinity });

  if (isLoading) {
    return (
      <div className="grid gap-5 sm:grid-cols-3" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-[440px] rounded-xl" />
        ))}
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-error/30 bg-error-soft p-5 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-error-text" />
        <div>
          <p className="font-medium text-text">Couldn&apos;t load pricing right now.</p>
          <p className="text-muted">Refresh the page, or reach out below and we&apos;ll send you the numbers directly.</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="grid gap-5 sm:grid-cols-3">
        {data.plans.map((plan) => (
          <PlanCard key={plan.id} plan={plan} />
        ))}
      </div>
      {/* contact_only is true (docs/22 §1, §16): plans are admin-granted, there is no
          self-serve checkout yet (docs/23, deferred). One shared CTA under all three cards. */}
      {data.contact_only && (
        <div className="mt-6 rounded-xl border border-border bg-surface-2/40 p-5 text-center">
          <p className="mb-3 text-sm text-muted">
            Plans aren&apos;t self-serve yet — get in touch and we&apos;ll switch your workspace over by hand.
          </p>
          <div className="flex justify-center">
            <ContactUsButtons />
          </div>
        </div>
      )}
    </>
  );
}

function limitRow(label: string, value: number | null, unit = ""): { label: string; value: string } {
  return { label, value: value === null ? "Unlimited" : `${value.toLocaleString()}${unit}` };
}

function formatBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(0)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)} MB`;
  return `${n} B`;
}

function PlanCard({ plan }: { plan: Plan }) {
  const rows = [
    limitRow("Workspaces", plan.limits.workspaces),
    limitRow("AI agents", plan.limits.agents),
    { label: "Messages / month", value: plan.limits.messages === null ? "Unlimited" : plan.limits.messages.toLocaleString() },
    { label: "Knowledge storage", value: plan.limits.storage_bytes === null ? "Unlimited" : formatBytes(plan.limits.storage_bytes) },
    limitRow("Team members", plan.limits.team_members),
    {
      label: "Channels",
      value: plan.channels === null ? "All supported channels" : plan.channels.join(", "),
    },
  ];

  const features: { label: string; on: boolean }[] = [
    { label: "n8n workflow automations", on: plan.features.n8n },
    { label: "Tool calling", on: plan.features.tool_calling },
    { label: "Webhooks", on: plan.features.webhooks },
    { label: "Advanced analytics", on: plan.features.analytics_advanced },
    { label: "Analytics export", on: plan.features.analytics_export },
    { label: "Remove branding", on: plan.features.remove_branding },
  ];

  const featured = plan.id === "pro";

  return (
    <div
      className={`flex flex-col rounded-xl border p-6 ${featured ? "border-accent bg-accent-soft/20" : "border-border bg-surface"}`}
    >
      <div className="flex items-center gap-2">
        <h3 className="font-display text-lg font-semibold text-text">{plan.name}</h3>
        {featured && <Badge variant="accent">Most popular</Badge>}
      </div>
      <p className="mt-2">
        <span className="font-display text-3xl font-extrabold text-text">{usd(plan.price_usd_month)}</span>
        <span className="text-sm text-muted"> / month per workspace</span>
      </p>
      <p className="mt-1 text-xs text-faint">
        Extra messages: {usd(plan.extra_message_pack_usd)} per {plan.extra_message_pack_size.toLocaleString()}
      </p>

      <ul className="mt-5 space-y-2 text-sm">
        {rows.map((r) => (
          <li key={r.label} className="flex items-center justify-between gap-3">
            <span className="text-muted">{r.label}</span>
            <span className="font-medium text-text">{r.value}</span>
          </li>
        ))}
      </ul>

      <ul className="mt-5 space-y-2 border-t border-border pt-5 text-sm">
        {features.map((f) => (
          <li key={f.label} className="flex items-center gap-2">
            {f.on ? (
              <Check className="size-4 shrink-0 text-success-text" />
            ) : (
              <Minus className="size-4 shrink-0 text-faint" />
            )}
            <span className={f.on ? "text-text" : "text-faint"}>{f.label}</span>
          </li>
        ))}
      </ul>

      <p className="mt-5 text-xs text-faint">{plan.support === "priority" ? "Priority support" : "Email support"}</p>
    </div>
  );
}
