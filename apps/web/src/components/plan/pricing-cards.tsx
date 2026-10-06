"use client";

import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Check, Minus, TriangleAlert } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { getPlans, type Plan, type Pricing } from "@/lib/api/billing";
import { ContactUsButtons } from "@/components/plan/contact-us";
import { CurrencySwitcher } from "@/components/plan/currency-switcher";
import { formatMoney, isCurrency, type Currency } from "@/lib/money";

const STORAGE_KEY = "vicero.pricing.currency";

// localStorage can be blocked or throw (private windows, site-data settings): the page must work
// without it, so every access is guarded and a failure just means "no remembered choice".
function readStoredCurrency(): Currency | null {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return isCurrency(v) ? v : null;
  } catch {
    return null;
  }
}

function storeCurrency(currency: Currency) {
  try {
    window.localStorage.setItem(STORAGE_KEY, currency);
  } catch {
    /* blocked: the choice simply is not remembered */
  }
}

/** The three paid plans from `GET /v1/billing/plans` (docs/22 §3) — no number here is
 *  hardcoded, all of it is rendered straight from the pricing table's own numbers. Used on the
 *  public /pricing page and the in-app /billing/upgrade landing target (docs/22 §10.1). */
export function PricingCards() {
  // `null` = no explicit choice: the server detects the currency from the visitor's country.
  // The remembered choice is read after mount so server and first client render match.
  const [selected, setSelected] = useState<Currency | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reading localStorage must wait for mount
    setSelected(readStoredCurrency());
    setReady(true);
  }, []);

  const { data, isError, isPlaceholderData } = useQuery({
    queryKey: ["billing-plans", selected],
    queryFn: () => getPlans(selected),
    enabled: ready,
    staleTime: Infinity,
    // Keep the previous list on screen while a new currency loads: no skeleton flash, no jump.
    placeholderData: keepPreviousData,
  });

  function choose(currency: Currency) {
    setSelected(currency);
    storeCurrency(currency);
  }

  if (!data && !isError) {
    return (
      <div className="grid gap-5 sm:grid-cols-3" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-[440px] rounded-xl" />
        ))}
      </div>
    );
  }

  if (!data) {
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
      <CurrencySwitcher
        currency={data.currency}
        source={data.currency_source}
        options={data.available_currencies}
        onChange={choose}
        loading={isPlaceholderData}
      />
      <div className={`grid gap-5 transition-opacity sm:grid-cols-3 ${isPlaceholderData ? "opacity-60" : ""}`} aria-busy={isPlaceholderData}>
        {data.plans.map((plan) => (
          <PlanCard key={plan.id} plan={plan} pricing={data} />
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

function PlanCard({ plan, pricing }: { plan: Plan; pricing: Pricing }) {
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
        <span className="font-display text-3xl font-extrabold text-text">{formatMoney(plan.price_minor, pricing.currency)}</span>
        <span className="text-sm text-muted"> / month per workspace, {pricing.tax_note}</span>
      </p>
      <p className="mt-1 text-xs text-faint">
        Extra messages: {formatMoney(plan.extra_message_pack_price_minor, pricing.currency)} per{" "}
        {plan.extra_message_pack_size.toLocaleString()}
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
