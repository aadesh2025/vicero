"use client";

import { api } from "./client";

// Platform-staff console. These endpoints are org-agnostic (no X-Org-Id) and
// require `is_staff`; a non-staff token gets 403 from the API regardless of the UI.

export interface AdminOrg {
  id: string;
  name: string;
  slug: string | null;
  members: number;
  agents: number;
  created_at: string;
  deleted: boolean;

  // ── billing (docs/22 §8.2) ──────────────────────────────────────────────
  plan: string;
  /** trial | trial_expired | legacy | starter | pro | business | plan_expired. */
  status: string;
  plan_source: string;
  plan_expires_at: string | null;
  plan_note: string | null;
  messages_used: number;
  /** `null` = unlimited; already includes any packs bought this period. */
  messages_limit: number | null;
  unanswered_messages: number;
  storage_bytes_used: number;
  /** `null` = unlimited. */
  storage_bytes_limit: number | null;
  /** The current billing cycle's computed state — paid | pending | overdue | waived | null. */
  payment_state: string | null;
  owner_email: string | null;
}

/** `?status=` filter chips the admin org list accepts (docs/22 §8.2, §9.1). */
export type OrgStatusFilter =
  | "at_limit"
  | "near_limit"
  | "expiring"
  | "expired"
  | "payment_pending"
  | "overdue";

/** Plans staff may grant from the panel (docs/22 §4.2) — `legacy` for your own/demo orgs. */
export const GRANTABLE_PLANS = ["trial", "starter", "pro", "business", "legacy"] as const;
export type GrantablePlan = (typeof GRANTABLE_PLANS)[number];
export const PAID_PLANS = ["starter", "pro", "business"] as const;

export interface PlanGrant {
  id: string;
  action: string;
  from_plan: string | null;
  to_plan: string | null;
  expires_at: string | null;
  extra_messages: number | null;
  amount_usd_cents: number | null;
  note: string | null;
  actor_email: string | null;
  invoiced: boolean;
  created_at: string;
}

export interface BillingCycle {
  id: string;
  organization_id: string;
  organization_name: string | null;
  plan: string;
  period_start: string;
  period_end: string;
  amount_usd_cents: number;
  /** The raw stored value. */
  status: "pending" | "paid" | "waived";
  /** The computed value — adds `overdue` when a pending cycle's period_end has passed. */
  payment_state: "pending" | "paid" | "waived" | "overdue";
  paid_at: string | null;
  method: string | null;
  reference: string | null;
  note: string | null;
  created_at: string;
}

export interface OrgAdminDetail extends AdminOrg {
  recent_grants: PlanGrant[];
  billing_cycles: BillingCycle[];
}

export interface Pack {
  id: string;
  organization_id: string;
  organization_name: string | null;
  extra_messages: number | null;
  amount_usd_cents: number | null;
  note: string | null;
  invoiced: boolean;
  created_at: string;
}

export interface AdminUser {
  id: string;
  email: string;
  is_staff: boolean;
  is_active: boolean;
  orgs: number;
  created_at: string;
}

export interface OrgUsageRow {
  organization_id: string;
  name: string;
  tokens_prompt: number;
  tokens_completion: number;
  requests: number;
  cost_micros: number;
}

export interface PlatformUsage {
  organizations: number;
  users: number;
  agents: number;
  conversations: number;
  messages: number;
  tokens_prompt: number;
  tokens_completion: number;
  cost_micros: number;
  top_orgs: OrgUsageRow[];
}

export interface AdminHealth {
  database: boolean;
  redis: boolean;
  organizations: number;
  users: number;
  conversations: number;
  messages: number;
}

export interface FeatureFlag {
  key: string;
  enabled: boolean;
  description: string | null;
  updated_at: string;
}

/** One Vicero tool pointing at a workflow. */
export interface AutomationBinding {
  organization_slug: string;
  organization_name: string;
  agent_name: string | null;
  tool_name: string;
  enabled: boolean;
  mode: string | null;
}

export type AutomationOwnerKind = "org" | "internal" | "shared-template" | "untagged" | "unknown-org";

export interface AdminAutomation {
  id: string;
  name: string;
  active: boolean;
  tags: string[];
  owner: string;
  owner_kind: AutomationOwnerKind;
  organization_name: string | null;
  webhook_url: string | null;
  bindings: AutomationBinding[];
}

export interface AutomationsOverview {
  workflows: AdminAutomation[];
  /** Set when n8n is unreachable or keyless — distinct from "no workflows exist". */
  error: string | null;
}

export interface ListOrgsParams {
  q?: string;
  plan?: string;
  status?: OrgStatusFilter;
}

export function listAdminOrgs(params: ListOrgsParams = {}) {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.plan) qs.set("plan", params.plan);
  if (params.status) qs.set("status", params.status);
  const suffix = qs.toString();
  return api<AdminOrg[]>(`/v1/admin/orgs${suffix ? `?${suffix}` : ""}`);
}

export const getAdminOrg = (orgId: string) => api<OrgAdminDetail>(`/v1/admin/orgs/${orgId}`);

export interface GrantPlanInput {
  plan: string;
  /** `null`/omitted = no expiry. */
  expires_at?: string | null;
  note: string;
  payment_received?: boolean;
  method?: string | null;
  reference?: string | null;
}

export const grantPlan = (orgId: string, data: GrantPlanInput) =>
  api<AdminOrg>(`/v1/admin/orgs/${orgId}/plan`, { method: "POST", body: data });

export const revokePlan = (orgId: string, note: string) =>
  api<AdminOrg>(`/v1/admin/orgs/${orgId}/plan`, { method: "DELETE", body: { note } });

export const addPacks = (orgId: string, packs: number, note: string) =>
  api<AdminOrg>(`/v1/admin/orgs/${orgId}/messages`, { method: "POST", body: { packs, note } });

export function listBillingCycles(status?: BillingCycle["payment_state"]) {
  const suffix = status ? `?status=${status}` : "";
  return api<BillingCycle[]>(`/v1/admin/billing/cycles${suffix}`);
}

export interface MarkCyclePaidInput {
  method: string;
  reference?: string | null;
  note?: string | null;
  renew: boolean;
}

export const markCyclePaid = (cycleId: string, data: MarkCyclePaidInput) =>
  api<BillingCycle>(`/v1/admin/billing/cycles/${cycleId}/paid`, { method: "POST", body: data });

export const waiveCycle = (cycleId: string, note: string) =>
  api<BillingCycle>(`/v1/admin/billing/cycles/${cycleId}/waive`, { method: "POST", body: { note } });

export function listPacks(invoiced = false) {
  return api<Pack[]>(`/v1/admin/billing/packs?invoiced=${invoiced}`);
}

export const markPackInvoiced = (packId: string, invoiced: boolean) =>
  api<Pack>(`/v1/admin/billing/packs/${packId}`, { method: "PATCH", body: { invoiced } });
export const getAutomationsOverview = () => api<AutomationsOverview>("/v1/admin/automations");
export const listAdminUsers = () => api<AdminUser[]>("/v1/admin/users");
export const getPlatformUsage = () => api<PlatformUsage>("/v1/admin/usage");
export const getAdminHealth = () => api<AdminHealth>("/v1/admin/health");
export const listFeatureFlags = () => api<FeatureFlag[]>("/v1/admin/feature-flags");

export const upsertFeatureFlag = (key: string, enabled: boolean, description?: string | null) =>
  api<FeatureFlag>(`/v1/admin/feature-flags/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: { enabled, description: description ?? null },
  });
