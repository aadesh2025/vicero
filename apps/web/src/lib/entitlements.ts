"use client";

// docs/22 §10.2 — the paid-plan entitlements hook. Separate from `components/plan/use-plan.ts`
// (the pre-paid-plans trial-only endpoint, still used by TrialBanner/TrialMeter for the
// trial/trial_expired lifecycle): this one covers starter/pro/business/plan_expired, the gap
// A0-A5 built the engine and the admin panel for but nothing customer-facing read yet.
import { useQuery } from "@tanstack/react-query";
import { getEntitlements } from "@/lib/api/entitlements";
import { useSession } from "@/lib/store/session";

export function useEntitlements() {
  const orgId = useSession((s) => s.activeOrgId);
  return useQuery({
    queryKey: ["entitlements", orgId],
    queryFn: getEntitlements,
    enabled: Boolean(orgId),
    staleTime: 60_000,
  });
}
