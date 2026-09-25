"use client";

import { api, ApiError } from "./client";

/** `GET /v1/orgs/{id}/plan` — everything the trial banner, meter and locked states need. */
export interface PlanStatus {
  plan: string;
  status: "trial" | "trial_expired" | "legacy";
  trial_ends_at: string | null;
  days_left: number | null;
  expired_reason: "time" | "messages" | null;
  messages_used: number;
  messages_limit: number | null;
  messages_remaining: number | null;
  /** Visitor messages saved but not answered because the plan ran out. */
  unanswered_messages: number;
  agents_used: number;
  max_agents: number | null;
  can_create_agent: boolean;
  features: { workflows: boolean; n8n: boolean; tool_calling: boolean };
}

export type PlanFeature = keyof PlanStatus["features"];

export function getPlan(orgId: string) {
  return api<PlanStatus>(`/v1/orgs/${orgId}/plan`);
}

/** True for the 402 every server-side plan gate returns. */
export function isPlanLimit(err: unknown): err is ApiError {
  return err instanceof ApiError && err.status === 402 && err.code === "plan_limit";
}

export const UPGRADE_PATH = "/billing/upgrade";
