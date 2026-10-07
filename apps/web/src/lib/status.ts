/** One place that says which colour a status is (docs/20 §4.4, §8).
 *
 * Colour follows *meaning*, not the raw value: green is "worked / healthy", amber is "needs a
 * person soon", red is "failed or blocked", blue is "in progress", purple is "the AI did this",
 * grey is "inactive or unknown". The API, the mock layer and the UI each have their own words
 * for the same thing (`published` / `live`, `active` / `open`), so they all fold into this map
 * rather than each screen keeping its own switch.
 */

export type Tone = "success" | "info" | "warn" | "error" | "ai" | "neutral";

export const STATUS_TONE: Record<string, Tone> = {
  // success
  resolved: "success",
  connected: "success",
  published: "success",
  live: "success",
  active: "success",
  completed: "success",
  success: "success",
  verified: "success",
  ready: "success",
  paid: "success",
  // in progress
  open: "info",
  in_progress: "info",
  building: "info",
  running: "info",
  processing: "info",
  syncing: "info",
  info: "info",
  // needs attention soon
  handoff: "warn",
  pending: "warn",
  requested: "warn",
  draft: "warn",
  paused: "warn",
  trial_ending: "warn",
  needs_review: "warn",
  queued: "warn",
  // failed / blocked
  unanswered: "error",
  failed: "error",
  rejected: "error",
  error: "error",
  disconnected_error: "error",
  plan_limit_block: "error",
  expired: "error",
  trial_expired: "error",
  // the AI
  ai_resolved: "ai",
  ai_suggested: "ai",
  agent: "ai",
  // inactive / unknown
  archived: "neutral",
  disabled: "neutral",
  not_connected: "neutral",
  closed: "neutral",
  unknown: "neutral",
};

/** Tone for any status string; anything unrecognised is neutral rather than an error. */
export function statusTone(status: string | null | undefined): Tone {
  return STATUS_TONE[(status ?? "").toLowerCase()] ?? "neutral";
}

/** Words the product uses where they differ from the raw value. */
const LABEL: Record<string, string> = {
  published: "Live",
  handoff: "Needs human",
  in_progress: "In progress",
  ai_resolved: "AI resolved",
  ai_suggested: "AI suggested",
  plan_limit_block: "Plan limit",
  not_connected: "Not connected",
  disconnected_error: "Connection error",
  trial_ending: "Trial ending",
  trial_expired: "Trial ended",
  needs_review: "Needs review",
};

/** "in_progress" -> "In progress"; falls back to sentence-casing the raw value. */
export function statusLabel(status: string | null | undefined): string {
  const key = (status ?? "").toLowerCase();
  if (!key) return "Unknown";
  if (LABEL[key]) return LABEL[key];
  const spaced = key.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
