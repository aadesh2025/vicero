import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";
import { Sparkline } from "@/components/charts/sparkline";
import { cn } from "@/lib/utils";

/** The meaning colour a KPI wears: chip background, icon and sparkline. */
export type StatTone = "accent" | "success" | "ai" | "warn";

const TONE: Record<StatTone, { chip: string; spark: "accent" | "success" | "ai" | "warn" }> = {
  accent: { chip: "bg-info-soft text-info", spark: "accent" },
  success: { chip: "bg-success-soft text-success", spark: "success" },
  ai: { chip: "bg-ai-soft text-ai", spark: "ai" },
  warn: { chip: "bg-warn-soft text-warn", spark: "warn" },
};

export function StatCard({
  label,
  value,
  delta,
  icon: Icon,
  hint,
  invertDelta = false,
  tone = "accent",
  spark,
}: {
  label: string;
  value: string;
  delta?: number;
  icon: LucideIcon;
  hint?: string;
  /** For metrics where "down is good" (e.g. cost). */
  invertDelta?: boolean;
  tone?: StatTone;
  /** A trend to draw beside the number; omitted when there is no real series behind it. */
  spark?: number[];
}) {
  const up = (delta ?? 0) >= 0;
  const good = invertDelta ? !up : up;
  const t = TONE[tone];

  return (
    <div className="rounded-card border border-border bg-surface p-3 shadow-card">
      <div className="flex items-center gap-2">
        <span className={cn("grid size-7 shrink-0 place-items-center rounded-[9px]", t.chip)}>
          <Icon className="size-4" aria-hidden />
        </span>
        <span className="text-[12px] font-bold text-muted">{label}</span>
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <span className="font-display text-[21px] font-extrabold leading-none tracking-[-0.02em] tabular-nums text-text">
          {value}
        </span>
        {spark && spark.length > 1 && <Sparkline values={spark} color={t.spark} />}
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-xs">
        {delta !== undefined && (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-extrabold",
              good ? "bg-success-soft text-success-text" : "bg-error-soft text-error-text",
            )}
          >
            {up ? <ArrowUpRight className="size-3" aria-hidden /> : <ArrowDownRight className="size-3" aria-hidden />}
            {Math.abs(delta)}%
          </span>
        )}
        {hint && <span className="font-semibold text-faint">{hint}</span>}
      </div>
    </div>
  );
}
