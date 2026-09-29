"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Filter } from "lucide-react";
import { ActivityBars, type ActivityBar } from "@/components/charts/activity-bars";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type ActivityMetric,
  getTimeseries,
} from "@/lib/api/analytics";
import {
  bucketKey,
  defaultRangeFor,
  deltaPct,
  granularityForPeriod,
  isoDate,
  type Granularity,
  type Period,
} from "@/lib/activity-chart-math";
import { useSession } from "@/lib/store/session";
import { compact, usd } from "@/lib/utils";

const METRIC_LABEL: Record<ActivityMetric, string> = {
  conversations: "Conversations",
  messages: "Messages",
  tokens: "Tokens",
  cost: "Cost",
};

const PERIODS: { key: Period; label: string }[] = [
  { key: "daily", label: "Daily" },
  { key: "weekly", label: "Weekly" },
  { key: "monthly", label: "Monthly" },
  { key: "range", label: "Range" },
];

function formatMetric(metric: ActivityMetric, n: number): string {
  return metric === "cost" ? usd(n) : compact(n);
}

/** Browser's own IANA zone — this app has no stored org timezone yet (ADR-100/101). */
function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function barLabel(bucketIso: string, granularity: Granularity, todayKey: string): string {
  const [y, m, d] = bucketIso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  if (bucketIso === todayKey) {
    return granularity === "day" ? "Today" : granularity === "week" ? "This week" : "This month";
  }
  if (granularity === "month") return date.toLocaleDateString("en", { month: "short" });
  return date.toLocaleDateString("en", { month: "short", day: "numeric" });
}

/**
 * Dashboard "Activity" bar chart (docs/20 §9.3.2, ADR-101) — replaces the old line chart on
 * `/dashboard` only (`/analytics` and the agent Analytics tab keep `UsageChart`).
 */
export function ActivityChart() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const enabled = Boolean(activeOrgId);
  const tz = useMemo(() => browserTz(), []);

  const [period, setPeriod] = useState<Period>("daily");
  const [metric, setMetric] = useState<ActivityMetric>("conversations");
  const [customRange, setCustomRange] = useState<{ from: string; to: string } | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [rangeOpen, setRangeOpen] = useState(false);

  const now = useMemo(() => new Date(), []);

  const { from, to, granularity } = useMemo(() => {
    if (period === "range" && customRange) {
      const [fy, fm, fd] = customRange.from.split("-").map(Number);
      const [ty, tm, td] = customRange.to.split("-").map(Number);
      const f = new Date(fy, fm - 1, fd);
      const t = new Date(ty, tm - 1, td);
      return { from: f, to: t, granularity: granularityForPeriod("range", f, t) };
    }
    if (period === "range") {
      const f = new Date(now);
      f.setDate(f.getDate() - 29);
      return { from: f, to: now, granularity: granularityForPeriod("range", f, now) };
    }
    const r = defaultRangeFor(period, now);
    return { ...r, granularity: granularityForPeriod(period, r.from, r.to) };
  }, [period, customRange, now]);

  const fromIso = isoDate(from);
  const toIso = isoDate(to);
  const todayKey = bucketKey(now, granularity);

  const { data, isLoading } = useQuery({
    queryKey: ["dash-activity", activeOrgId, metric, granularity, fromIso, toIso, tz],
    queryFn: () => getTimeseries({ metric, granularity, from: fromIso, to: toIso, tz }),
    enabled,
  });

  const bars: ActivityBar[] = useMemo(
    () =>
      (data?.points ?? []).map((p) => ({
        key: p.bucket_start,
        label: barLabel(p.bucket_start, granularity, todayKey),
        value: p.value,
        ariaLabel: `${barLabel(p.bucket_start, granularity, todayKey)}: ${formatMetric(metric, p.value)} ${METRIC_LABEL[metric].toLowerCase()}`,
      })),
    [data, granularity, todayKey, metric],
  );

  // Default selection = the current bucket (today/this week/this month); falls back to the
  // last bar when the range doesn't include it (e.g. a past custom Range). Reset during render
  // (React's documented "adjusting state when an input changes" pattern) rather than an effect,
  // so switching period/metric/range doesn't flash the *previous* dataset's selection first.
  const datasetId = data ? `${metric}|${granularity}|${fromIso}|${toIso}` : null;
  const [resetFor, setResetFor] = useState<string | null>(null);
  if (datasetId !== null && datasetId !== resetFor) {
    setResetFor(datasetId);
    const idx = bars.findIndex((b) => b.key === todayKey);
    setSelectedIndex(bars.length === 0 ? 0 : idx >= 0 ? idx : bars.length - 1);
  }

  const total = (data?.points ?? []).reduce((sum, p) => sum + p.value, 0);
  const prevTotal = data?.previous_period_total ?? 0;
  const headerDelta = deltaPct(total, prevTotal);

  const clampedIndex = Math.min(selectedIndex, Math.max(0, bars.length - 1));
  const selectedBar = bars[clampedIndex];
  const prevBarValue = clampedIndex > 0 ? bars[clampedIndex - 1].value : undefined;
  const barDelta = selectedBar
    ? prevBarValue === undefined
      ? null
      : (() => {
          const d = deltaPct(selectedBar.value, prevBarValue);
          if (d.isNew) return { label: "New", tone: "new" as const };
          const sign = d.pct >= 0 ? "▲" : "▼";
          const tone: "up" | "down" = d.pct >= 0 ? "up" : "down";
          return { label: `${sign} ${d.pct >= 0 ? "+" : ""}${Math.round(d.pct)}%`, tone };
        })()
    : null;

  function selectPeriod(p: Period) {
    setPeriod(p);
    if (p === "range") {
      setRangeOpen(true);
      if (!customRange) {
        const f = new Date(now);
        f.setDate(f.getDate() - 29);
        setCustomRange({ from: isoDate(f), to: isoDate(now) });
      }
    }
  }

  return (
    <div data-testid="activity-chart" className="rounded-card border border-border bg-surface">
      <div className="flex flex-col gap-2 border-b border-border p-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="font-display text-[14px] font-extrabold text-text">Activity</h3>
          <div className="mt-1 flex flex-wrap items-baseline gap-2">
            <span className="font-display text-xl font-extrabold tabular-nums text-text">
              {isLoading ? "—" : `${formatMetric(metric, total)} ${METRIC_LABEL[metric].toLowerCase()}`}
            </span>
            {!isLoading && (
              <span
                className={
                  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-extrabold " +
                  (headerDelta.isNew
                    ? "bg-accent-soft text-accent"
                    : headerDelta.pct >= 0
                      ? "bg-success-soft text-success-text"
                      : "bg-error-soft text-error-text")
                }
              >
                {headerDelta.isNew ? "New" : `${headerDelta.pct >= 0 ? "▲" : "▼"} ${Math.abs(Math.round(headerDelta.pct))}%`}
              </span>
            )}
            {!isLoading && !headerDelta.isNew && (
              <span className="text-[13px] font-medium text-muted">
                vs {formatMetric(metric, prevTotal)} last period
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex rounded-[10px] bg-surface-3 p-[3px]">
            {PERIODS.map((p) => (
              <button
                key={p.key}
                onClick={() => selectPeriod(p.key)}
                aria-pressed={period === p.key}
                className={
                  "rounded-lg px-3 py-1 text-xs font-bold transition-colors " +
                  (period === p.key ? "bg-surface text-text shadow-card dark:bg-surface-2" : "text-muted hover:text-text")
                }
              >
                {p.label}
              </button>
            ))}
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon-sm" aria-label="Choose metric">
                <Filter />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Metric</DropdownMenuLabel>
              {(Object.keys(METRIC_LABEL) as ActivityMetric[]).map((m) => (
                <DropdownMenuCheckboxItem key={m} checked={metric === m} onCheckedChange={() => setMetric(m)}>
                  {METRIC_LABEL[m]}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {period === "range" && rangeOpen && customRange && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-3 text-sm">
          <label className="flex items-center gap-1.5 font-medium text-muted">
            From
            <input
              type="date"
              value={customRange.from}
              max={customRange.to}
              onChange={(e) => setCustomRange({ ...customRange, from: e.target.value })}
              className="rounded-lg border border-border bg-surface px-2 py-1 text-text"
            />
          </label>
          <label className="flex items-center gap-1.5 font-medium text-muted">
            To
            <input
              type="date"
              value={customRange.to}
              min={customRange.from}
              max={isoDate(now)}
              onChange={(e) => setCustomRange({ ...customRange, to: e.target.value })}
              className="rounded-lg border border-border bg-surface px-2 py-1 text-text"
            />
          </label>
        </div>
      )}

      <div className="px-2 pb-1 pt-2">
        {isLoading ? (
          <Skeleton className="h-80 rounded-lg" />
        ) : (
          <ActivityBars
            bars={bars}
            selectedIndex={clampedIndex}
            onSelect={setSelectedIndex}
            format={(n) => formatMetric(metric, n)}
            selectedDelta={barDelta}
            height={320}
            ariaLabel={`${METRIC_LABEL[metric]} per ${granularity}, ${fromIso} to ${toIso}`}
          />
        )}
      </div>
    </div>
  );
}
