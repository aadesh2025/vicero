"use client";

import { useId, useLayoutEffect, useRef, useState } from "react";
import { niceTicks } from "@/lib/activity-chart-math";

export interface ActivityBar {
  /** `bucket_start` from the API — used only as a React key. */
  key: string;
  /** X-axis label, e.g. "Sep 28" / "Sep 22" / "Sep", or "Today"/"This week"/"This month". */
  label: string;
  value: number;
  ariaLabel: string;
}

const PAD = { top: 30, right: 8, bottom: 18, left: 28 };

/**
 * The dashboard "Activity" bar chart (docs/20 §9.3.2, ADR-101), styled after
 * designreference/ACTIVITYNEW DAHSBOARDDESING.png: striped neutral bars, one solid-gradient
 * selected bar carrying its value and a delta pill, dotted gridlines, animated growth on load.
 *
 * Purely presentational — the caller owns period/metric state, data fetching and formatting.
 */
export function ActivityBars({
  bars,
  selectedIndex,
  onSelect,
  format,
  selectedDelta,
  height = 220,
  ariaLabel,
}: {
  bars: ActivityBar[];
  selectedIndex: number;
  onSelect: (i: number) => void;
  format: (n: number) => string;
  /** Delta pill for the selected bar vs. the previous one, e.g. "▲ +12%" / "▼ −5%" / "New". */
  selectedDelta: { label: string; tone: "up" | "down" | "new" } | null;
  height?: number;
  ariaLabel: string;
}) {
  const uid = useId().replace(/:/g, "");
  const wrapRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(720);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const n = bars.length;
  const innerW = w - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const max = Math.max(0, ...bars.map((b) => b.value));
  const ticks = niceTicks(max, 5);
  const topTick = ticks[ticks.length - 1] || 1;

  const gap = n > 0 ? (innerW / n) * 0.3 : 0;
  const barW = n > 0 ? innerW / n - gap : 0;
  const STUB = 6;
  const valueToHeight = (v: number) => (v <= 0 ? STUB : Math.max(STUB, (innerH * v) / topTick));

  function handleKeyDown(e: React.KeyboardEvent) {
    if (n === 0) return;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      onSelect(Math.max(0, selectedIndex - 1));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      onSelect(Math.min(n - 1, selectedIndex + 1));
    }
  }

  if (n === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg text-sm font-semibold text-faint" style={{ height }}>
        No activity in this period yet.
      </div>
    );
  }

  return (
    <div ref={wrapRef} className="relative" role="group" aria-label={ariaLabel} tabIndex={0} onKeyDown={handleKeyDown}>
      <svg width={w} height={height} className="overflow-visible">
        <defs>
          <pattern id={`${uid}-stripe`} width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="rgb(var(--bar-fill))" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="rgb(var(--bar-stripe))" strokeWidth="2" />
          </pattern>
          <linearGradient id={`${uid}-selected`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--bar-gradient-from))" />
            <stop offset="100%" stopColor="rgb(var(--bar-gradient-to))" />
          </linearGradient>
        </defs>

        {ticks.map((t, i) => {
          const y = PAD.top + innerH - (innerH * t) / topTick;
          return (
            // Index, not value: a quiet chart's ticks are all 0 (niceTicks(0, 5)), which
            // duplicate-keyed on the value itself and threw "two children with the same key".
            <g key={i}>
              <line
                x1={PAD.left}
                x2={w - PAD.right}
                y1={y}
                y2={y}
                stroke="rgb(var(--chart-grid))"
                strokeDasharray={i === 0 ? undefined : "2 5"}
              />
              <text x={PAD.left - 8} y={y} textAnchor="end" dominantBaseline="middle" className="tabular-nums" style={{ fill: "rgb(var(--chart-axis))", fontSize: 10, fontWeight: 600 }}>
                {format(t)}
              </text>
            </g>
          );
        })}

        {bars.map((b, i) => {
          const h = valueToHeight(b.value);
          const x = PAD.left + i * (barW + gap) + gap / 2;
          const y = PAD.top + innerH - h;
          const selected = i === selectedIndex;
          const rx = Math.min(12, barW / 2);
          // Grows in from the baseline on mount; `motion-reduce` drops it to an instant show.
          const growClass = "origin-bottom animate-bar-grow motion-reduce:animate-none";

          return (
            <g
              key={b.key}
              role="img"
              aria-label={b.ariaLabel}
              className="cursor-pointer"
              onClick={() => onSelect(i)}
              onMouseEnter={() => onSelect(i)}
            >
              <rect
                x={x}
                y={y}
                width={Math.max(1, barW)}
                height={h}
                rx={rx}
                fill={selected ? `url(#${uid}-selected)` : `url(#${uid}-stripe)`}
                stroke={selected ? "none" : "rgb(var(--bar-border))"}
                className={growClass}
                style={{ transformBox: "fill-box" }}
              />
              {!selected && (
                <rect
                  x={x}
                  y={y}
                  width={Math.max(1, barW)}
                  height={2}
                  rx={1}
                  fill="rgb(var(--bar-cap))"
                  className={growClass}
                  style={{ transformBox: "fill-box" }}
                />
              )}
              {selected && b.value > 0 && (
                <text
                  x={x + barW / 2}
                  y={y + h - 10}
                  textAnchor="middle"
                  className="tabular-nums"
                  style={{ fill: "rgb(var(--on-accent))", fontSize: 12, fontWeight: 800 }}
                >
                  {format(b.value)}
                </text>
              )}
              {selected && selectedDelta && (
                <foreignObject x={x + barW / 2 - 28} y={Math.max(0, y - 28)} width={56} height={22}>
                  <div
                    className={
                      "flex h-[20px] items-center justify-center rounded-full px-1.5 text-[10px] font-extrabold text-white " +
                      (selectedDelta.tone === "up"
                        ? "bg-success"
                        : selectedDelta.tone === "down"
                          ? "bg-error"
                          : "bg-accent")
                    }
                  >
                    {selectedDelta.label}
                  </div>
                </foreignObject>
              )}
              <text
                x={x + barW / 2}
                y={height - 6}
                textAnchor="middle"
                className="tabular-nums"
                style={{ fill: "rgb(var(--chart-axis))", fontSize: 11, fontWeight: 600 }}
              >
                {b.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
