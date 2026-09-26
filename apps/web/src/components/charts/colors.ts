/** Chart series colours (docs/20 §4.5). Series 1-5 in order: accent, success, ai, warn, teal.
 *  Teal exists for charts only; it is not a meaning colour and never appears in the UI chrome. */
export type SeriesColor = "accent" | "success" | "ai" | "warn" | "error" | "info" | "chart-5";

export const SERIES_ORDER: SeriesColor[] = ["accent", "success", "ai", "warn", "chart-5"];

/** CSS colour for a series; a variable, so it follows the theme. */
export function seriesColor(color: SeriesColor): string {
  return `rgb(var(--${color}))`;
}
