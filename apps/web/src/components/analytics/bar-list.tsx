import { compact } from "@/lib/utils";

export function BarList({
  items,
  format = (n) => compact(n),
}: {
  /**
   * `label` is display text and is NOT unique — two agents may share a name, and
   * two channel keys may map to one label. Pass `id` wherever the source row has
   * a stable identifier; it is what keys the row.
   */
  items: {
    id?: string;
    label: string;
    value: number;
    /** CSS colour of the bar (a series token or a channel tone). Defaults to the accent. */
    color?: string;
  }[];
  format?: (n: number) => string;
}) {
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="space-y-3">
      {items.map((item, i) => (
        <div key={item.id ?? `${item.label}:${i}`} className="space-y-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="font-semibold text-muted">{item.label}</span>
            <span className="text-xs font-bold tabular-nums text-text">{format(item.value)}</span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-surface-3">
            <div
              className="h-full rounded"
              style={{ width: `${(item.value / max) * 100}%`, backgroundColor: item.color ?? "rgb(var(--accent))" }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
