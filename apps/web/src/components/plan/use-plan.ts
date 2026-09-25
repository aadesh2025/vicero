"use client";

import { useQuery } from "@tanstack/react-query";
import { getPlan, type PlanFeature, type PlanStatus } from "@/lib/api/plan";
import { useSession } from "@/lib/store/session";

/** The active workspace's plan (trial status, usage meter, feature locks).
 *
 * Polled gently: the meter should move as visitors chat and the banner should flip when the
 * trial ends, without anyone refreshing. The server is the authority — this only draws it. */
export function usePlan() {
  const orgId = useSession((s) => s.activeOrgId);
  return useQuery({
    queryKey: ["plan", orgId],
    queryFn: () => getPlan(orgId as string),
    enabled: Boolean(orgId),
    refetchInterval: 60_000,
    staleTime: 15_000,
    retry: false,
  });
}

/** Whether a plan-gated feature is available. `true` while loading or if the plan can't be read:
 * the UI never locks something the server might allow (the server would refuse it anyway). */
export function useFeature(feature: PlanFeature): { allowed: boolean; plan: PlanStatus | undefined } {
  const { data } = usePlan();
  return { allowed: data ? data.features[feature] : true, plan: data };
}
