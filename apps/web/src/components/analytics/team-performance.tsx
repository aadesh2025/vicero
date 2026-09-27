"use client";

import { UserRound } from "lucide-react";
import type { AgentPerformanceBucket } from "@/lib/api/analytics";
import { compact } from "@/lib/utils";

/** ms → a duration a human reads at a glance. Null means "nothing to average yet". */
export function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hours = minutes / 60;
  return hours < 24 ? `${hours.toFixed(1)}h` : `${Math.round(hours / 24)}d`;
}

/**
 * Per-teammate inbox workload — people, not channels.
 *
 * Same visual language as the per-channel breakdown so the Analytics page reads as one
 * thing rather than two competing table styles.
 */
export function TeamPerformance({
  buckets,
  isLoading = false,
}: {
  buckets: AgentPerformanceBucket[] | undefined;
  isLoading?: boolean;
}) {
  const rows = buckets ?? [];
  const busiest = Math.max(...rows.map((b) => b.handoffs), 1);

  if (isLoading) return <p className="px-5 py-4 text-sm font-medium text-muted">Loading…</p>;
  if (rows.length === 0) {
    return (
      <p className="px-5 py-4 text-sm font-medium text-muted">
        No handoffs have been taken over yet — this fills in as teammates handle conversations.
      </p>
    );
  }

  return (
    <table className="w-full min-w-[420px] text-sm">
      <thead>
        <tr className="border-b border-border text-left text-[10.5px] font-extrabold uppercase tracking-[0.08em] text-faint">
          <th scope="col" className="px-5 py-2.5">
            Teammate
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right">
            Handled
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right">
            1st reply
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right">
            Resolved in
          </th>
          <th scope="col" className="px-5 py-2.5 text-right font-medium">
            Closed
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {rows.map((b) => (
          <tr key={b.user_id} className="transition-colors hover:bg-surface-2">
            <th scope="row" className="whitespace-nowrap px-5 py-2.5 text-left font-normal">
              <span className="flex items-center gap-2">
                <span className="grid size-6 shrink-0 place-items-center rounded-full bg-info-soft text-info">
                  <UserRound className="size-3.5" aria-hidden />
                </span>
                <span className="font-bold text-text">{b.name}</span>
              </span>
            </th>
            <td className="px-3 py-2.5 text-right">
              <span className="flex items-center justify-end gap-2">
                <span className="hidden h-1.5 w-16 overflow-hidden rounded bg-surface-3 sm:block">
                  <span
                    className="block h-full rounded bg-info"
                    style={{ width: `${(b.handoffs / busiest) * 100}%` }}
                  />
                </span>
                <span className="text-xs font-bold tabular-nums text-text">{compact(b.handoffs)}</span>
              </span>
            </td>
            <td className="px-3 py-2.5 text-right text-xs font-semibold tabular-nums text-muted">
              {formatDuration(b.avg_first_response_ms)}
            </td>
            <td className="px-3 py-2.5 text-right text-xs font-semibold tabular-nums text-muted">
              {formatDuration(b.avg_resolution_ms)}
            </td>
            <td className="px-5 py-2.5 text-right text-xs font-semibold tabular-nums text-muted">{b.closed_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
