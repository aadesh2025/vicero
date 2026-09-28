"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleDollarSign, MessagesSquare, ShieldCheck, Zap } from "lucide-react";
import { StatCard } from "@/components/dashboard/stat-card";
import { TodayCard } from "@/components/dashboard/today-card";
import { ActivityChart } from "@/components/dashboard/activity-chart";
import { AgentBreakdown } from "@/components/analytics/agent-breakdown";
import { ChannelBreakdown } from "@/components/analytics/channel-breakdown";
import { getByAgent, getOverview, getSeries } from "@/lib/api/analytics";
import { useSession } from "@/lib/store/session";
import { compact, usd } from "@/lib/utils";

export function DashboardStats() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const enabled = Boolean(activeOrgId);

  const { data: overview, isLoading: overviewLoading } = useQuery({
    queryKey: ["dash-overview", activeOrgId],
    queryFn: () => getOverview(),
    enabled,
  });
  // The KPI sparklines' and Today gauge's source — the Activity chart fetches its own
  // timeseries now (ADR-101), keyed by period/metric, so it no longer reuses this query.
  const { data: series } = useQuery({
    queryKey: ["dash-series", activeOrgId],
    queryFn: () => getSeries(),
    enabled,
  });
  const { data: byAgent, isLoading: byAgentLoading } = useQuery({
    queryKey: ["dash-by-agent", activeOrgId],
    queryFn: () => getByAgent(),
    enabled,
  });

  const tokens = (overview?.tokens_prompt ?? 0) + (overview?.tokens_completion ?? 0);
  const cost = (overview?.cost_micros ?? 0) / 1_000_000;
  // $0 across a month of real traffic is almost always the free tier, not a broken query —
  // say which, because a bare $0.00 next to 14k tokens reads as a bug.
  const costHint = tokens > 0 && cost === 0 ? "free tier — no billable usage" : "last 30d";

  const convoSpark = series?.map((d) => d.conversations);
  const tokenSpark = series?.map((d) => d.tokens_prompt + d.tokens_completion);
  const costSpark = series?.map((d) => d.cost_micros / 1_000_000);

  return (
    <>
      {/* Today gauge spans both KPI rows in one column; the four KPIs fill a 2x2 grid beside
          it (docs/20 §9.3.1). Below `lg` everything just stacks in document order. */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <TodayCard series={series} className="lg:row-span-2" />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:col-span-2">
          <StatCard
            label="Conversations"
            value={compact(overview?.conversations ?? 0)}
            icon={MessagesSquare}
            hint="last 30d"
            tone="accent"
            spark={convoSpark}
          />
          <StatCard
            label="Resolution rate"
            value={overview?.conversations ? `${Math.round((overview.resolution_rate ?? 0) * 100)}%` : "—"}
            icon={ShieldCheck}
            hint="no human needed"
            tone="success"
          />
          <StatCard label="Tokens used" value={compact(tokens)} icon={Zap} hint="across providers" tone="ai" spark={tokenSpark} />
          <StatCard
            label="Est. cost"
            value={usd(cost)}
            icon={CircleDollarSign}
            hint={costHint}
            invertDelta
            tone="warn"
            spark={costSpark}
          />
        </div>
      </div>

      {/* Side by side only when there's genuinely room; below xl the table would be
          squeezed to the point of clipping, so it stacks full width instead. */}
      <div className="grid grid-cols-1 gap-6 2xl:grid-cols-[1fr_420px]">
        <ActivityChart />
        <section aria-labelledby="dash-by-channel" className="overflow-hidden rounded-card border border-border bg-surface">
          <div className="border-b border-border p-5">
            <h3 id="dash-by-channel" className="font-display text-[15px] font-extrabold text-text">
              By channel
            </h3>
            <p className="text-[13px] font-medium text-muted">Where your conversations came from.</p>
          </div>
          <div className="overflow-x-auto">
            <ChannelBreakdown buckets={overview?.by_channel} isLoading={overviewLoading} />
          </div>
        </section>
      </div>

      <section aria-labelledby="dash-by-agent" className="overflow-hidden rounded-card border border-border bg-surface">
        <div className="border-b border-border p-5">
          <h3 id="dash-by-agent" className="font-display text-[15px] font-extrabold text-text">
            By agent
          </h3>
          <p className="text-[13px] font-medium text-muted">
            How each agent is doing. Open one for its own analytics.
          </p>
        </div>
        <div className="overflow-x-auto">
          <AgentBreakdown buckets={byAgent} isLoading={byAgentLoading} />
        </div>
      </section>
    </>
  );
}
