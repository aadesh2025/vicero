"use client";

import { ChannelIcon, ChannelText } from "@/components/shared/channel";
import { channelTone } from "@/lib/channel-meta";
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

/** Per-channel performance, shared by the Dashboard summary and the Analytics page. Bars and
 *  icons use each channel's own colour (docs/20 §4.5, §5) — never a generic series colour. */
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
    return <p className="px-5 py-4 text-sm font-medium text-muted">Loading…</p>;
  }
  if (rows.length === 0) {
    return (
      <p className="px-5 py-4 text-sm font-medium text-muted">
        No channels connected yet — connect one from an agent’s Channels tab.
      </p>
    );
  }

  return (
    <table className="w-full min-w-[340px] text-sm">
      <thead>
        <tr className="border-b border-border text-left text-[10.5px] font-extrabold uppercase tracking-[0.08em] text-faint">
          <th scope="col" className="px-5 py-2.5">
            Channel
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right">
            Convos
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right">
            Resolved
          </th>
          <th scope="col" className="px-5 py-2.5 text-right">
            Cost
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {rows.map((b) => {
          const idle = b.conversations === 0;
          const tone = channelTone(b.channel);
          return (
            <tr key={b.channel} className="transition-colors hover:bg-surface-2">
              <th scope="row" className="whitespace-nowrap px-5 py-2.5 text-left font-normal">
                <span className="flex items-center gap-2">
                  <ChannelIcon channel={b.channel} size="sm" />
                  <ChannelText channel={b.channel} className={idle ? "opacity-60" : ""} />
                  {idle && (
                    <span className="rounded-md bg-surface-3 px-1.5 py-px text-[10px] font-bold text-faint">
                      no traffic
                    </span>
                  )}
                </span>
              </th>
              <td className="px-3 py-2.5 text-right">
                <span className="flex items-center justify-end gap-2">
                  {/* Proportional bar, so relative volume reads at a glance. */}
                  <span className="hidden h-1.5 w-16 overflow-hidden rounded bg-surface-3 sm:block">
                    <span
                      className="block h-full rounded"
                      style={{ width: `${(b.conversations / busiest) * 100}%`, backgroundColor: tone.dot }}
                    />
                  </span>
                  <span className="text-xs font-bold tabular-nums text-text">{compact(b.conversations)}</span>
                </span>
              </td>
              <td className="px-3 py-2.5 text-right text-xs font-semibold tabular-nums text-muted">
                {formatRate(b.resolution_rate, b.conversations)}
              </td>
              <td className="px-5 py-2.5 text-right text-xs font-semibold tabular-nums text-muted">
                {usd(b.cost_micros / 1_000_000)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
