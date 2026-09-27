"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleDollarSign, Download, MessagesSquare, ShieldCheck, Timer, Users, Zap } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { StatCard } from "@/components/dashboard/stat-card";
import { UsageChart } from "@/components/dashboard/usage-chart";
import { AgentBreakdown } from "@/components/analytics/agent-breakdown";
import { BarList } from "@/components/analytics/bar-list";
import { ChannelBreakdown } from "@/components/analytics/channel-breakdown";
import { TeamPerformance } from "@/components/analytics/team-performance";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { channelMeta, channelTone } from "@/lib/channel-meta";
import {
  getAgentPerformance,
  getByAgent,
  getLatency,
  getOverview,
  getSeries,
  getTopQuestions,
  getUnanswered,
  getUsage,
} from "@/lib/api/analytics";
import { API_BASE } from "@/lib/api/config";
import { getAccessToken, getActiveOrgId } from "@/lib/api/tokens";
import { useSession } from "@/lib/store/session";
import { compact, usd } from "@/lib/utils";

async function downloadCsv(type: "usage" | "conversations" | "channels") {
  const token = getAccessToken();
  const org = getActiveOrgId();
  const res = await fetch(`${API_BASE}/v1/analytics/export?type=${type}`, {
    headers: { Authorization: `Bearer ${token}`, "X-Org-Id": org ?? "" },
  });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${type}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function AnalyticsPage() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const enabled = Boolean(activeOrgId);

  const { data: overview, isLoading: overviewLoading } = useQuery({
    queryKey: ["an-overview", activeOrgId],
    queryFn: () => getOverview(),
    enabled,
  });
  const { data: latency } = useQuery({ queryKey: ["an-latency", activeOrgId], queryFn: () => getLatency(), enabled });
  const { data: series, isLoading: seriesLoading } = useQuery({
    queryKey: ["an-series", activeOrgId],
    queryFn: () => getSeries(),
    enabled,
  });
  const { data: byAgent, isLoading: byAgentLoading } = useQuery({
    queryKey: ["an-by-agent", activeOrgId],
    queryFn: () => getByAgent(),
    enabled,
  });
  const { data: usageProvider } = useQuery({
    queryKey: ["an-usage-provider", activeOrgId],
    queryFn: () => getUsage({ group_by: "provider" }),
    enabled,
  });
  const { data: usageChannel } = useQuery({
    queryKey: ["an-usage-channel", activeOrgId],
    queryFn: () => getUsage({ group_by: "channel" }),
    enabled,
  });
  const { data: team, isLoading: teamLoading } = useQuery({
    queryKey: ["an-team", activeOrgId],
    queryFn: () => getAgentPerformance(),
    enabled,
  });
  const { data: top } = useQuery({ queryKey: ["an-top", activeOrgId], queryFn: () => getTopQuestions(), enabled });
  const { data: unanswered } = useQuery({ queryKey: ["an-un", activeOrgId], queryFn: () => getUnanswered(), enabled });

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader title="Analytics" description="How your agents are performing across channels.">
        <Button variant="secondary" size="default" onClick={() => downloadCsv("usage")}>
          <Download /> Export usage
        </Button>
        <Button variant="secondary" size="default" onClick={() => downloadCsv("channels")}>
          <Download /> Export channels
        </Button>
        <Button variant="secondary" size="default" onClick={() => downloadCsv("conversations")}>
          <Download /> Export conversations
        </Button>
      </PageHeader>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Conversations" value={compact(overview?.conversations ?? 0)} icon={MessagesSquare} hint="last 30d" tone="accent" />
        <StatCard
          label="Resolution rate"
          // An em dash, not 0%: nothing has happened yet is not a total failure to resolve.
          value={overview?.conversations ? `${Math.round((overview.resolution_rate ?? 0) * 100)}%` : "—"}
          icon={ShieldCheck}
          hint="no human needed"
          tone="success"
        />
        <StatCard label="p50 latency" value={`${latency?.p50_ms ?? 0} ms`} icon={Timer} hint="assistant replies" invertDelta tone="warn" />
        <StatCard
          label="Est. cost"
          value={usd((overview?.cost_micros ?? 0) / 1_000_000)}
          icon={CircleDollarSign}
          hint="last 30d"
          invertDelta
          tone="warn"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Messages" value={compact(overview?.messages ?? 0)} icon={MessagesSquare} hint="user + assistant" tone="accent" />
        <StatCard label="Unique users" value={compact(overview?.users ?? 0)} icon={Users} hint="across channels" tone="accent" />
        <StatCard
          label="Tokens"
          value={compact((overview?.tokens_prompt ?? 0) + (overview?.tokens_completion ?? 0))}
          icon={Zap}
          hint="prompt + completion"
          tone="ai"
        />
      </div>

      <UsageChart data={series} isLoading={seriesLoading} />

      <section
        aria-labelledby="an-by-agent"
        className="overflow-hidden rounded-card border border-border bg-surface"
      >
        <div className="border-b border-border p-5">
          <h3 id="an-by-agent" className="font-display text-[15px] font-extrabold text-text">
            By agent
          </h3>
          <p className="text-[13px] font-medium text-muted">
            Every agent in this workspace. Open one for its own analytics.
          </p>
        </div>
        <div className="overflow-x-auto">
          <AgentBreakdown buckets={byAgent} isLoading={byAgentLoading} />
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section
          aria-labelledby="an-by-channel"
          className="overflow-hidden rounded-card border border-border bg-surface"
        >
          <div className="border-b border-border p-5">
            <h3 id="an-by-channel" className="font-display text-[15px] font-extrabold text-text">
              By channel
            </h3>
            <p className="text-[13px] font-medium text-muted">
              Every connected channel, including ones still waiting on their first message.
            </p>
          </div>
          <div className="overflow-x-auto">
            <ChannelBreakdown buckets={overview?.by_channel} isLoading={overviewLoading} />
          </div>
        </section>

        <section className="rounded-card border border-border bg-surface">
          <div className="border-b border-border p-5">
            <h3 className="font-display text-[15px] font-extrabold text-text">Tokens by channel</h3>
          </div>
          <div className="p-5">
            {(usageChannel ?? []).length === 0 ? (
              <p className="text-[13px] font-medium text-muted">No usage yet.</p>
            ) : (
              <BarList
                items={(usageChannel ?? []).map((b) => ({
                  id: b.key,
                  label: channelMeta(b.key).label,
                  value: b.tokens_prompt + b.tokens_completion,
                  color: channelTone(b.key).dot,
                }))}
                format={(n) => compact(n)}
              />
            )}
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-card border border-border bg-surface">
          <div className="border-b border-border p-5">
            <h3 className="font-display text-[15px] font-extrabold text-text">Tokens by agent</h3>
            <p className="text-[13px] font-medium text-muted">
              Prompt + completion, as reported by the provider for each reply.
            </p>
          </div>
          <div className="p-5">
            {(byAgent ?? []).every((b) => b.tokens_prompt + b.tokens_completion === 0) ? (
              <p className="text-[13px] font-medium text-muted">No usage yet.</p>
            ) : (
              <BarList
                items={(byAgent ?? [])
                  .map((b) => ({
                    id: b.agent_id,
                    label: b.deleted ? `${b.name} (deleted)` : b.name,
                    value: b.tokens_prompt + b.tokens_completion,
                  }))
                  // Agents that never ran would otherwise render a row of zero-width bars.
                  .filter((i) => i.value > 0)
                  .sort((a, b) => b.value - a.value)}
                format={(n) => compact(n)}
              />
            )}
          </div>
        </section>

        <section className="rounded-card border border-border bg-surface">
          <div className="border-b border-border p-5">
            <h3 className="font-display text-[15px] font-extrabold text-text">Tokens by provider</h3>
          </div>
          <div className="p-5">
            {(usageProvider ?? []).length === 0 ? (
              <p className="text-[13px] font-medium text-muted">No usage yet.</p>
            ) : (
              <BarList
                items={(usageProvider ?? []).map((b) => ({
                  id: b.key,
                  label: b.key,
                  value: b.tokens_prompt + b.tokens_completion,
                }))}
                format={(n) => compact(n)}
              />
            )}
          </div>
        </section>

        <section className="rounded-card border border-border bg-surface">
          <div className="border-b border-border p-5">
            <h3 className="font-display text-[15px] font-extrabold text-text">Latency</h3>
          </div>
          <div className="grid grid-cols-3 gap-4 p-5 text-center">
            <div>
              <div className="font-display text-2xl font-semibold text-text">{latency?.p50_ms ?? 0}</div>
              <div className="text-xs text-faint">p50 ms</div>
            </div>
            <div>
              <div className="font-display text-2xl font-semibold text-text">{latency?.p95_ms ?? 0}</div>
              <div className="text-xs text-faint">p95 ms</div>
            </div>
            <div>
              <div className="font-display text-2xl font-semibold text-text">{Math.round(latency?.avg_ms ?? 0)}</div>
              <div className="text-xs text-faint">avg ms</div>
            </div>
          </div>
        </section>
      </div>

      <section
        aria-labelledby="an-team"
        className="overflow-hidden rounded-card border border-border bg-surface"
      >
        <div className="border-b border-border p-5">
          <h3 id="an-team" className="font-display text-[15px] font-extrabold text-text">
            Team performance
          </h3>
          <p className="text-[13px] font-medium text-muted">
            How your teammates are handling conversations the bot escalated.
          </p>
        </div>
        <div className="overflow-x-auto">
          <TeamPerformance buckets={team} isLoading={teamLoading} />
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-card border border-border bg-surface">
          <div className="border-b border-border p-5">
            <h3 className="font-display text-[15px] font-extrabold text-text">Top questions</h3>
            <p className="text-[13px] font-medium text-muted">Most asked by your users.</p>
          </div>
          <ul className="divide-y divide-border">
            {(top ?? []).length === 0 && <li className="px-5 py-4 text-sm text-muted">No data yet.</li>}
            {(top ?? []).map((q, i) => (
              <li key={i} className="flex items-center gap-3 px-5 py-3">
                <span className="font-mono text-xs text-faint">{String(i + 1).padStart(2, "0")}</span>
                <span className="flex-1 truncate text-sm text-text">{q.question}</span>
                <span className="font-mono text-sm text-muted">{q.count}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded-card border border-border bg-surface">
          <div className="border-b border-border p-5">
            <h3 className="font-display text-[15px] font-extrabold text-text">Escalated questions</h3>
            <p className="text-[13px] font-medium text-muted">Questions that needed a human — gaps to fill.</p>
          </div>
          <ul className="divide-y divide-border">
            {(unanswered ?? []).length === 0 && <li className="px-5 py-4 text-sm text-muted">None — nice.</li>}
            {(unanswered ?? []).map((q, i) => (
              <li key={i} className="flex items-center gap-3 px-5 py-3">
                <Badge variant="warn">gap</Badge>
                <span className="flex-1 truncate text-sm text-text">{q.question}</span>
                <span className="font-mono text-sm text-muted">{q.count}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
