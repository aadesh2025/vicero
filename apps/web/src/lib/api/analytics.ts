"use client";

import { api } from "./client";

export interface ChannelBucket {
  channel: string;
  conversations: number;
  messages: number;
  tokens_prompt: number;
  tokens_completion: number;
  cost_micros: number;
  handoff_rate: number;
  resolution_rate: number;
}

export interface Overview {
  conversations: number;
  messages: number;
  users: number;
  tokens_prompt: number;
  tokens_completion: number;
  cost_micros: number;
  handoff_rate: number;
  resolution_rate: number;
  /** One bucket per channel, including connected ones with no traffic yet. */
  by_channel: ChannelBucket[];
}

export interface UsageBucket {
  key: string;
  tokens_prompt: number;
  tokens_completion: number;
  requests: number;
  cost_micros: number;
}

/**
 * One calendar day of activity — the chart's data source.
 *
 * Every day in the range is present, including quiet ones. `UsageBucket` with
 * `group_by: "day"` is sparse, and plotting a sparse series by array index is what drew a
 * straight line through a month with three busy days.
 */
export interface DayPoint {
  /** ISO date, `YYYY-MM-DD`. */
  date: string;
  /** Conversations *started* that day — not assistant message count. */
  conversations: number;
  messages: number;
  tokens_prompt: number;
  tokens_completion: number;
  cost_micros: number;
}

/** One agent's traffic. Distinct from `AgentPerformanceBucket`, which is one teammate's. */
export interface AgentBucket {
  agent_id: string;
  name: string;
  status: string;
  /** Deleted agents keep their history so the rows still sum to the overview. */
  deleted: boolean;
  conversations: number;
  messages: number;
  tokens_prompt: number;
  tokens_completion: number;
  cost_micros: number;
  handoff_rate: number;
  resolution_rate: number;
  /** Null when the agent has never been messaged. */
  last_active_at: string | null;
}

export interface LatencyStats {
  count: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
}

export interface AgentPerformanceBucket {
  user_id: string;
  name: string;
  handoffs: number;
  /** Null when there's nothing to average yet — not a zero-millisecond response. */
  avg_first_response_ms: number | null;
  avg_resolution_ms: number | null;
  closed_count: number;
}

export interface QuestionCount {
  question: string;
  count: number;
}

export interface AnalyticsParams {
  agent_id?: string;
  from?: string;
  to?: string;
  group_by?: string;
  /** Narrow every metric to one channel, e.g. `instagram`. */
  channel?: string;
}

function qs(params: AnalyticsParams): string {
  const entries = Object.entries(params).filter(([, v]) => v != null && v !== "");
  return entries.length ? "?" + entries.map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join("&") : "";
}

export function getOverview(p: AnalyticsParams = {}) {
  return api<Overview>(`/v1/analytics/overview${qs(p)}`, { orgScoped: true });
}

export interface TodaySnapshot {
  conversations: number;
  resolved_by_ai: number;
  handed_to_human: number;
  unanswered: number;
}

/** The dashboard "Today" gauge (ADR-100). `start`/`end` are UTC instants for whatever the
 *  caller means by "today" — this app has no stored org timezone yet, so the browser's own
 *  midnight-to-now is what callers should pass. */
export function getToday(start: Date, end: Date) {
  const q = `?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;
  return api<TodaySnapshot>(`/v1/analytics/today${q}`, { orgScoped: true });
}

export function getUsage(p: AnalyticsParams & { group_by?: "day" | "provider" | "model" | "channel" } = {}) {
  return api<UsageBucket[]>(`/v1/analytics/usage${qs(p)}`, { orgScoped: true });
}

/** Daily activity for charting: one point per day in range, quiet days included. */
export function getSeries(p: AnalyticsParams = {}) {
  return api<DayPoint[]>(`/v1/analytics/series${qs(p)}`, { orgScoped: true });
}

/** Per-*bot* traffic. `getAgentPerformance` below is per-*teammate* — a different report. */
export function getByAgent(p: AnalyticsParams = {}) {
  return api<AgentBucket[]>(`/v1/analytics/by-agent${qs(p)}`, { orgScoped: true });
}

/** Per-teammate inbox workload. Reports on people, not channels. */
export function getAgentPerformance(p: AnalyticsParams = {}) {
  return api<AgentPerformanceBucket[]>(`/v1/analytics/agents${qs(p)}`, { orgScoped: true });
}

export function getLatency(p: AnalyticsParams = {}) {
  return api<LatencyStats>(`/v1/analytics/latency${qs(p)}`, { orgScoped: true });
}

export function getTopQuestions(p: AnalyticsParams = {}) {
  return api<QuestionCount[]>(`/v1/analytics/top-questions${qs(p)}`, { orgScoped: true });
}

export function getUnanswered(p: AnalyticsParams = {}) {
  return api<QuestionCount[]>(`/v1/analytics/unanswered${qs(p)}`, { orgScoped: true });
}
