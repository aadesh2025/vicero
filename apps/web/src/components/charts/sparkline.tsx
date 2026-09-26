import { useId } from "react";
import { seriesColor, type SeriesColor } from "./colors";
import { smoothPath } from "./smooth-path";

/** A tiny trend line for stat cards (80x32 by default). Decorative: the number beside it is
 *  the information, so the SVG is hidden from assistive tech. */
export function Sparkline({
  values,
  color = "accent",
  width = 80,
  height = 32,
  className,
}: {
  values: number[];
  color?: SeriesColor;
  width?: number;
  height?: number;
  className?: string;
}) {
  const uid = useId().replace(/:/g, "");
  if (values.length < 2) return <span aria-hidden className={className} style={{ width, height, display: "inline-block" }} />;

  const pad = 3;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => ({
    x: pad + ((width - pad * 2) * i) / (values.length - 1),
    y: pad + (height - pad * 2) * (1 - (v - min) / span),
  }));
  const line = smoothPath(pts);
  const area = `${line} L${pts[pts.length - 1].x.toFixed(1)},${height} L${pts[0].x.toFixed(1)},${height} Z`;
  const c = seriesColor(color);

  return (
    <svg aria-hidden width={width} height={height} className={className}>
      <defs>
        <linearGradient id={`${uid}-f`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={c} style={{ stopOpacity: "var(--chart-fill)" }} />
          <stop offset="100%" stopColor={c} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${uid}-f)`} />
      <path d={line} fill="none" stroke={c} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
