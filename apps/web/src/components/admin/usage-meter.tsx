import { cn } from "@/lib/utils";

/** `8,412 / 10,000` + a bar that turns amber at 80% and red at 100% (docs/22 §9.1).
 *  `limit == null` renders "used / Unlimited" with no bar — there is nothing to fill toward. */
export function UsageMeter({
  used,
  limit,
  label,
  format = (n) => n.toLocaleString(),
}: {
  used: number;
  limit: number | null;
  label: string;
  format?: (n: number) => string;
}) {
  if (limit === null) {
    return (
      <div className="whitespace-nowrap font-mono text-xs text-muted">
        {format(used)} <span className="text-faint">/ Unlimited</span>
      </div>
    );
  }
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 100;
  return (
    <div className="min-w-[110px]">
      <div className="whitespace-nowrap font-mono text-xs text-muted">
        {format(used)} <span className="text-faint">/ {format(limit)}</span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        className="mt-1 h-1.5 overflow-hidden rounded bg-surface-3"
      >
        <div
          className={cn("h-full rounded", pct >= 100 ? "bg-error" : pct >= 80 ? "bg-warn" : "bg-accent")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/** Bytes as `1.2 GB` / `500 MB`. */
export function formatBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}
