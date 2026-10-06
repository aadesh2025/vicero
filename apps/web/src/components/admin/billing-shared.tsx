import { Badge } from "@/components/ui/badge";
import type { AdminOrg } from "@/lib/api/admin";

/** Plan badge colour (docs/20 §10.14 — the colour decision actually implemented in this
 *  codebase's 6-tone token set, not docs/22 §9.1's grey/blue/violet/gold sketch, which has no
 *  matching tokens). Paid tiers all read `success`; the plan name itself carries the tier. */
const PLAN_TONE: Record<string, "neutral" | "ai" | "success"> = {
  legacy: "neutral",
  trial: "ai",
  starter: "success",
  pro: "success",
  business: "success",
};

export function PlanBadge({ plan }: { plan: string }) {
  return <Badge variant={PLAN_TONE[plan] ?? "neutral"}>{plan}</Badge>;
}

const PAYMENT_TONE: Record<string, "success" | "warn" | "error" | "neutral"> = {
  paid: "success",
  pending: "warn",
  overdue: "error",
  waived: "neutral",
};

export function PaymentBadge({ state }: { state: string | null }) {
  if (!state) return <span className="text-xs text-faint">—</span>;
  return <Badge variant={PAYMENT_TONE[state] ?? "neutral"}>{state}</Badge>;
}

/** Whole days from now until an ISO timestamp; negative once it's past. */
export function daysUntil(iso: string): number {
  const ms = new Date(iso).getTime() - Date.now();
  return Math.ceil(ms / (24 * 60 * 60 * 1000));
}

/** `active` · `expires in 5 days` · `EXPIRED` · `AT LIMIT` (docs/22 §9.1). Reads the org's
 *  computed `status` plus its own dates/usage rather than re-deriving entitlement logic here —
 *  that logic lives server-side in `get_entitlements()` and this only labels its output. */
export function orgStatusLabel(org: AdminOrg): { text: string; tone: "success" | "warn" | "error" } {
  if (org.status === "trial_expired" || org.status === "plan_expired") {
    return { text: "EXPIRED", tone: "error" };
  }
  if (org.messages_limit !== null && org.messages_used >= org.messages_limit) {
    return { text: "AT LIMIT", tone: "error" };
  }
  if (org.plan_expires_at) {
    const days = daysUntil(org.plan_expires_at);
    if (days <= 7) return { text: `expires in ${Math.max(days, 0)} day${days === 1 ? "" : "s"}`, tone: "warn" };
  }
  return { text: "active", tone: "success" };
}
