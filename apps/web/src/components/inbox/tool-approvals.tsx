"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, ShieldQuestion, X } from "lucide-react";
import { StatusPill } from "@/components/shared/status-pill";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  decideToolApproval,
  listToolApprovals,
  type ToolApproval,
} from "@/lib/api/hooks";
import { useCan } from "@/lib/rbac";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

/** Tool calls the agent wants to make that need a person's yes first (docs/28 T4.4, T5.7).
 *
 * The customer is not waiting in silence: the agent has told them it asked and carries on chatting, and it tells them
 * how it went once someone decides. Arguments are shown with secret-looking values already masked by the server. */
export function ToolApprovals() {
  const qc = useQueryClient();
  const orgId = useSession((s) => s.activeOrgId);
  const canHandle = useCan("inbox:handle");
  const [showDecided, setShowDecided] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["tool-approvals", orgId, showDecided],
    queryFn: () => listToolApprovals(showDecided),
    enabled: Boolean(orgId) && canHandle,
    refetchInterval: 15_000,
  });
  const decide = useMutation({
    mutationFn: ({
      id,
      decision,
    }: {
      id: string;
      decision: "approved" | "rejected";
    }) => decideToolApproval(id, decision),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["tool-approvals"] }),
  });

  const rows = data ?? [];
  return (
    <div className="space-y-4" data-testid="tool-approvals">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted">
          Each request is an action your agent wants to take. Nothing happens
          until you approve it.
        </p>
        <label className="flex items-center gap-2 text-xs font-bold text-muted">
          <input
            type="checkbox"
            checked={showDecided}
            onChange={(e) => setShowDecided(e.target.checked)}
            className="size-4 accent-[var(--accent)]"
          />
          Show decided
        </label>
      </div>

      {decide.isError && (
        <p role="alert" className="text-sm text-error-text">
          {(decide.error as Error).message}
        </p>
      )}

      {isLoading ? (
        <Skeleton className="h-28 w-full" />
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-strong p-8 text-center">
          <ShieldQuestion
            className="mx-auto mb-2 size-5 text-faint"
            aria-hidden
          />
          <p className="text-sm font-bold text-text">
            Nothing waiting for approval
          </p>
          <p className="mt-0.5 text-sm text-muted">
            Tools set to ask first will show up here.
          </p>
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Tool approvals">
          {rows.map((a) => (
            <ApprovalCard
              key={a.handoff_id}
              item={a}
              busy={decide.isPending && decide.variables?.id === a.handoff_id}
              onDecide={(decision) =>
                decide.mutate({ id: a.handoff_id, decision })
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function ApprovalCard({
  item,
  busy,
  onDecide,
}: {
  item: ToolApproval;
  busy: boolean;
  onDecide: (d: "approved" | "rejected") => void;
}) {
  const open = item.status !== "resolved";
  return (
    <li
      className="rounded-card border border-border bg-surface p-4 shadow-card"
      data-testid="tool-approval"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-bold text-text">
          {item.tool}
        </span>
        {open ? (
          <StatusPill status="pending">Waiting</StatusPill>
        ) : (
          <StatusPill
            status={item.decision === "approved" ? "success" : "rejected"}
          >
            {item.decision === "approved" ? "Approved" : "Declined"}
          </StatusPill>
        )}
        <span className="text-xs text-faint">
          {relativeTime(item.created_at)}
        </span>
        {item.conversation_id && (
          <Link
            href={`/inbox/${item.conversation_id}`}
            className="ml-auto text-xs font-bold text-accent hover:underline"
          >
            Open the conversation
          </Link>
        )}
      </div>
      <pre
        aria-label={`Arguments for ${item.tool}`}
        className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-md bg-surface-2 p-3 font-mono text-xs text-muted"
      >
        {JSON.stringify(item.args, null, 2)}
      </pre>
      {open && (
        <div className="mt-3 flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onDecide("rejected")}
          >
            <X /> Decline
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={busy}
            onClick={() => onDecide("approved")}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Check />} Approve
          </Button>
        </div>
      )}
    </li>
  );
}
