"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, FileText, Headphones, RotateCcw, Sparkles, Wrench } from "lucide-react";
import { useBuilder } from "@/lib/store/builder";
import { useSession } from "@/lib/store/session";
import { playgroundStream } from "@/lib/api/agents";
import { listProviders } from "@/lib/api/credentials";
import { cn } from "@/lib/utils";

interface Msg {
  id: number;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  citation?: string;
  tool?: string;
  handoff?: boolean;
}

export function Playground() {
  const draft = useBuilder((s) => s.draft);
  const agentId = useBuilder((s) => s.agentId);
  const orgId = useSession((s) => s.activeOrgId);
  // Shares the Model tab's cache entry, so this costs no extra request in the builder.
  const providers = useQuery({
    queryKey: ["providers", orgId],
    queryFn: listProviders,
    enabled: Boolean(orgId),
  });
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(0);
  // The persisted playground conversation these turns belong to. A ref, not state: it's
  // read inside `send` and never rendered, so storing it in state would re-render the
  // transcript on the first turn for no visible change.
  const conversationRef = useRef<string | null>(null);

  // Seed with the welcome message whenever the draft's welcome text changes.
  useEffect(() => {
    if (!draft) return;
    setMessages([{ id: idRef.current++, role: "assistant", text: draft.persona.welcomeMessage }]);
  }, [draft?.persona.welcomeMessage]); // eslint-disable-line react-hooks/exhaustive-deps

  // Moving to another agent starts a new session. Without this the ref would survive the
  // store re-pointing at a different agent and the next turn would 400 on agent_mismatch.
  useEffect(() => {
    conversationRef.current = null;
  }, [agentId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy || !agentId) return;
    setInput("");
    setBusy(true);

    const history = messages.filter((m) => m.text).map((m) => ({ role: m.role, content: m.text }));
    const userId = idRef.current++;
    const botId = idRef.current++;
    setMessages((m) => [
      ...m,
      { id: userId, role: "user", text },
      { id: botId, role: "assistant", text: "", streaming: true },
    ]);

    try {
      for await (const event of playgroundStream(agentId, text, history, conversationRef.current)) {
        const type = event.type as string;
        if (type === "conversation" && typeof event.conversation_id === "string") {
          conversationRef.current = event.conversation_id;
        } else if (type === "token" && typeof event.delta === "string") {
          const delta = event.delta;
          setMessages((m) => m.map((msg) => (msg.id === botId ? { ...msg, text: msg.text + delta } : msg)));
        } else if (type === "tool_call") {
          const tc = event.tool_call as { name?: string } | null;
          setMessages((m) => m.map((msg) => (msg.id === botId ? { ...msg, tool: tc?.name } : msg)));
        } else if (type === "done" && event.finish_reason === "handoff") {
          // The bot paused itself here — same trigger a real visitor's "talk to a human"
          // hits in app/chat/inbound.py. Flag it so the transcript shows what actually
          // happened instead of reading like an ordinary reply.
          setMessages((m) => m.map((msg) => (msg.id === botId ? { ...msg, handoff: true } : msg)));
        } else if (type === "error") {
          setMessages((m) =>
            m.map((msg) =>
              msg.id === botId ? { ...msg, text: `⚠ ${String(event.error)}`, streaming: false } : msg,
            ),
          );
        }
      }
    } catch {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === botId ? { ...msg, text: msg.text || "⚠ Couldn't reach the agent.", streaming: false } : msg,
        ),
      );
    } finally {
      setMessages((m) => m.map((msg) => (msg.id === botId ? { ...msg, streaming: false } : msg)));
      setBusy(false);
    }
  };

  const reset = () => {
    // A reset starts a fresh session, so the next turn opens a new conversation rather than
    // appending an unrelated transcript to the previous one.
    conversationRef.current = null;
    setMessages(draft ? [{ id: idRef.current++, role: "assistant", text: draft.persona.welcomeMessage }] : []);
  };

  if (!draft) return null;
  const providerLabel =
    providers.data?.find((p) => p.name === draft.model.provider)?.label ?? draft.model.provider;
  const modelLabel = `${providerLabel} · ${draft.model.model}`;

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Sparkles className="size-4 text-accent" />
        <span className="font-display text-sm font-semibold text-text">Playground</span>
        <span className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-faint">draft</span>
        <button
          onClick={reset}
          className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted transition-colors hover:bg-surface-2 hover:text-text"
        >
          <RotateCcw className="size-3.5" /> Reset
        </button>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4 scroll-thin">
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-accent-strong px-3.5 py-2 text-sm text-on-accent">
                {m.text}
              </div>
            </div>
          ) : (
            <div key={m.id} className="flex flex-col gap-1.5">
              {m.handoff && (
                <div className="inline-flex w-fit items-center gap-1.5 rounded-md border border-accent/25 bg-accent/[0.07] px-2 py-1 text-[11px] text-accent">
                  <Headphones className="size-3" /> Handed off to a human — status set to &quot;handoff&quot;
                </div>
              )}
              {m.tool && (
                <div className="inline-flex w-fit items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2 py-1 text-[11px] text-muted">
                  <Wrench className="size-3 text-accent" />
                  called <span className="font-mono text-accent">{m.tool}</span>
                </div>
              )}
              <div className="max-w-[85%] rounded-2xl rounded-tl-sm border border-border bg-surface-2 px-3.5 py-2 text-sm leading-relaxed text-text">
                {m.text}
                {m.streaming && (
                  <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-caret-blink bg-accent" />
                )}
              </div>
              {m.citation && !m.streaming && (
                <div className="inline-flex w-fit items-center gap-1.5 rounded-md border border-accent/25 bg-accent/[0.07] px-2 py-1 text-[11px] text-accent">
                  <FileText className="size-3" /> {m.citation}
                </div>
              )}
            </div>
          ),
        )}
      </div>

      <div className="border-t border-border p-3">
        <div className="flex items-end gap-2 rounded-lg border border-border bg-surface-2 p-2 focus-within:border-accent/50">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
            placeholder="Message the draft agent…"
            className="max-h-28 flex-1 resize-none bg-transparent px-1 py-1.5 text-sm text-text placeholder:text-faint focus:outline-none"
          />
          <button
            onClick={send}
            disabled={!input.trim() || busy}
            className={cn(
              "grid size-8 shrink-0 place-items-center rounded-md transition-colors",
              input.trim() && !busy ? "bg-accent-strong text-on-accent hover:bg-accent" : "bg-surface-3 text-faint",
            )}
            aria-label="Send message"
          >
            <CornerDownLeft className="size-4" />
          </button>
        </div>
        <div className="mt-1.5 flex items-center justify-between px-1 text-[11px] text-faint">
          <span className="truncate font-mono">{modelLabel}</span>
          <span>temp {draft.model.temperature.toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}
