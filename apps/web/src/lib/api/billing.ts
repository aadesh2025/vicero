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
  /** Always USD, whole dollars (legacy). Display uses `price_minor` + `Pricing.currency`. */
  price_usd_month: number;
  /** Price in `Pricing.currency`, integer minor units (cents / paise). */
  price_minor: number;
  extra_message_pack_size: number;
  extra_message_pack_usd: number;
  /** One pack's price in `Pricing.currency`, minor units. */
  extra_message_pack_price_minor: number;
  limits: PlanLimits;
  features: PlanFeatures;
  channels: string[] | null;
  support: string;
}

export interface Pricing {
  plans: Plan[];
  /** The currency every `*_minor` field is in. */
  currency: string;
  /** Why: `query` (switcher) | `geo` (country header) | `default`. Display only. */
  currency_source: "query" | "geo" | "default";
  available_currencies: string[];
  /** "excl. taxes" | "excl. VAT" | "excl. GST" */
  tax_note: string;
  contact_only: boolean;
}

/** No currency = let the server detect it; `?currency=` is the manual switcher. */
export const getPlans = (currency?: string | null) =>
  api<Pricing>(`/v1/billing/plans${currency ? `?currency=${encodeURIComponent(currency)}` : ""}`);
