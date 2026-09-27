"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, MessagesSquare } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { ChannelBadge } from "@/components/shared/channel";
import { StatusPill } from "@/components/shared/status-pill";
import { listConversations } from "@/lib/api/conversations";
import { listAgents } from "@/lib/api/agents";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

const MAX_ROWS = 6;

/**
 * The org's most recent conversations, newest first.
 *
 * Sourced from `GET /v1/conversations` rather than the inbox: the inbox endpoint is the
 * *handoff queue* (`Conversation.id.in_(handoff_ids)`), so an org whose bot is answering
 * everything without escalating would show an empty panel. It also requires
 * `inbox:handle`, which the `viewer` role lacks — a viewer would get a 403 here.
 */
export function ConversationsPanel() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const enabled = Boolean(activeOrgId);

  const { data: conversations, isLoading } = useQuery({
    queryKey: ["conversations", activeOrgId],
    queryFn: () => listConversations(),
    enabled,
  });
  // Conversations carry an agent_id, not a name. Same query key as the agents panel, so
  // React Query serves both from one fetch.
  const { data: agents } = useQuery({
    queryKey: ["agents", activeOrgId],
    queryFn: listAgents,
    enabled,
  });

  const agentName = new Map((agents ?? []).map((a) => [a.id, a.name]));
  const rows = (conversations ?? []).slice(0, MAX_ROWS);

  return (
    <div className="flex h-full flex-col rounded-card border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border p-5">
        <div>
          <h3 className="font-display text-[15px] font-extrabold text-text">Recent conversations</h3>
          <p className="text-[13px] font-medium text-muted">Live across every channel</p>
        </div>
        <Link href="/conversations" className="inline-flex items-center gap-1 text-sm font-bold text-accent hover:underline">
          View all <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      </div>

      {isLoading ? (
        <ul className="flex-1 divide-y divide-border" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className="space-y-2 px-5 py-3.5">
              <Skeleton className="h-3 w-28" />
              <Skeleton className="h-3.5 w-full" />
              <Skeleton className="h-3 w-36" />
            </li>
          ))}
        </ul>
      ) : rows.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-5 py-10 text-center">
          <span className="grid size-11 place-items-center rounded-[10px] bg-info-soft text-info">
            <MessagesSquare className="size-5" aria-hidden />
          </span>
          <div>
            <p className="text-sm font-bold text-text">No conversations yet</p>
            <p className="text-sm font-medium text-muted">
              Chats will appear here once someone messages one of your agents.
            </p>
          </div>
        </div>
      ) : (
        <ul className="flex-1 divide-y divide-border">
          {rows.map((c) => {
            // A handed-off thread belongs to the operator inbox; everything else only
            // exists in the conversations browser.
            const href = c.status === "handoff" ? `/inbox/${c.id}` : "/conversations";
            return (
              <li key={c.id}>
                <Link href={href} className="flex flex-col gap-1.5 px-5 py-3.5 transition-colors hover:bg-surface-2">
                  <div className="flex items-center gap-2">
                    <ChannelBadge channel={c.channel} size="sm" />
                    <StatusPill status={c.status} className="ml-auto shrink-0" />
                  </div>
                  <p className="line-clamp-1 text-sm font-semibold text-text">{c.title || "Untitled conversation"}</p>
                  <div className="flex items-center gap-2 text-[11px] font-medium text-faint">
                    <span className="truncate">{agentName.get(c.agent_id) ?? "Unknown agent"}</span>
                    <span className="text-border-strong">·</span>
                    <span>{c.message_count} messages</span>
                    <span className="text-border-strong">·</span>
                    <span>{relativeTime(c.last_message_at ?? c.created_at)}</span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
