"use client";

import { api } from "./client";

// The public pricing table (docs/22 §8.1) — unauthenticated, rendered on /pricing and reused
// here so the admin panel never hardcodes a price or pack rate.

export interface PlanLimits {
  workspaces: number | null;
  agents: number | null;
  messages: number | null;
  knowledge_bases: number | null;
  documents: number | null;
  storage_bytes: number | null;
  workflows: number | null;
  tools: number | null;
  webhooks: number | null;
  team_members: number | null;
}

export interface PlanFeatures {
  workflows: boolean;
  n8n: boolean;
  tool_calling: boolean;
  webhooks: boolean;
  api_write: boolean;
  analytics_advanced: boolean;
  analytics_export: boolean;
  remove_branding: boolean;
}

export interface Plan {
  id: string;
  name: string;
  price_usd_month: number;
  extra_message_pack_size: number;
  extra_message_pack_usd: number;
  limits: PlanLimits;
  features: PlanFeatures;
  channels: string[] | null;
  support: string;
}

export interface Pricing {
  plans: Plan[];
  currency: string;
  contact_only: boolean;
}

export const getPlans = () => api<Pricing>("/v1/billing/plans");
