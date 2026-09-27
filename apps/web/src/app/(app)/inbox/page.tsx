"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { InboxView } from "@/components/inbox/inbox-view";
import { AttentionQueue } from "@/components/inbox/attention-queue";
import { PageHeader } from "@/components/dashboard/page-header";
import { listAttention } from "@/lib/api/inbox";
import { useSession } from "@/lib/store/session";

/** Attention is a tab on the inbox, not a second nav item (docs/11 §L6).
 *
 * The inbox already owns "conversations a human is involved in"; a separate nav entry would
 * fragment that queue and give an operator two places to check. The count badge is the point —
 * a queue nobody notices is worth nothing on the day it matters.
 */
export default function InboxPage() {
  const [tab, setTab] = useState<"inbox" | "attention">("inbox");
  const activeOrgId = useSession((s) => s.activeOrgId);

  const { data: attention } = useQuery({
    queryKey: ["attention", activeOrgId],
    queryFn: listAttention,
    enabled: Boolean(activeOrgId),
    refetchInterval: 15_000,
  });
  const count = attention?.length ?? 0;
  const hasCrisis = (attention ?? []).some((a) => a.attention_level === "crisis");

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <PageHeader title="Inbox" description="Live conversations across every channel. Take over when the bot needs a hand." />

      <div className="flex items-center gap-0.5 rounded-[10px] bg-surface-3 p-[3px]">
        <button
          onClick={() => setTab("inbox")}
          aria-current={tab === "inbox"}
          className={`rounded-lg px-3 py-1.5 text-[13px] font-bold transition-colors ${
            tab === "inbox" ? "bg-surface text-text shadow-card dark:bg-surface-2" : "text-muted hover:text-text"
          }`}
        >
          Conversations
        </button>
        <button
          onClick={() => setTab("attention")}
          aria-current={tab === "attention"}
          aria-label={`Needs attention${count ? ` (${count})` : ""}`}
          className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-[13px] font-bold transition-colors ${
            tab === "attention" ? "bg-surface text-text shadow-card dark:bg-surface-2" : "text-muted hover:text-text"
          }`}
        >
          Needs attention
          {count > 0 && (
            <span
              className={`rounded-full px-1.5 py-0.5 text-[11px] font-extrabold ${
                hasCrisis ? "bg-error-soft text-error-text" : "bg-warn-soft text-warn-text"
              }`}
            >
              {count}
            </span>
          )}
        </button>
      </div>

      {tab === "inbox" ? <InboxView /> : <AttentionQueue />}
    </div>
  );
}
