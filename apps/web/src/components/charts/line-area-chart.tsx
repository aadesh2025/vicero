"use client";

import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { compact } from "@/lib/utils";
import { ChartTooltip } from "./chart-tooltip";
import { seriesColor, type SeriesColor } from "./colors";
import { smoothPath } from "./smooth-path";

export interface LineSeries {
  key: string;
  label: string;
  color: SeriesColor;
  /** One value per x position; every series must be the same length as `xLabels`. */
  values: number[];
}

const PAD = { top: 16, right: 12, bottom: 26, left: 12 };

/**
 * Line + area chart (docs/20 §4.5): 2.5px monotone line, a vertical-gradient area under each
 * series, dotted gridlines, and on hover a guide line, a ringed dot per series and a tooltip.
 *
 * All series share one y scale, so two lines of the same unit are directly comparable. The
 * geometry is computed in pixels from the measured width (not a scaled viewBox), so strokes
 * stay 2.5px at any size.
 */
export function LineAreaChart({
  xLabels,
  tooltipHeadings,
  series,
  format = (n) => compact(n),
  height = 240,
  ariaLabel,
  emptyText = "No data yet.",
}: {
  /** Axis text per x position (already formatted). */
  xLabels: string[];
  /** Tooltip heading per x position; defaults to `xLabels`. */
  tooltipHeadings?: string[];
  series: LineSeries[];
  format?: (n: number) => string;
  height?: number;
  ariaLabel: string;
  emptyText?: string;
}) {
  const uid = useId().replace(/:/g, "");
  const wrapRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(720);
  const [hover, setHover] = useState<number | null>(null);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const n = xLabels.length;
  const innerW = w - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;

  const geometry = useMemo(() => {
    if (n === 0) return null;
    const peak = Math.max(0, ...series.flatMap((s) => s.values));
    // Headroom so the line never touches the top edge; an all-zero chart gets a nominal scale
    // and draws flat along the baseline instead of dividing by zero.
    const scale = (peak > 0 ? peak : 1) * 1.15;
    const x = (i: number) => PAD.left + (innerW * i) / Math.max(1, n - 1);
    const y = (v: number) => PAD.top + innerH - (innerH * v) / scale;
    const base = PAD.top + innerH;
    return series.map((s) => {
      const pts = s.values.map((v, i) => ({ x: x(i), y: y(v), v }));
      const line = smoothPath(pts);
      const area = line ? `${line} L${pts[pts.length - 1].x.toFixed(1)},${base} L${pts[0].x.toFixed(1)},${base} Z` : "";
      return { pts, line, area };
    });
  }, [series, n, innerW, innerH]);

  if (!geometry) {
    return (
      <div
        className="relative flex items-center justify-center rounded-lg text-sm font-semibold text-faint"
        style={{ height }}
      >
        <svg aria-hidden className="absolute inset-0 size-full">
          {[0.2, 0.4, 0.6, 0.8].map((t) => (
            <line key={t} x1="0" x2="100%" y1={`${t * 100}%`} y2={`${t * 100}%`} stroke="rgb(var(--chart-grid))" strokeDasharray="2 5" />
          ))}
        </svg>
        <span className="relative">{emptyText}</span>
      </div>
    );
  }

  const active = hover ?? n - 1;
  const anchor = geometry[0].pts[active];
  const tipX = Math.max(80, Math.min(w - 80, anchor.x));
  const highest = Math.min(...geometry.map((g) => g.pts[active].y));
  const flipDown = highest < 90;
  const step = Math.max(1, Math.ceil(n / 6));
  const tickIdx = [...new Set([...Array.from({ length: n }, (_, i) => i).filter((i) => i % step === 0), n - 1])];

  return (
    <div ref={wrapRef} className="relative">
      <svg
        width={w}
        height={height}
        className="overflow-visible"
        role="img"
        aria-label={ariaLabel}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const i = Math.round(((e.clientX - rect.left - PAD.left) / innerW) * (n - 1));
          setHover(Math.max(0, Math.min(n - 1, i)));
        }}
      >
        <defs>
          {series.map((s) => (
            <linearGradient key={s.key} id={`${uid}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={seriesColor(s.color)} style={{ stopOpacity: "var(--chart-fill)" }} />
              <stop offset="100%" stopColor={seriesColor(s.color)} stopOpacity="0" />
            </linearGradient>
          ))}
        </defs>

        {[0.25, 0.5, 0.75].map((t) => {
          const yy = PAD.top + innerH * t;
          return <line key={t} x1={PAD.left} x2={w - PAD.right} y1={yy} y2={yy} stroke="rgb(var(--chart-grid))" strokeDasharray="2 5" />;
        })}
        <line x1={PAD.left} x2={w - PAD.right} y1={PAD.top + innerH} y2={PAD.top + innerH} stroke="rgb(var(--chart-grid))" />

        {geometry.map((g, si) => (
          <g key={series[si].key}>
            <path d={g.area} fill={`url(#${uid}-${series[si].key})`} />
            <path
              d={g.line}
              fill="none"
              stroke={seriesColor(series[si].color)}
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          </g>
        ))}

        <line x1={anchor.x} x2={anchor.x} y1={PAD.top} y2={PAD.top + innerH} stroke="rgb(var(--border-strong))" strokeDasharray="3 3" />
        {geometry.map((g, si) => (
          <circle
            key={series[si].key}
            cx={g.pts[active].x}
            cy={g.pts[active].y}
            r={4.5}
            fill={seriesColor(series[si].color)}
            stroke="rgb(var(--surface))"
            strokeWidth={2.5}
          />
        ))}

        {tickIdx.map((i) => (
          <text
            key={i}
            x={geometry[0].pts[i].x}
            y={height - 8}
            textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
            className="tabular-nums"
            style={{ fill: "rgb(var(--chart-axis))", fontSize: 11, fontWeight: 600 }}
          >
            {xLabels[i]}
          </text>
        ))}
      </svg>

      <ChartTooltip
        heading={(tooltipHeadings ?? xLabels)[active]}
        rows={series.map((s) => ({ label: s.label, value: format(s.values[active]), color: seriesColor(s.color) }))}
        style={{ left: tipX, top: flipDown ? highest + 14 : highest - 12, transform: `translateX(-50%)${flipDown ? "" : " translateY(-100%)"}` }}
      />
    </div>
  );
}
