/**
 * Pure date/number math for the dashboard "Activity" bar chart (docs/20 §9.3.2, ADR-101).
 * No React, no fetch — kept separate so the bucketing/tick/delta logic is vitest-able without
 * mounting the chart.
 */

export type Granularity = "day" | "week" | "month";
export type Period = "daily" | "weekly" | "monthly" | "range";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** `YYYY-MM-DD` in the date's own local fields — never `toISOString()`, which shifts to UTC
 *  and can print the wrong day for any date not at local midnight in UTC+ zones. */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

/** Monday of the week containing `d` — matches the backend's `day - weekday()` bucketing. */
export function weekStart(d: Date): Date {
  const day = d.getDay(); // 0 = Sunday .. 6 = Saturday
  const diff = (day === 0 ? -6 : 1) - day;
  return addDays(startOfDay(d), diff);
}

export function monthStart(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

export function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

/** The local bucket a date falls into, as the same `YYYY-MM-DD` key the backend returns for
 *  `bucket_start` — so the frontend can find "today's bar" in the response by string equality. */
export function bucketKey(d: Date, granularity: Granularity): string {
  if (granularity === "day") return isoDate(d);
  if (granularity === "week") return isoDate(weekStart(d));
  return isoDate(monthStart(d));
}

/** Range → granularity: Daily/Weekly/Monthly force their own granularity; a custom Range picks
 *  the coarsest granularity that still keeps the bar count reasonable. */
export function granularityForPeriod(period: Period, from: Date, to: Date): Granularity {
  if (period === "daily") return "day";
  if (period === "weekly") return "week";
  if (period === "monthly") return "month";
  const spanDays = Math.round((startOfDay(to).getTime() - startOfDay(from).getTime()) / 86_400_000) + 1;
  if (spanDays <= 31) return "day";
  if (spanDays <= 182) return "week";
  return "month";
}

/** Default `[from, to]` for the three fixed periods — Range supplies its own via the date
 *  picker, so it isn't handled here. */
export function defaultRangeFor(period: "daily" | "weekly" | "monthly", now: Date): { from: Date; to: Date } {
  const to = startOfDay(now);
  if (period === "daily") return { from: addDays(to, -13), to }; // 14 days inclusive
  if (period === "weekly") return { from: addDays(weekStart(to), -7 * 11), to }; // 12 weeks inclusive
  return { from: addMonths(monthStart(to), -11), to }; // 12 months inclusive
}

/** Exactly `count` "nice" tick values from 0 up to a round number at or above `max`, e.g.
 *  niceTicks(348, 5) -> [0, 100, 200, 300, 400]. `max <= 0` still returns `count` zeros so a
 *  quiet chart draws a flat axis instead of failing on log10(0). */
export function niceTicks(max: number, count = 5): number[] {
  if (max <= 0) return Array.from({ length: count }, () => 0);
  const rawStep = max / (count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const residual = rawStep / magnitude;
  const niceResidual = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 2.5 ? 2.5 : residual <= 5 ? 5 : 10;
  const step = niceResidual * magnitude;
  return Array.from({ length: count }, (_, i) => Math.round(step * i));
}

export interface Delta {
  /** Percent change, e.g. 12 for "▲ +12%". 0 when `isNew`. */
  pct: number;
  /** `previous` was 0 (or negative): a percentage would be infinite/undefined — show "New". */
  isNew: boolean;
}

/** Percent change from `previous` to `current`, guarding the divide-by-zero case a brand-new
 *  bucket/org hits on day one. */
export function deltaPct(current: number, previous: number): Delta {
  if (previous <= 0) return { pct: 0, isNew: current > 0 };
  return { pct: ((current - previous) / previous) * 100, isNew: false };
}
