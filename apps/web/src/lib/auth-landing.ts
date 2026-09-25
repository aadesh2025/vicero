"use client";

import { listAgents } from "@/lib/api/agents";
import { listOrgs } from "@/lib/api/orgs";
import { setActiveOrgId } from "@/lib/api/tokens";

/** Where to send someone who has just authenticated by a route that can also be a first visit
 * (sign-up, magic link, provider sign-in): a workspace with no agent yet goes to onboarding,
 * everyone else to where they were headed. Never throws — a failed lookup just means "dashboard". */
export async function landingPath(next?: string | null): Promise<string> {
  const fallback = next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
  try {
    const orgs = await listOrgs();
    const first = orgs[0];
    if (!first) return fallback;
    setActiveOrgId(first.id);
    const agents = await listAgents();
    return agents.length === 0 ? "/onboarding" : fallback;
  } catch {
    return fallback;
  }
}
