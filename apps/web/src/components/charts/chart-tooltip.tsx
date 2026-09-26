import { cn } from "@/lib/utils";

export interface TooltipRow {
  label: string;
  value: string;
  /** CSS colour of the series dot. */
  color: string;
}

/** The hover card shared by every chart: heading, then one row per series. Positioned by the
 *  chart that owns it (it is `absolute`), never interactive. */
export function ChartTooltip({
  heading,
  rows,
  className,
  style,
}: {
  heading: string;
  rows: TooltipRow[];
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      role="status"
      className={cn(
        "pointer-events-none absolute z-10 min-w-[8.5rem] rounded-lg border border-border bg-surface px-3 py-2 shadow-pop dark:bg-surface-2",
        className,
      )}
      style={style}
    >
      <div className="text-[11px] font-bold text-faint">{heading}</div>
      <ul className="mt-1 space-y-0.5">
        {rows.map((r) => (
          <li key={r.label} className="flex items-center gap-2 text-xs font-semibold text-text">
            <span aria-hidden className="size-2 rounded-full" style={{ backgroundColor: r.color }} />
            <span className="text-muted">{r.label}</span>
            <span className="ml-auto pl-3 tabular-nums">{r.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
