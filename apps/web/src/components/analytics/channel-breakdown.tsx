"use client";

import { channelMeta } from "@/lib/channel-meta";
import type { ChannelBucket } from "@/lib/api/analytics";
import { compact, usd } from "@/lib/utils";

/**
 * A rate is only meaningful once something happened. A channel connected yesterday with no
 * conversations yet would read as "0% resolved" — which looks like failure rather than
 * silence — so it gets an em dash instead.
 */
export function formatRate(rate: number, conversations: number): string {
  if (!conversations) return "—";
  return `${Math.round(rate * 100)}%`;
}

/** Per-channel performance, shared by the Dashboard summary and the Analytics page. */
export function ChannelBreakdown({
  buckets,
  isLoading = false,
}: {
  buckets: ChannelBucket[] | undefined;
  isLoading?: boolean;
}) {
  const rows = buckets ?? [];
  const busiest = Math.max(...rows.map((b) => b.conversations), 1);

  if (isLoading) {
    return <p className="px-5 py-4 text-sm text-muted">Loading…</p>;
  }
  if (rows.length === 0) {
    return (
      <p className="px-5 py-4 text-sm text-muted">
        No channels connected yet — connect one from an agent’s Channels tab.
      </p>
    );
  }

  return (
    <table className="w-full min-w-[340px] text-sm">
      <thead>
        <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-faint">
          <th scope="col" className="px-5 py-2.5 font-medium">
            Channel
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right font-medium">
            Convos
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right font-medium">
            Resolved
          </th>
          <th scope="col" className="px-5 py-2.5 text-right font-medium">
            Cost
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {rows.map((b) => {
          const { label, Icon } = channelMeta(b.channel);
          const idle = b.conversations === 0;
          return (
            <tr key={b.channel} className="transition-colors hover:bg-surface-2/40">
              <th scope="row" className="whitespace-nowrap px-5 py-2.5 text-left font-normal">
                <span className="flex items-center gap-2">
                  <span className="grid size-6 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                    <Icon className="size-3.5" aria-hidden />
                  </span>
                  <span className={idle ? "text-muted" : "text-text"}>{label}</span>
                  {idle && (
                    <span className="rounded border border-border px-1 py-px text-[10px] text-faint">
                      no traffic
                    </span>
                  )}
                </span>
              </th>
              <td className="px-3 py-2.5 text-right">
                <span className="flex items-center justify-end gap-2">
                  {/* Proportional bar, so relative volume reads at a glance. */}
                  <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-surface-3 sm:block">
                    <span
                      className="block h-full rounded-full bg-gradient-to-r from-accent to-accent-2"
                      style={{ width: `${(b.conversations / busiest) * 100}%` }}
                    />
                  </span>
                  <span className="font-mono text-xs text-text">{compact(b.conversations)}</span>
                </span>
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-xs text-muted">
                {formatRate(b.resolution_rate, b.conversations)}
              </td>
              <td className="px-5 py-2.5 text-right font-mono text-xs text-muted">
                {usd(b.cost_micros / 1_000_000)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
