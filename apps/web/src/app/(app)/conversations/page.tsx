"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2, MessagesSquare, Plus, Send, Trash2, User } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ChannelBadge } from "@/components/shared/channel";
import { listAgents } from "@/lib/api/agents";
import {
  chatStream,
  deleteConversation,
  getConversation,
  listConversations,
} from "@/lib/api/conversations";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

interface LiveMsg {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}

export default function ConversationsPage() {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);

  const [activeCid, setActiveCid] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [live, setLive] = useState<LiveMsg[] | null>(null); // set while composing a new/continued thread
  const [input, setInput] = useState("");
  const [picking, setPicking] = useState(false);
  const [sending, setSending] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const { data: conversations, isLoading } = useQuery({
    queryKey: ["conversations", activeOrgId],
    queryFn: () => listConversations(),
    enabled: Boolean(activeOrgId),
  });

  const { data: agents } = useQuery({
    queryKey: ["agents", activeOrgId],
    queryFn: listAgents,
    enabled: Boolean(activeOrgId),
  });

  const { data: detail } = useQuery({
    queryKey: ["conversation", activeCid],
    queryFn: () => getConversation(activeCid!),
    enabled: Boolean(activeCid) && live === null,
  });

  const remove = useMutation({
    mutationFn: (cid: string) => deleteConversation(cid),
    onSuccess: (_r, cid) => {
      qc.invalidateQueries({ queryKey: ["conversations", activeOrgId] });
      if (activeCid === cid) {
        setActiveCid(null);
        setLive(null);
      }
    },
  });

  const agentName = (id: string) => agents?.find((a) => a.id === id)?.name ?? "Agent";

  function openConversation(cid: string, aId: string) {
    setActiveCid(cid);
    setAgentId(aId);
    setLive(null);
    setInput("");
  }

  function startNewChat(aId: string) {
    setAgentId(aId);
    setActiveCid(null);
    setLive([]);
    setPicking(false);
    setInput("");
  }

  async function send() {
    if (!agentId || !input.trim() || sending) return;
    const message = input.trim();
    setInput("");
    setSending(true);

    // Seed the visible thread from either the streaming buffer or the loaded detail.
    const base: LiveMsg[] =
      live ??
      (detail?.messages.map((m) => ({
        role: m.role === "user" ? "user" : "assistant",
        content: m.content ?? "",
      })) as LiveMsg[]) ??
      [];
    const next: LiveMsg[] = [...base, { role: "user", content: message }, { role: "assistant", content: "", streaming: true }];
    setLive(next);
    const assistantIdx = next.length - 1;

    try {
      for await (const ev of chatStream(agentId, message, activeCid ?? undefined)) {
        const type = ev.type as string;
        if (type === "conversation" && typeof ev.conversation_id === "string") {
          setActiveCid(ev.conversation_id);
        } else if (type === "token" && typeof ev.delta === "string") {
          next[assistantIdx] = { ...next[assistantIdx], content: next[assistantIdx].content + ev.delta };
          setLive([...next]);
          scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
        } else if (type === "error") {
          next[assistantIdx] = { role: "assistant", content: `⚠ ${String(ev.error)}` };
          setLive([...next]);
        }
      }
      next[assistantIdx] = { ...next[assistantIdx], streaming: false };
      setLive([...next]);
    } finally {
      setSending(false);
      qc.invalidateQueries({ queryKey: ["conversations", activeOrgId] });
    }
  }

  const threadMsgs: LiveMsg[] =
    live ??
    (detail?.messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.content ?? "" })) as LiveMsg[]) ??
    [];

  const hasThread = activeCid !== null || live !== null;

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader title="Conversations" description="Persisted chats with your agents — history, usage, and memory.">
        <Button variant="primary" onClick={() => setPicking(true)}>
          <Plus /> New chat
        </Button>
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        {/* List */}
        <div className="overflow-hidden rounded-card border border-border bg-surface">
          <div className="border-b border-border p-4 text-sm font-bold text-muted">
            {conversations?.length ?? 0} conversations
          </div>
          <div className="max-h-[60vh] overflow-y-auto scroll-thin">
            {isLoading && <Skeleton className="m-4 h-16" />}
            {!isLoading && (conversations ?? []).length === 0 && (
              <p className="p-6 text-center text-sm font-medium text-muted">No conversations yet. Start a new chat.</p>
            )}
            {(conversations ?? []).map((c) => (
              <button
                key={c.id}
                onClick={() => openConversation(c.id, c.agent_id)}
                className={`group flex w-full items-start gap-3 border-b border-border p-4 text-left transition-colors hover:bg-surface-2 ${
                  activeCid === c.id && live === null ? "bg-surface-2" : ""
                }`}
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-ai-soft text-ai">
                  <MessagesSquare className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <div className="truncate text-sm font-bold text-text">{c.title || "Untitled chat"}</div>
                    <ChannelBadge channel={c.channel} size="sm" className="ml-auto shrink-0" />
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-xs font-medium text-faint">
                    <span className="truncate">{agentName(c.agent_id)}</span>
                    <span>·</span>
                    <span>{c.message_count} msgs</span>
                  </div>
                </div>
                <span className="text-[11px] font-semibold text-faint">
                  {relativeTime(c.last_message_at ?? c.created_at)}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Thread */}
        <div className="flex min-h-[60vh] flex-col rounded-card border border-border bg-surface">
          {!hasThread ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted">
              <MessagesSquare className="size-8 text-faint" />
              <p className="text-sm font-medium">Select a conversation or start a new chat.</p>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between border-b border-border p-4">
                <div className="flex items-center gap-2">
                  <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-ai-soft text-ai">
                    <Bot className="size-4" />
                  </span>
                  <span className="text-sm font-bold text-text">{agentId ? agentName(agentId) : "Agent"}</span>
                  {detail?.memory_summary && <Badge variant="ai">memory</Badge>}
                </div>
                {activeCid && (
                  <button
                    onClick={() => remove.mutate(activeCid)}
                    className="rounded-lg p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                    title="Delete conversation"
                  >
                    <Trash2 className="size-4" />
                  </button>
                )}
              </div>

              <div ref={scroller} className="flex-1 space-y-4 overflow-y-auto scroll-thin p-4">
                {threadMsgs.map((m, i) => (
                  <div key={i} className={`flex gap-3 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                    <span
                      className={`grid size-7 shrink-0 place-items-center rounded-lg ${
                        m.role === "user" ? "bg-surface-3 text-faint" : "bg-ai-soft text-ai"
                      }`}
                    >
                      {m.role === "user" ? <User className="size-3.5" /> : <Bot className="size-3.5" />}
                    </span>
                    <div
                      className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm font-medium ${
                        m.role === "user" ? "bg-surface-2 text-text" : "bg-ai-soft text-text"
                      }`}
                    >
                      {m.content || (m.streaming ? <Loader2 className="size-4 animate-spin text-ai" /> : "")}
                    </div>
                  </div>
                ))}
              </div>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  send();
                }}
                className="flex items-center gap-2 border-t border-border p-3"
              >
                <Input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Message the agent…"
                  aria-label="Message the agent"
                  className="flex-1"
                />
                <Button type="submit" variant="primary" disabled={!input.trim() || sending} aria-label="Send message">
                  {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                </Button>
              </form>
            </>
          )}
        </div>
      </div>

      <Dialog open={picking} onOpenChange={setPicking}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Start a chat</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            {(agents ?? []).map((a) => (
              <button
                key={a.id}
                onClick={() => startNewChat(a.id)}
                className="flex w-full items-center gap-3 rounded-[13px] border border-border bg-surface p-3 text-left transition-colors hover:border-border-strong hover:bg-surface-2"
              >
                <span className="grid size-8 place-items-center rounded-lg bg-ai-soft font-display text-sm font-extrabold text-ai-text">
                  {a.name[0]?.toUpperCase()}
                </span>
                <span className="text-sm font-bold text-text">{a.name}</span>
                <Badge variant={a.status === "published" ? "success" : "default"} className="ml-auto">
                  {a.status}
                </Badge>
              </button>
            ))}
            {(agents ?? []).length === 0 && (
              <p className="p-4 text-center text-sm font-medium text-muted">Create an agent first.</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
