"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { isCurrency, type Currency } from "@/lib/money";
import type { Pricing } from "@/lib/api/billing";

const SOURCE_LABEL: Record<Pricing["currency_source"], string> = {
  geo: "detected from your location",
  query: "your choice",
  default: "default",
};

/** USD / EUR / INR picker for the pricing page. The currency is display-only — it never changes
 *  what a workspace is charged — so the line under it says where the current choice came from. */
export function CurrencySwitcher({
  currency,
  source,
  options,
  onChange,
  loading = false,
}: {
  currency: string;
  source: Pricing["currency_source"];
  options: string[];
  onChange: (currency: Currency) => void;
  loading?: boolean;
}) {
  const choices = options.filter(isCurrency);
  return (
    // Fixed height so the page below never moves when the label text changes.
    <div className="mb-5 flex min-h-[3.25rem] flex-wrap items-center justify-between gap-3">
      <div role="radiogroup" aria-label="Currency" className="inline-flex rounded-lg border border-border bg-surface p-0.5">
        {choices.map((c) => (
          <button
            key={c}
            type="button"
            role="radio"
            aria-checked={c === currency}
            onClick={() => c !== currency && onChange(c)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              c === currency ? "bg-accent text-white" : "text-muted hover:text-text"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
      <div className="text-xs text-faint" aria-live="polite">
        {loading ? <Skeleton className="inline-block h-3 w-40 align-middle" /> : `Prices in ${currency} (${SOURCE_LABEL[source]}) — display only`}
      </div>
    </div>
  );
}
