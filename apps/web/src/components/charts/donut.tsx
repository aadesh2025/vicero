export interface DonutSegment {
  key: string;
  label: string;
  value: number;
  /** Any CSS colour, e.g. a series token or a channel tone's `dot`. */
  color: string;
}

/** Ring chart. Segments are drawn as arcs on one circle; `children` sits in the hole. The whole
 *  thing is one labelled image, since a screen reader gets nothing from individual arcs. */
export function Donut({
  segments,
  size = 148,
  thickness = 18,
  ariaLabel,
  children,
}: {
  segments: DonutSegment[];
  size?: number;
  thickness?: number;
  ariaLabel: string;
  children?: React.ReactNode;
}) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const r = (size - thickness) / 2;
  const circ = 2 * Math.PI * r;
  // A hairline gap between arcs so neighbours read as separate; skipped for a single segment.
  const gap = segments.filter((s) => s.value > 0).length > 1 ? 2 : 0;

  let offset = 0;
  return (
    <div className="relative inline-grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} role="img" aria-label={ariaLabel} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--surface-3))" strokeWidth={thickness} />
        {total > 0 &&
          segments.map((s) => {
            const len = (s.value / total) * circ;
            const dash = Math.max(0, len - gap);
            const el = (
              <circle
                key={s.key}
                cx={size / 2}
                cy={size / 2}
                r={r}
                fill="none"
                stroke={s.color}
                strokeWidth={thickness}
                strokeDasharray={`${dash} ${circ - dash}`}
                strokeDashoffset={-offset}
              />
            );
            offset += len;
            return s.value > 0 ? el : null;
          })}
      </svg>
      {children && <div className="absolute inset-0 grid place-items-center text-center">{children}</div>}
    </div>
  );
}
