"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { Gauge } from "@/components/charts/gauge";
import { StatusPill } from "@/components/shared/status-pill";
import { Skeleton } from "@/components/ui/skeleton";
import { getToday } from "@/lib/api/analytics";
import type { DayPoint } from "@/lib/api/analytics";
import { useSession } from "@/lib/store/session";
import { cn } from "@/lib/utils";

/** Local midnight for whatever "now" is passed — the browser's own timezone, since this app
 *  has no stored org timezone yet (ADR-100). Recomputed on every fetch, not memoized, so the
 *  window rolls over on its own once a real midnight passes. */
function localDayStart(now: Date): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** The busiest single day in the series, floored at 10 so a quiet org's gauge doesn't look
 *  maxed-out after three chats. Exported for the vitest coverage. */
export function computePeak(series: Pick<DayPoint, "conversations">[] | undefined): number {
  return Math.max(10, ...(series ?? []).map((d) => d.conversations));
}

/**
 * The dashboard "Today" gauge (docs/20 §9.3.1 — the reference's "Time Off 10 OUT OF 20" card).
 * `series` is the same 30-day `getSeries()` result the Activity chart already fetches, reused
 * here for the peak so this card costs exactly one extra request (`/v1/analytics/today`).
 */
export function TodayCard({ series, className }: { series: DayPoint[] | undefined; className?: string }) {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const enabled = Boolean(activeOrgId);
  const now = new Date();

  const { data, isLoading } = useQuery({
    queryKey: ["dash-today", activeOrgId, now.toDateString()],
    queryFn: () => getToday(localDayStart(new Date()), new Date()),
    enabled,
    refetchInterval: 60_000,
  });

  const peak = computePeak(series);
  const total = data?.conversations ?? 0;
  const dateLabel = now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  return (
    <div
      data-testid="today-card"
      className={cn("flex flex-col rounded-card border border-border bg-surface p-5 shadow-card", className)}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-display text-[15px] font-extrabold text-text">Today</h3>
          <p className="text-[13px] font-medium text-muted">{dateLabel}</p>
        </div>
        {/* /conversations has no date filter to link into yet (it's a per-agent chat console,
            not the filterable browser docs/20 §10.8 pictures — see R5's notes) — this goes to
            the plain list rather than a query param the page would silently ignore. */}
        <Link
          href="/conversations"
          className="inline-flex shrink-0 items-center gap-1 text-sm font-bold text-accent hover:underline"
        >
          See all <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      </div>

      {isLoading ? (
        <Skeleton className="mx-auto mt-6 h-[110px] w-full max-w-[220px] rounded-t-full" />
      ) : (
        <div className="relative mx-auto mt-4 w-full max-w-[220px]">
          <Gauge
            value={total}
            max={peak}
            ariaLabel={`${total} conversations today, out of a busiest day of ${peak} in the last 30 days`}
            className="w-full"
          />
          <div className="pointer-events-none absolute inset-x-0 bottom-1 flex flex-col items-center">
            <span className="font-display text-[28px] font-extrabold leading-none tabular-nums text-text">{total}</span>
            <span className="mt-1 text-[10.5px] font-extrabold uppercase tracking-[0.08em] text-faint">
              out of {peak}
            </span>
          </div>
        </div>
      )}
      {!isLoading && total === 0 && (
        <p className="mt-2 text-center text-xs font-semibold text-faint">No chats yet today.</p>
      )}

      <div className="mt-5 flex flex-col gap-3 border-t border-border pt-4">
        <TodayRow status="resolved" label="Resolved by AI" count={data?.resolved_by_ai} loading={isLoading} />
        <TodayRow status="handoff" label="Handed to human" count={data?.handed_to_human} loading={isLoading} />
        <TodayRow status="unanswered" label="Unanswered" count={data?.unanswered} loading={isLoading} />
      </div>
    </div>
  );
}

function TodayRow({
  status,
  label,
  count,
  loading,
}: {
  status: "resolved" | "handoff" | "unanswered";
  label: string;
  count: number | undefined;
  loading: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <StatusPill status={status}>{label}</StatusPill>
      {loading ? (
        <Skeleton className="h-4 w-6" />
      ) : (
        <span className="text-sm font-bold tabular-nums text-text">{count ?? 0}</span>
      )}
    </div>
  );
}
