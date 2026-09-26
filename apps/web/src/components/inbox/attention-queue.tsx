"use client";

import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bot, Check, Loader2, ShieldAlert, UserCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { listAttention, resolveAttention, takeover, type ApiAttentionItem } from "@/lib/api/inbox";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

/** The attention queue (docs/11 §L6, Phase E).
 *
 * Distinct from the inbox's handoff queue, and the distinction is the feature: here the bot is
 * usually **still answering** while a human decides whether to step in. `bot_still_answering`
 * is rendered explicitly on every row, because "who is talking to this customer right now"
 * must never be something an operator has to infer.
 *
 * Ordered server-side by severity then age, so crisis pins to the top and the oldest
 * unattended conversation is next — an operator working top-down is working the right order.
 */

const SEVERITY: Record<string, { label: string; row: string; chip: string; icon: typeof AlertTriangle }> = {
  crisis: {
    label: "Crisis",
    row: "border-error/50 bg-error/[0.06]",
    chip: "border-error/40 bg-error/15 text-error-text",
    icon: ShieldAlert,
  },
  elevated: {
    label: "Elevated",
    row: "border-warn/40 bg-warn/[0.05]",
    chip: "border-warn/40 bg-warn/15 text-warn-text",
    icon: AlertTriangle,
  },
  mild: {
    label: "Mild",
    row: "border-border",
    chip: "border-border bg-surface-2 text-muted",
    icon: AlertTriangle,
  },
};

function Trajectory({ item }: { item: ApiAttentionItem }) {
  if (item.flags.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {item.flags.map((f, i) => (
        <span key={f.id} className="inline-flex items-center gap-1.5">
          {i > 0 && <span className="text-faint">→</span>}
          <span
            className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${SEVERITY[f.severity]?.chip ?? ""}`}
            title={`${f.kind} · ${relativeTime(f.created_at)}`}
          >
            {f.kind === "distress" ? SEVERITY[f.severity]?.label ?? f.severity : f.kind}
          </span>
        </span>
      ))}
    </div>
  );
}

export function AttentionQueue() {
  const router = useRouter();
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);

  const { data: items, isLoading } = useQuery({
    queryKey: ["attention", activeOrgId],
    queryFn: listAttention,
    enabled: Boolean(activeOrgId),
    // The whole point is noticing quickly; the payload is small and the list is short.
    refetchInterval: 15_000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["attention", activeOrgId] });
    qc.invalidateQueries({ queryKey: ["inbox"] });
  };
  const resolve = useMutation({ mutationFn: resolveAttention, onSuccess: invalidate });
  const take = useMutation({ mutationFn: takeover, onSuccess: invalidate });

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-28 rounded-lg" />
        ))}
      </div>
    );
  }

  if (!items || items.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-surface p-10 text-center">
        <Check className="mx-auto size-6 text-success-text" />
        <p className="mt-2 text-sm text-text">Nothing needs attention.</p>
        <p className="mt-1 text-xs text-faint">
          Conversations appear here when a customer sounds angry, under real pressure, or in
          distress. An empty queue is the normal state.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {items.map((item) => {
        const meta = SEVERITY[item.attention_level ?? "mild"] ?? SEVERITY.mild;
        const Icon = meta.icon;
        const signals = item.flags.flatMap((f) => f.signals).slice(0, 4);
        const lastCustomer = [...item.recent_messages].reverse().find((m) => m.role === "user");
        return (
          <div key={item.id} className={`rounded-lg border p-4 ${meta.row}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Icon className={`size-4 ${item.attention_level === "crisis" ? "text-error-text" : "text-warn-text"}`} />
                  <span className="font-medium text-text">
                    {item.contact?.display_name || item.channel_user_id || "Visitor"}
                  </span>
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${meta.chip}`}>
                    {meta.label}
                  </span>
                  {/* Never ambiguous who is replying to the customer right now. */}
                  {item.bot_still_answering ? (
                    <span
                      className="inline-flex items-center gap-1 rounded border border-info/30 bg-info/10 px-1.5 py-0.5 text-[11px] text-info-text"
                      title="The bot has not been paused — it is still replying while you decide."
                    >
                      <Bot className="size-3" /> AI is still responding
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted">
                      <UserCheck className="size-3" /> A human has taken over
                    </span>
                  )}
                </div>
                {signals.length > 0 && (
                  <p className="mt-1.5 text-sm text-muted">
                    {signals.map((s, i) => (
                      <span key={`${item.id}-sig-${i}`}>
                        {i > 0 && <span className="text-faint"> · </span>}
                        <span className="italic">&ldquo;{s}&rdquo;</span>
                      </span>
                    ))}
                  </p>
                )}
                {lastCustomer?.content && (
                  <p className="mt-1.5 line-clamp-2 text-sm text-text/80">{lastCustomer.content}</p>
                )}
                <Trajectory item={item} />
              </div>

              <div className="flex shrink-0 items-center gap-2">
                <span className="text-xs text-faint">
                  {relativeTime(item.last_message_at ?? item.created_at)}
                </span>
                <Button variant="outline" size="sm" onClick={() => router.push(`/inbox/${item.id}`)}>
                  Open
                </Button>
                {item.bot_still_answering && (
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={take.isPending}
                    onClick={() => take.mutate(item.id)}
                    title="Pause the bot and reply yourself"
                  >
                    {take.isPending ? <Loader2 className="animate-spin" /> : <UserCheck />} Take over
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate(item.id)}
                  title="Clear the flags — the only way this leaves the queue"
                >
                  {resolve.isPending ? <Loader2 className="animate-spin" /> : <Check />} Resolve
                </Button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
