"use client";

import { api } from "./client";

// Client-visible automations (docs/26). Everything here is read-only for a client, apart from asking for a new
// one. Nothing in these shapes exposes n8n: no workflow id, no webhook path, no raw error text.

export type RunStatus = "success" | "failed" | "running";
export type AutomationStatus = "active" | "paused";
export type RequestStatus = "requested" | "building" | "active" | "paused" | "rejected";

export interface ApiRun {
  id: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  /** A short plain-language line: the reason for a failure, or "Completed". */
  message: string;
  /** True when the run happened after the plan's monthly run limit was reached. */
  over_cap: boolean;
}

export interface ApiRunsPage {
  items: ApiRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiAutomationCard {
  id: string;
  name: string;
  status: AutomationStatus;
  agent_id: string | null;
  agent_name: string | null;
  last_run_at: string | null;
  last_status: RunStatus | null;
  runs_this_month: number;
  /** 0..1, or null until a run has finished this month. */
  success_rate: number | null;
}

export interface ApiAutomationDetail extends ApiAutomationCard {
  runs_total: number;
  success_this_month: number;
  failed_this_month: number;
  last_run: ApiRun | null;
  last_failure: ApiRun | null;
}

export interface ApiAutomationUsage {
  plan: string;
  automations_limit: number | null;
  automations_used: number;
  runs_limit: number | null;
  runs_this_month: number;
  /** False when the plan has no automations at all. */
  included: boolean;
  /** False when the plan allows them but every slot is taken. */
  can_request: boolean;
}

export interface ApiAutomationRequest {
  id: string;
  description: string;
  agent_id: string | null;
  status: RequestStatus;
  staff_note: string | null;
  created_at: string;
}

export const listAutomations = () => api<ApiAutomationCard[]>("/v1/automations", { orgScoped: true });
export const getAutomationUsage = () => api<ApiAutomationUsage>("/v1/automations/usage", { orgScoped: true });
export const listAutomationRequests = () =>
  api<ApiAutomationRequest[]>("/v1/automations/requests", { orgScoped: true });
export const getAutomation = (id: string) =>
  api<ApiAutomationDetail>(`/v1/automations/${id}`, { orgScoped: true });

export function listAutomationRuns(id: string, opts: { status?: RunStatus; page?: number; pageSize?: number } = {}) {
  const q = new URLSearchParams();
  if (opts.status) q.set("status", opts.status);
  q.set("page", String(opts.page ?? 1));
  q.set("page_size", String(opts.pageSize ?? 25));
  return api<ApiRunsPage>(`/v1/automations/${id}/runs?${q}`, { orgScoped: true });
}

export const requestAutomation = (body: { description: string; agent_id?: string }) =>
  api<ApiAutomationRequest>("/v1/automations/requests", { method: "POST", body, orgScoped: true });

// ── platform staff ───────────────────────────────────────────────────────────
export interface ApiStaffRequest extends ApiAutomationRequest {
  organization_id: string;
  organization_name: string;
  requested_by_email: string | null;
}

export interface ApiStaffAutomation extends ApiAutomationCard {
  organization_id: string;
  organization_name: string;
  n8n_workflow_id: string;
  webhook_path: string | null;
  has_config: boolean;
}

export const listStaffRequests = (status?: string) =>
  api<ApiStaffRequest[]>(`/v1/admin/automation-requests${status ? `?status=${status}` : ""}`);
export const updateStaffRequest = (id: string, body: { status: "requested" | "building" | "rejected"; staff_note?: string }) =>
  api<ApiAutomationRequest>(`/v1/admin/automation-requests/${id}`, { method: "PATCH", body });
export const listStaffAutomations = () => api<ApiStaffAutomation[]>("/v1/admin/automation-registry");
export const registerAutomation = (body: {
  organization_id: string;
  agent_id?: string;
  request_id?: string;
  n8n_workflow_id: string;
  webhook_path?: string;
  name: string;
}) => api<ApiStaffAutomation>("/v1/admin/automation-registry", { method: "POST", body });
export const setAutomationStatus = (id: string, status: AutomationStatus) =>
  api<ApiStaffAutomation>(`/v1/admin/automation-registry/${id}`, { method: "PATCH", body: { status } });
