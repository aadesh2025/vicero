"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Bot } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/shared/status-pill";
import { listAgents } from "@/lib/api/agents";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

/**
 * The org's real agents. Deliberately renders only what `GET /v1/agents` returns — name,
 * status, description, draft version, updated_at. The panel used to show per-agent "7d
 * chats" and a resolution rate, but those came from the mock fixture; the agents endpoint
 * carries no traffic figures, and fetching per-agent analytics would be one request per
 * row. The aggregate numbers live in the stat cards directly above.
 */
export function AgentsPanel() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const { data: agents, isLoading } = useQuery({
    queryKey: ["agents", activeOrgId],
    queryFn: listAgents,
    enabled: Boolean(activeOrgId),
  });

  const rows = agents ?? [];

  return (
    <div className="rounded-card border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border p-5">
        <div>
          <h3 className="font-display text-[15px] font-extrabold text-text">Your agents</h3>
          <p className="text-[13px] font-medium text-muted">
            {isLoading ? "Loading…" : `${rows.length} total`}
          </p>
        </div>
        <Link href="/agents" className="inline-flex items-center gap-1 text-sm font-bold text-accent hover:underline">
          View all <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      </div>

      {isLoading ? (
        <ul className="divide-y divide-border" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <li key={i} className="flex items-center gap-4 px-5 py-3.5">
              <Skeleton className="size-9 shrink-0 rounded-lg" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="h-3 w-24" />
              </div>
            </li>
          ))}
        </ul>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center gap-3 px-5 py-10 text-center">
          <span className="grid size-11 place-items-center rounded-[10px] bg-ai-soft text-ai">
            <Bot className="size-5" aria-hidden />
          </span>
          <div>
            <p className="text-sm font-bold text-text">No agents yet</p>
            <p className="text-sm font-medium text-muted">Your agents will appear here once you build one.</p>
          </div>
          <Link href="/agents" className="inline-flex items-center gap-1 text-sm font-bold text-accent hover:underline">
            Create your first agent <ArrowUpRight className="size-3.5" aria-hidden />
          </Link>
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((agent) => (
            <li key={agent.id}>
              <Link
                href={`/agents/${agent.id}`}
                className="group flex items-center gap-4 px-5 py-3.5 transition-colors hover:bg-surface-2"
              >
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-ai-soft font-display text-sm font-extrabold text-ai-text">
                  {agent.name[0]?.toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-bold text-text">{agent.name}</span>
                    <StatusPill status={agent.status} />
                  </div>
                  <p className="mt-0.5 truncate text-xs font-medium text-faint">
                    {agent.description || "No description yet."}
                  </p>
                </div>
                <div className="hidden shrink-0 text-right sm:block">
                  <div className="font-mono text-xs font-semibold text-muted">v{agent.draft_version} draft</div>
                  <div className="text-[11px] font-medium text-faint">updated {relativeTime(agent.updated_at)}</div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
