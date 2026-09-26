import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  delta,
  icon: Icon,
  hint,
  invertDelta = false,
}: {
  label: string;
  value: string;
  delta?: number;
  icon: LucideIcon;
  hint?: string;
  /** For metrics where "down is good" (e.g. cost). */
  invertDelta?: boolean;
}) {
  const up = (delta ?? 0) >= 0;
  const good = invertDelta ? !up : up;

  return (
    <div className="group relative overflow-hidden rounded-lg border border-border bg-surface p-5 transition-colors hover:border-border-strong">
      {/* accent top hairline reveals on hover */}
      <span className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/70 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">{label}</span>
        <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-faint transition-colors group-hover:text-accent">
          <Icon className="size-4" />
        </span>
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="font-display text-[28px] font-semibold leading-none tracking-tight text-text">
          {value}
        </span>
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs">
        {delta !== undefined && (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-medium",
              good ? "bg-success/10 text-success-text" : "bg-error/10 text-error-text",
            )}
          >
            {up ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}
            {Math.abs(delta)}%
          </span>
        )}
        {hint && <span className="text-faint">{hint}</span>}
      </div>
    </div>
  );
}
