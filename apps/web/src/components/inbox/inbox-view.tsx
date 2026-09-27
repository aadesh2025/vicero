"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Headphones, PauseCircle, User } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/shared/status-pill";
import {
  closeConversation,
  getInboxDetail,
  handback,
  listInbox,
  openInboxSocket,
  takeover,
} from "@/lib/api/inbox";
import { listChannels } from "@/lib/api/channels";
import {
  CHANNEL_META,
  channelMeta,
  channelTone,
  inboxChannelTabs,
  isChannelConnected,
  isNewChannel,
  type InboxChannel,
} from "@/lib/channel-meta";
import { ContactAvatar, contactLabel } from "@/components/inbox/contact-avatar";
import { ChannelNotConnected } from "@/components/inbox/channel-not-connected";
import { ReplyBox } from "@/components/inbox/reply-box";
import { RunMacro } from "@/components/inbox/run-macro";
import { Lock } from "lucide-react";
import { handoffReasonLabel, isPlanLimitReason } from "@/lib/inbox-reason";
import { useSession } from "@/lib/store/session";

const STATUS_FILTERS = [
  { key: "", label: "All" },
  { key: "handoff", label: "Handoff" },
  { key: "active", label: "Active" },
  { key: "closed", label: "Closed" },
];

export function InboxView({ initialId }: { initialId?: string }) {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [filter, setFilter] = useState("");
  const [channel, setChannel] = useState(""); // "" = every channel
  const [activeCid, setActiveCid] = useState<string | null>(initialId ?? null);

  // Every channel gets a tab; this decides which of them are live vs still to set up.
  const { data: channels } = useQuery({
    queryKey: ["channels", activeOrgId],
    queryFn: () => listChannels(),
    enabled: Boolean(activeOrgId),
  });
  const tabs = inboxChannelTabs();
  // "" (All messages) is always viewable; a specific tab may point at an unset-up channel.
  const selectedUnconnected = channel !== "" && !isChannelConnected(channels, channel as InboxChannel);

  const { data: items, isLoading } = useQuery({
    queryKey: ["inbox", activeOrgId, filter, channel],
    queryFn: () => listInbox(filter || undefined, channel || undefined),
    enabled: Boolean(activeOrgId),
  });

  // Realtime: refresh the queue on inbox events.
  useEffect(() => {
    if (!activeOrgId) return;
    const ws = openInboxSocket();
    if (!ws) return;
    ws.onmessage = () => {
      qc.invalidateQueries({ queryKey: ["inbox", activeOrgId] });
      qc.invalidateQueries({ queryKey: ["inbox-detail"] });
    };
    return () => ws.close();
  }, [activeOrgId, qc]);

  const pickChannel = (next: string) => {
    setChannel(next);
    setActiveCid(null); // the open thread may not belong to the channel we just switched to
  };

  return (
    <div className="flex h-[calc(100vh-220px)] flex-col overflow-hidden rounded-card border border-border bg-surface">
      {/* Every channel, connected or not — an unconnected tab is how you discover it. */}
      <div
        role="tablist"
        aria-label="Channels"
        className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-2 scroll-thin"
      >
        <ChannelTab label="All messages" active={channel === ""} onClick={() => pickChannel("")} />
        {tabs.map((type) => {
          const meta = CHANNEL_META[type];
          return (
            <ChannelTab
              key={type}
              channel={type}
              label={meta.label}
              icon={<meta.Icon className="size-3.5" aria-hidden />}
              badge={isNewChannel(channels, type) ? "New" : undefined}
              connected={isChannelConnected(channels, type)}
              active={channel === type}
              onClick={() => pickChannel(type)}
            />
          );
        })}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[340px_1fr] overflow-hidden">
        <div className="flex min-h-0 flex-col border-r border-border">
          <div className="flex gap-1 border-b border-border p-2">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`rounded-lg px-2.5 py-1 text-xs font-bold transition-colors ${
                  filter === f.key ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto scroll-thin">
            {/* A channel that can't receive at all reads differently from one that's
                simply had no messages yet — don't collapse the two. */}
            {selectedUnconnected && <ChannelNotConnected channel={channel as InboxChannel} compact />}
            {!selectedUnconnected && isLoading && <Skeleton className="m-3 h-16" />}
            {!selectedUnconnected && !isLoading && (items ?? []).length === 0 && (
              <p className="p-6 text-center text-sm text-muted">Nothing in the inbox yet.</p>
            )}
            {!selectedUnconnected &&
              (items ?? []).map((it) => (
              <button
                key={it.id}
                onClick={() => setActiveCid(it.id)}
                className={`flex w-full items-start gap-3 border-b border-border p-3 text-left transition-colors hover:bg-surface-2/50 ${
                  activeCid === it.id ? "bg-surface-2/60" : ""
                }`}
              >
                <ContactAvatar
                  channel={it.channel}
                  name={contactLabel(it.contact, it.channel_user_id)}
                  avatarUrl={it.contact?.avatar_url}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-bold text-text">
                      {contactLabel(it.contact, it.channel_user_id)}
                    </span>
                    <StatusPill status={it.status} className="ml-auto shrink-0" />
                  </div>
                  <div className="truncate text-xs text-muted">{it.title || "Conversation"}</div>
                  <div className="flex items-center gap-2 text-xs text-faint">
                    <span>{it.message_count} msgs</span>
                    {it.handoff && it.handoff.status !== "resolved" && (
                      <span className="ml-auto flex items-center gap-1.5">
                        {isPlanLimitReason(it.handoff.reason) && (
                          <span
                            title={handoffReasonLabel(it.handoff.reason as string)}
                            className="inline-flex items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-[11px] font-extrabold text-faint"
                          >
                            <Lock className="size-2.5" aria-hidden /> plan limit
                          </span>
                        )}
                        <Badge variant={it.handoff.assigned_to ? "ai" : "warn"}>
                          {it.handoff.assigned_to ? "assigned" : "needs agent"}
                        </Badge>
                      </span>
                    )}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {selectedUnconnected ? (
          <div className="flex items-center justify-center">
            <ChannelNotConnected channel={channel as InboxChannel} />
          </div>
        ) : activeCid ? (
          <Thread cid={activeCid} onChanged={() => qc.invalidateQueries({ queryKey: ["inbox", activeOrgId] })} />
        ) : (
          <div className="flex items-center justify-center text-sm text-muted">
            Select a conversation to view it.
          </div>
        )}
      </div>
    </div>
  );
}

function ChannelTab({
  channel,
  label,
  icon,
  badge,
  active,
  connected = true,
  onClick,
}: {
  /** "" for the "All messages" tab, which has no channel colour of its own. */
  channel?: string;
  label: string;
  icon?: React.ReactNode;
  badge?: string;
  active: boolean;
  /** Unconnected tabs stay clickable — that's how you get to the connect flow. */
  connected?: boolean;
  onClick: () => void;
}) {
  const tone = channel ? channelTone(channel) : null;
  return (
    <button
      role="tab"
      aria-selected={active}
      // Stated explicitly: the accessible name is computed by concatenating text nodes
      // without separators, so a visually-hidden suffix would read as "Instagram(not
      // connected)". Keeps the visible label as a prefix, per label-in-name.
      aria-label={connected ? undefined : `${label} (not connected)`}
      onClick={onClick}
      // Dimmed rather than disabled: "available, not set up yet", still reachable.
      className={`flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-bold transition-colors ${
        active
          ? "border-current"
          : connected
            ? "border-transparent text-muted hover:border-border-strong hover:text-text"
            : "border-transparent text-faint hover:border-border hover:text-muted"
      }`}
      style={active ? { color: tone?.text ?? "rgb(var(--accent))" } : undefined}
    >
      {icon}
      {label}
      {/* A hollow dot reads as "off" at a glance; aria-label carries it for screen readers. */}
      {!connected && (
        <span aria-hidden className="ml-0.5 size-1.5 rounded-full border border-current opacity-70" />
      )}
      {badge && (
        <Badge variant="accent" className="ml-0.5">
          {badge}
        </Badge>
      )}
    </button>
  );
}

function Thread({ cid, onChanged }: { cid: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const scroller = useRef<HTMLDivElement>(null);

  const { data: detail } = useQuery({
    queryKey: ["inbox-detail", cid],
    queryFn: () => getInboxDetail(cid),
    refetchInterval: 4000, // catch end-user messages while handed off
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["inbox-detail", cid] });
    onChanged();
  };
  const doTakeover = useMutation({ mutationFn: () => takeover(cid), onSuccess: invalidate });
  const doHandback = useMutation({ mutationFn: () => handback(cid), onSuccess: invalidate });
  const doClose = useMutation({ mutationFn: () => closeConversation(cid), onSuccess: invalidate });

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [detail?.messages.length]);

  const handoff = detail?.handoff;
  const isHandoff = detail?.status === "handoff";
  const assigned = Boolean(handoff?.assigned_to);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {isHandoff && (
        <div
          role="status"
          className={
            assigned
              ? "flex items-center gap-2 border-b border-border bg-warn-soft px-3 py-2 text-xs font-bold text-warn-text"
              : "flex items-center gap-2 border-b border-border bg-info-soft px-3 py-2 text-xs font-bold text-info-text"
          }
        >
          <PauseCircle className="size-3.5 shrink-0" aria-hidden />
          {assigned ? "Bot paused — you're replying" : "Waiting for a teammate to take over"}
        </div>
      )}
      <div className="flex items-center gap-3 border-b border-border p-3">
        <ContactAvatar
          channel={detail?.channel ?? "widget"}
          name={detail ? contactLabel(detail.contact, detail.channel_user_id) : null}
          avatarUrl={detail?.contact?.avatar_url}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-text">
            {/* Linked only once this handle is matched to a CRM person — `contact.id` is
                the per-channel handle, which the CRM (which lists people) can't resolve. */}
            {detail?.contact?.crm_contact_id ? (
              <Link
                href={`/contacts/${detail.contact.crm_contact_id}`}
                className="hover:text-accent-2 hover:underline"
              >
                {contactLabel(detail.contact, detail.channel_user_id)}
              </Link>
            ) : (
              (detail ? contactLabel(detail.contact, detail.channel_user_id) : "Conversation")
            )}
          </div>
          <div className="truncate text-xs font-medium text-faint">
            {detail ? `${channelMeta(detail.channel).label} · ${detail.status}` : ""}
            {handoff?.reason ? ` · reason: ${handoffReasonLabel(handoff.reason)}` : ""}
          </div>
        </div>
        {isHandoff && !assigned && (
          <Button size="sm" variant="primary" onClick={() => doTakeover.mutate()} disabled={doTakeover.isPending}>
            <Headphones className="size-4" /> Take over
          </Button>
        )}
        {isHandoff && assigned && (
          <Button size="sm" variant="ai" onClick={() => doHandback.mutate()} disabled={doHandback.isPending}>
            <Bot className="size-4" /> Hand back
          </Button>
        )}
        <RunMacro cid={cid} onRan={invalidate} />
        {detail?.status !== "closed" && (
          <Button size="sm" variant="outline" onClick={() => doClose.mutate()} disabled={doClose.isPending}>
            <Check className="size-4" /> Close
          </Button>
        )}
      </div>

      <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto scroll-thin p-4">
        {(detail?.messages ?? []).map((m) => {
          const who = m.role === "user" ? "user" : "assistant";
          const operator = m.provider === "operator";
          return (
            <div key={m.id} className={`flex gap-2 ${who === "user" ? "flex-row-reverse" : ""}`}>
              <span
                className={`grid size-7 shrink-0 place-items-center rounded-lg ${
                  who === "user" ? "bg-surface-3 text-faint" : operator ? "bg-accent-soft text-accent" : "bg-ai-soft text-ai"
                }`}
              >
                {who === "user" ? <User className="size-3.5" /> : operator ? <Headphones className="size-3.5" /> : <Bot className="size-3.5" />}
              </span>
              <div
                className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm font-medium ${
                  who === "user"
                    ? "bg-surface-2 text-text"
                    : operator
                      ? "bg-accent-soft text-text"
                      : "bg-ai-soft text-text"
                }`}
              >
                {operator && (
                  <div className="mb-0.5 text-[10px] font-extrabold uppercase tracking-wide text-accent">Operator</div>
                )}
                {m.content}
              </div>
            </div>
          );
        })}
      </div>

      {isHandoff && assigned ? (
        <ReplyBox cid={cid} sendWindow={detail?.send_window ?? null} onSent={invalidate} />
      ) : (
        <div className="border-t border-border p-3 text-center text-xs font-semibold text-muted">
          {isHandoff ? "Take over to reply." : "The assistant is handling this conversation."}
        </div>
      )}
    </div>
  );
}
