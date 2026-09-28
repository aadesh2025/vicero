import { useId } from "react";

/** Point on a circle at `angleDeg` (0 = 3 o'clock, 90 = 6 o'clock, going clockwise — the
 *  convention that makes a left-to-right semicircle sweep read as 180 -> 360). */
function polar(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

/** An arc from `startDeg` to `endDeg` (clockwise), as an SVG path `d`. */
function arcPath(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  const start = polar(cx, cy, r, startDeg);
  const end = polar(cx, cy, r, endDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${r} ${r} 0 ${largeArc} 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
}

const CX = 110;
const CY = 108;
const R = 86;
const STROKE = 16;

/**
 * A semicircle "speedometer" gauge (docs/20 §9.3.1 — the reference's "Time Off 10 OUT OF 20"
 * card). Track is a full 180° half-circle; the fill sweeps clockwise from the left end in
 * proportion to `value / max`. Purely presentational — callers own the number, the "out of"
 * line and the gradient's exact colours.
 */
export function Gauge({
  value,
  max,
  ariaLabel,
  className,
}: {
  value: number;
  max: number;
  ariaLabel: string;
  className?: string;
}) {
  const id = `gauge-${useId().replace(/:/g, "")}`;
  const pct = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const fillEnd = 180 + 180 * pct;

  return (
    <svg
      viewBox="0 0 220 120"
      className={className}
      role="img"
      aria-label={ariaLabel}
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="rgb(var(--accent))" />
          <stop offset="100%" stopColor="rgb(var(--gauge-to))" />
        </linearGradient>
      </defs>
      <path
        d={arcPath(CX, CY, R, 180, 360)}
        fill="none"
        stroke="rgb(var(--surface-3))"
        strokeWidth={STROKE}
        strokeLinecap="round"
      />
      {pct > 0 && (
        <path
          d={arcPath(CX, CY, R, 180, fillEnd)}
          fill="none"
          stroke={`url(#${id})`}
          strokeWidth={STROKE}
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}
