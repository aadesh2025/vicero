"use client";

import Link from "next/link";
import { ArrowUpRight, Bot } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRate } from "@/components/analytics/channel-breakdown";
import type { AgentBucket } from "@/lib/api/analytics";
import { compact, relativeTime, usd } from "@/lib/utils";

/**
 * Per-agent traffic, shared by the Dashboard and the Analytics page.
 *
 * Every row links to that agent's own Analytics tab, which is the same numbers filtered to
 * one `agent_id` — so the combined view is the entry point to the per-agent one rather than
 * a separate report that happens to look similar.
 *
 * Agents with no traffic are shown rather than filtered out: a published agent with zero
 * conversations almost always means the embed was never installed, which is the single most
 * useful thing this table can tell an operator.
 */
/** The agent's name cell — a link to its analytics, unless the agent is gone. */
function NameCell({
  deleted,
  agentId,
  children,
}: {
  deleted: boolean;
  agentId: string;
  children: React.ReactNode;
}) {
  if (deleted) {
    return <span className="flex items-center gap-2">{children}</span>;
  }
  return (
    <Link href={`/agents/${agentId}?tab=analytics`} className="flex items-center gap-2 hover:text-accent">
      {children}
    </Link>
  );
}

export function AgentBreakdown({
  buckets,
  isLoading = false,
}: {
  buckets: AgentBucket[] | undefined;
  isLoading?: boolean;
}) {
  const rows = buckets ?? [];
  const busiest = Math.max(...rows.map((b) => b.conversations), 1);

  if (isLoading) {
    return (
      <div className="space-y-2 px-5 py-4" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 px-5 py-10 text-center">
        <span className="grid size-11 place-items-center rounded-lg border border-border bg-surface-2 text-muted">
          <Bot className="size-5" />
        </span>
        <div>
          <p className="text-sm font-medium text-text">No agents yet</p>
          <p className="text-sm text-muted">Per-agent numbers appear here once you build one.</p>
        </div>
      </div>
    );
  }

  return (
    <table className="w-full min-w-[520px] text-sm">
      <thead>
        <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-faint">
          <th scope="col" className="px-5 py-2.5 font-medium">
            Agent
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right font-medium">
            Convos
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right font-medium">
            Messages
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right font-medium">
            Resolved
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right font-medium">
            Tokens
          </th>
          <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-right font-medium">
            Cost
          </th>
          <th scope="col" className="whitespace-nowrap px-5 py-2.5 text-right font-medium">
            Last active
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {rows.map((b) => {
          const idle = b.conversations === 0;
          return (
            <tr key={b.agent_id} className="group transition-colors hover:bg-surface-2/40">
              <th scope="row" className="whitespace-nowrap px-5 py-2.5 text-left font-normal">
                {/* A deleted agent keeps its history here so the rows sum to the headline
                    number, but it has no builder page left to link to. */}
                <NameCell deleted={b.deleted} agentId={b.agent_id}>
                  <span className="grid size-6 shrink-0 place-items-center rounded-md border border-border bg-surface-2 font-display text-[11px] font-semibold text-muted">
                    {b.name[0]?.toUpperCase()}
                  </span>
                  <span className={idle || b.deleted ? "text-muted" : "text-text"}>{b.name}</span>
                  {b.deleted ? (
                    <span className="rounded border border-border px-1 py-px text-[10px] text-faint">
                      deleted
                    </span>
                  ) : (
                    idle && (
                      <span className="rounded border border-border px-1 py-px text-[10px] text-faint">
                        no traffic
                      </span>
                    )
                  )}
                  {!b.deleted && (
                    <ArrowUpRight
                      className="size-3 opacity-0 transition-opacity group-hover:opacity-100"
                      aria-hidden
                    />
                  )}
                </NameCell>
              </th>
              <td className="px-3 py-2.5 text-right">
                <span className="flex items-center justify-end gap-2">
                  <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-surface-3 sm:block">
                    <span
                      className="block h-full rounded-full bg-gradient-to-r from-accent to-accent-2"
                      style={{ width: `${(b.conversations / busiest) * 100}%` }}
                    />
                  </span>
                  <span className="font-mono text-xs text-text">{compact(b.conversations)}</span>
                </span>
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-xs text-muted">{compact(b.messages)}</td>
              <td className="px-3 py-2.5 text-right font-mono text-xs text-muted">
                {formatRate(b.resolution_rate, b.conversations)}
              </td>
              {/* The split is what makes the total checkable: these are the provider's own
                  per-reply `usage` numbers summed, not an estimate from the text. */}
              <td
                className="px-3 py-2.5 text-right font-mono text-xs text-muted"
                title={`${b.tokens_prompt.toLocaleString()} prompt + ${b.tokens_completion.toLocaleString()} completion`}
              >
                {compact(b.tokens_prompt + b.tokens_completion)}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-xs text-muted">
                {usd(b.cost_micros / 1_000_000)}
              </td>
              <td className="whitespace-nowrap px-5 py-2.5 text-right text-xs text-faint">
                {b.last_active_at ? relativeTime(b.last_active_at) : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
