"use client";

import { useMemo, useState } from "react";
import type { DayPoint } from "@/lib/api/analytics";
import { LineAreaChart } from "@/components/charts/line-area-chart";
import { Skeleton } from "@/components/ui/skeleton";
import { compact, usd } from "@/lib/utils";

type Metric = "conversations" | "messages" | "tokens" | "cost";

const metricMeta: Record<Metric, { label: string; value: (d: DayPoint) => number; format: (n: number) => string }> = {
  conversations: { label: "Conversations", value: (d) => d.conversations, format: (n) => compact(n) },
  messages: { label: "Messages", value: (d) => d.messages, format: (n) => compact(n) },
  tokens: { label: "Tokens", value: (d) => d.tokens_prompt + d.tokens_completion, format: (n) => compact(n) },
  cost: { label: "Cost", value: (d) => d.cost_micros / 1_000_000, format: (n) => usd(n) },
};

/** Parse `YYYY-MM-DD` as a *local* date.
 *
 * `new Date("2026-08-09")` is parsed as UTC midnight, which renders as the 8th anywhere west
 * of Greenwich — so the chart's labels disagreed with the day the data belongs to.
 */
function parseDay(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}

export interface UsageChartProps {
  data: DayPoint[] | undefined;
  isLoading?: boolean;
  title?: string;
  /** Overrides the range description under the title. */
  subtitle?: string;
}

/** Dashboard/Analytics activity card: one metric at a time, from the shared LineAreaChart. */
export function UsageChart({ data, isLoading, title = "Activity", subtitle }: UsageChartProps) {
  const [metric, setMetric] = useState<Metric>("conversations");
  const points = useMemo(() => data ?? [], [data]);

  if (isLoading) return <Skeleton className="h-[340px] rounded-card" />;

  if (points.length === 0) {
    return (
      <div className="rounded-card border border-border bg-surface p-5">
        <h3 className="font-display text-[15px] font-extrabold text-text">{title}</h3>
        <div className="mt-4 flex h-[220px] items-center justify-center rounded-lg text-sm font-semibold text-faint">
          No usage in this period yet.
        </div>
      </div>
    );
  }

  const meta = metricMeta[metric];
  const first = parseDay(points[0].date);
  const last = parseDay(points[points.length - 1].date);
  const fmt = (d: Date) => d.toLocaleDateString("en", { month: "short", day: "numeric" });
  const rangeLabel = subtitle ?? `${fmt(first)} – ${fmt(last)} · ${points.length} days`;
  const values = points.map(meta.value);
  const peak = Math.max(...values);

  return (
    <div className="rounded-card border border-border bg-surface">
      <div className="flex flex-col gap-3 border-b border-border p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="font-display text-[15px] font-extrabold text-text">{title}</h3>
          <p className="text-[13px] font-medium text-muted">{rangeLabel}</p>
        </div>
        <div className="flex rounded-[10px] bg-surface-3 p-[3px]">
          {(Object.keys(metricMeta) as Metric[]).map((m) => (
            <button
              key={m}
              onClick={() => setMetric(m)}
              aria-pressed={metric === m}
              className={
                "rounded-lg px-3 py-1 text-xs font-bold capitalize transition-colors " +
                (metric === m ? "bg-surface text-text shadow-card dark:bg-surface-2" : "text-muted hover:text-text")
              }
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      <div className="px-2 pb-2 pt-4">
        <LineAreaChart
          ariaLabel={`${meta.label} per day, ${rangeLabel}. Peak ${meta.format(peak)}.`}
          xLabels={points.map((p) => fmt(parseDay(p.date)))}
          tooltipHeadings={points.map((p) => fmt(parseDay(p.date)))}
          series={[{ key: metric, label: meta.label, color: "accent", values }]}
          format={meta.format}
        />
      </div>
    </div>
  );
}
