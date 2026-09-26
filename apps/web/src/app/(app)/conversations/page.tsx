"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2, MessagesSquare, Plus, Send, Trash2, User } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
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
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <div className="border-b border-border p-4 text-sm font-medium text-muted">
            {conversations?.length ?? 0} conversations
          </div>
          <div className="max-h-[60vh] overflow-y-auto scroll-thin">
            {isLoading && <Skeleton className="m-4 h-16" />}
            {!isLoading && (conversations ?? []).length === 0 && (
              <p className="p-6 text-center text-sm text-muted">No conversations yet. Start a new chat.</p>
            )}
            {(conversations ?? []).map((c) => (
              <button
                key={c.id}
                onClick={() => openConversation(c.id, c.agent_id)}
                className={`group flex w-full items-start gap-3 border-b border-border p-4 text-left transition-colors hover:bg-surface-2/50 ${
                  activeCid === c.id && live === null ? "bg-surface-2/60" : ""
                }`}
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                  <MessagesSquare className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-text">{c.title || "Untitled chat"}</div>
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-faint">
                    <span className="truncate">{agentName(c.agent_id)}</span>
                    <span>·</span>
                    <span>{c.message_count} msgs</span>
                  </div>
                </div>
                <span className="text-[11px] text-faint">
                  {relativeTime(c.last_message_at ?? c.created_at)}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Thread */}
        <div className="flex min-h-[60vh] flex-col rounded-lg border border-border bg-surface">
          {!hasThread ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted">
              <MessagesSquare className="size-8 text-faint" />
              <p className="text-sm">Select a conversation or start a new chat.</p>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between border-b border-border p-4">
                <div className="flex items-center gap-2">
                  <Bot className="size-4 text-accent" />
                  <span className="text-sm font-medium text-text">{agentId ? agentName(agentId) : "Agent"}</span>
                  {detail?.memory_summary && <Badge variant="accent">memory</Badge>}
                </div>
                {activeCid && (
                  <button
                    onClick={() => remove.mutate(activeCid)}
                    className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                    title="Delete conversation"
                  >
                    <Trash2 className="size-4" />
                  </button>
                )}
              </div>

              <div ref={scroller} className="flex-1 space-y-4 overflow-y-auto scroll-thin p-4">
                {threadMsgs.map((m, i) => (
                  <div key={i} className={`flex gap-3 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                    <span className="grid size-7 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-faint">
                      {m.role === "user" ? <User className="size-3.5" /> : <Bot className="size-3.5 text-accent" />}
                    </span>
                    <div
                      className={`max-w-[80%] whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm ${
                        m.role === "user"
                          ? "border-accent/30 bg-accent/[0.06] text-text"
                          : "border-border bg-surface-2/60 text-text"
                      }`}
                    >
                      {m.content || (m.streaming ? <Loader2 className="size-4 animate-spin text-accent" /> : "")}
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
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Message the agent…"
                  className="h-10 flex-1 rounded-md border border-border bg-surface-2 px-3 text-sm text-text placeholder:text-faint focus-visible:border-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
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
                className="flex w-full items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3 text-left transition-colors hover:border-accent/40"
              >
                <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 font-display text-sm font-semibold text-muted">
                  {a.name[0]?.toUpperCase()}
                </span>
                <span className="text-sm font-medium text-text">{a.name}</span>
                <Badge variant={a.status === "published" ? "success" : "default"} className="ml-auto">
                  {a.status}
                </Badge>
              </button>
            ))}
            {(agents ?? []).length === 0 && (
              <p className="p-4 text-center text-sm text-muted">Create an agent first.</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
