import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { NODE_TYPE_BY_ID, branchLabelsFor } from "./node-types";
import type { WorkflowNodeType } from "@/lib/api/types";

export interface WorkflowNodeData extends Record<string, unknown> {
  nodeType: WorkflowNodeType;
  config: Record<string, unknown>;
  label?: string;
  /** Set only while a test run's steps are overlaid on the canvas (item 6's run-overlay). */
  runStatus?: "completed" | "failed" | "awaiting_approval" | "current" | null;
}

/** One canvas node for every workflow node type. A single component rather than 13 — the
 * only real differences between types are the icon/color (looked up from `NODE_TYPE_BY_ID`)
 * and how many labeled output handles it needs (branch-style types only). */
export function WorkflowNode({ data, selected }: NodeProps) {
  const d = data as WorkflowNodeData;
  const meta = NODE_TYPE_BY_ID[d.nodeType];
  const Icon = meta?.icon ?? AlertCircle;
  const branches = branchLabelsFor(d.nodeType, d.config ?? {});
  const isStart = d.nodeType === "start";
  const isEnd = d.nodeType === "end";

  const runRing =
    d.runStatus === "completed"
      ? "ring-2 ring-success/60"
      : d.runStatus === "failed"
        ? "ring-2 ring-error/60"
        : d.runStatus === "current" || d.runStatus === "awaiting_approval"
          ? "ring-2 ring-accent/70 animate-pulse"
          : "";

  // meta.color is a text/border/bg triple ("text-X border-X/40 bg-X/10"); the text class is the
  // one reused for the top bar and the icon, so the node reads by colour at a glance without a
  // tinted border fighting the card's own border on every side.
  const textClass = meta?.color?.split(" ")[0] ?? "text-muted";

  return (
    <div
      className={cn(
        "relative min-w-[180px] overflow-hidden rounded-lg border border-border bg-surface px-3 py-2.5 shadow-card transition-shadow",
        selected && "shadow-pop ring-2 ring-ring",
        runRing,
      )}
    >
      <span className={cn("absolute inset-x-0 top-0 h-[3px] bg-current", textClass)} aria-hidden />
      {!isStart && (
        <Handle type="target" position={Position.Top} className="!size-2.5 !border-border !bg-surface" />
      )}
      <div className={cn("flex items-center gap-2", textClass)}>
        <Icon className="size-4 shrink-0" />
        <div className="min-w-0">
          <p className="truncate text-xs font-bold">{meta?.label ?? d.nodeType}</p>
          {d.label && <p className="truncate text-[11px] font-medium text-muted">{d.label}</p>}
        </div>
        {d.runStatus === "completed" && <CheckCircle2 className="ml-auto size-3.5 shrink-0 text-success-text" />}
        {d.runStatus === "failed" && <AlertCircle className="ml-auto size-3.5 shrink-0 text-error-text" />}
      </div>

      {!isEnd && branches.length === 0 && (
        <Handle type="source" position={Position.Bottom} className="!size-2.5 !border-border !bg-surface" />
      )}
      {branches.length > 0 && (
        <div className="mt-2 flex justify-between gap-1 border-t border-border/60 pt-1.5">
          {branches.map((label) => (
            <span key={label} className="relative flex-1 text-center text-[10px] text-muted">
              {label}
              <Handle
                type="source"
                position={Position.Bottom}
                id={label}
                className="!size-2.5 !border-border !bg-surface"
                style={{ left: "50%" }}
              />
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
