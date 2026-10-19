"use client";

import { useEntitlements } from "@/lib/entitlements";

type LimitKey = "connections" | "mcp_servers" | "inbound_hooks" | "webhooks";

/** Whether the plan includes a tool-related feature at all. `locked` is true only when the server says the cap is 0:
 *  while loading, or if the numbers cannot be read, nothing is locked (the server refuses what the plan excludes
 *  anyway, and a wrong lock would hide a feature the customer paid for). */
export function useToolLimit(key: LimitKey): {
  locked: boolean;
  limit: number | null | undefined;
} {
  const { data } = useEntitlements();
  const limit = data?.limits?.[key];
  return { locked: limit === 0, limit };
}
