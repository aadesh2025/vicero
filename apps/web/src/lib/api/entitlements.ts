"use client";

import { api } from "./client";
import type { PlanFeatures, PlanLimits } from "./billing";

// GET /v1/me/entitlements (docs/22 §8.1) — what the active org may do right now, plus usage.
// For rendering only, never a security decision: the server re-enforces every limit at the
// point of mutation. Deliberately carries no payment information — payment_state and
// billing_cycles are staff-only (docs/22 §5.1) and never appear in this response at all.

export interface UsageCounts {
  agents: number;
  messages: number;
  knowledge_bases: number;
  documents: number;
  storage_bytes: number;
  workflows: number;
  tools: number;
  webhooks: number;
  team_members: number;
}

export interface MessagesInfo {
  used: number;
  /** The plan's own cap, display only. `null` = unlimited. */
  plan_limit: number | null;
  /** Extra messages bought this period (packs). */
  extra: number;
  /** The cap actually enforced (plan_limit + extra) — always use this, never plan_limit, to
   *  decide or show whether the org is near/at its limit (docs/22 §7, the inbound.py money bug
   *  this mirrors). `null` = unlimited. */
  effective_limit: number | null;
  period_end: string | null;
  unanswered: number;
}

export interface Entitlements {
  plan: string;
  /** trial | trial_expired | legacy | starter | pro | business | plan_expired. */
  status: string;
  plan_expires_at: string | null;
  /** Whole days remaining until trial_ends_at/plan_expires_at. `null` when nothing is
   *  counting down. Only shown to the customer for a running trial (docs/22 §16.3 — a paid
   *  plan's admin-managed expiry is never surfaced as a countdown). */
  days_left: number | null;
  limits: PlanLimits;
  usage: UsageCounts;
  messages: MessagesInfo;
  features: PlanFeatures;
  channels: string[] | null;
  contact_url: string;
}

export const getEntitlements = () => api<Entitlements>("/v1/me/entitlements", { orgScoped: true });
