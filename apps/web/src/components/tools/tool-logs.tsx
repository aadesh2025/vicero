"use client";

import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, RotateCcw } from "lucide-react";
import { SectionCard } from "@/components/builder/field";
import { StatusPill } from "@/components/shared/status-pill";
import { Button } from "@/components/ui/button";
import { relativeTime } from "@/lib/utils";
import { listToolRunPage, listTools, type ApiToolRun } from "@/lib/api/tools";

const STATUSES = [
  "",
  "success",
  "error",
  "timeout",
  "pending",
  "pending_approval",
  "declined",
] as const;
const STATUS_LABEL: Record<string, string> = {
  "": "Any status",
  success: "Succeeded",
  error: "Failed",
  timeout: "Timed out",
  pending: "Running in background",
  pending_approval: "Waiting for approval",
  declined: "Declined",
};

/** The tool-run log for one agent: filter by tool and status, open a row to see what was sent and what came back
 *  (credentials already masked by the server), and replay the arguments in the test panel (docs/28 T5.6). */
export function ToolLogs({
  agentId,
  onReplay,
}: {
  agentId: string | null;
  /** Called with the tool and the arguments of the run to try again. */
  onReplay: (toolId: string, input: Record<string, unknown>) => void;
}) {
  const [toolId, setToolId] = useState("");
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const { data: tools } = useQuery({
    queryKey: ["tools", agentId],
    queryFn: () => listTools(agentId ?? undefined),
    enabled: Boolean(agentId),
  });
  const runs = useInfiniteQuery({
    queryKey: ["tool-run-page", agentId, toolId, status],
    queryFn: ({ pageParam }) =>
      listToolRunPage({
        agentId: agentId ?? undefined,
        toolId: toolId || undefined,
        status: status || undefined,
        cursor: pageParam,
        limit: 15,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: Boolean(agentId),
  });
  const rows = runs.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <SectionCard
      title="Tool log"
      description="Every call the agent made: what it sent, what came back, and how long it took."
    >
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Filter by tool"
          value={toolId}
          onChange={(e) => setToolId(e.target.value)}
          className="h-8 rounded-lg border border-border-strong bg-surface px-2 text-[13px] font-medium text-text"
        >
          <option value="">Every tool</option>
          {(tools ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="h-8 rounded-lg border border-border-strong bg-surface px-2 text-[13px] font-medium text-text"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
      </div>

      {runs.isLoading ? (
        <p className="text-sm text-muted">Loading...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">
          No calls match. When the agent uses a tool it shows up here.
        </p>
      ) : (
        <ul className="space-y-1.5" aria-label="Tool runs">
          {rows.map((r) => (
            <RunRow
              key={r.id}
              run={r}
              expanded={open === r.id}
              onToggle={() => setOpen(open === r.id ? null : r.id)}
              onReplay={onReplay}
            />
          ))}
        </ul>
      )}
      {runs.hasNextPage && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => void runs.fetchNextPage()}
          disabled={runs.isFetchingNextPage}
        >
          Show older
        </Button>
      )}
    </SectionCard>
  );
}

function RunRow({
  run,
  expanded,
  onToggle,
  onReplay,
}: {
  run: ApiToolRun;
  expanded: boolean;
  onToggle: () => void;
  onReplay: (toolId: string, input: Record<string, unknown>) => void;
}) {
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <li className="rounded-md border border-border bg-surface-2/40">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs"
      >
        <Chevron className="size-3.5 shrink-0 text-faint" aria-hidden />
        <span className="font-mono text-text">
          {run.tool_name ?? run.tool_id.slice(0, 8)}
        </span>
        <StatusPill status={run.status} />
        {run.attempt > 1 && (
          <span className="text-faint">{run.attempt} tries</span>
        )}
        <span className="ml-auto text-faint">
          {run.latency_ms ?? "-"} ms · {relativeTime(run.created_at)}
        </span>
      </button>
      {expanded && (
        <div className="space-y-3 border-t border-border p-3 text-xs">
          <Block title="Arguments from the agent" value={run.input} />
          {run.request_sanitized && (
            <Block
              title="Request sent (credentials masked)"
              value={run.request_sanitized}
            />
          )}
          <Block
            title={run.error ? `Result - ${run.error}` : "Result"}
            value={run.output ?? {}}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => onReplay(run.tool_id, run.input)}
          >
            <RotateCcw /> Replay in test panel
          </Button>
        </div>
      )}
    </li>
  );
}

function Block({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <div className="mb-1 font-bold text-text">{title}</div>
      <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface p-2 font-mono text-muted">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
