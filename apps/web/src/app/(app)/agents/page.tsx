"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Bot, Plus } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { NewAgentDialog, type NewAgentSubmit } from "@/components/agents/new-agent-dialog";
import { createAgent, listAgents } from "@/lib/api/agents";
import { LockedButton } from "@/components/plan/locked";
import { usePlan } from "@/components/plan/use-plan";
import { useSession } from "@/lib/store/session";
import { apiAgentStatusMeta as statusMeta } from "@/lib/display";
import { relativeTime } from "@/lib/utils";

export default function AgentsPage() {
  const router = useRouter();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const { data: agents, isLoading } = useQuery({
    queryKey: ["agents", activeOrgId],
    queryFn: listAgents,
    enabled: Boolean(activeOrgId),
  });

  const [creating, setCreating] = useState(false);
  const { data: plan } = usePlan();
  // The server enforces this (402 `plan_limit`); the button just says why it is off.
  const canCreate = plan ? plan.can_create_agent : true;
  const lockReason =
    plan?.status === "trial_expired"
      ? "Your free trial has ended, so your agent is read-only."
      : "Your free trial includes one agent.";

  async function onCreate({ name, templateId }: NewAgentSubmit) {
    const agent = await createAgent(name, { templateId: templateId ?? undefined });
    // Land on the Persona tab: whatever the template filled in is the first thing to review,
    // and it's editable from the moment it loads.
    router.push(`/agents/${agent.id}?tab=persona`);
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader title="Agents" description="Build, configure, and publish your AI agents.">
        {canCreate ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus /> New agent
          </Button>
        ) : (
          <LockedButton reason={lockReason}>New agent</LockedButton>
        )}
      </PageHeader>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[180px] rounded-lg" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {(agents ?? []).map((agent) => {
            const status = statusMeta[agent.status];
            return (
              <Link
                key={agent.id}
                href={`/agents/${agent.id}`}
                className="group relative overflow-hidden rounded-lg border border-border bg-surface p-5 transition-colors hover:border-border-strong"
              >
                <span className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/70 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
                <div className="flex items-start justify-between">
                  <span className="grid size-11 place-items-center rounded-lg border border-border bg-surface-2 font-display text-lg font-semibold text-muted">
                    {agent.name[0]?.toUpperCase()}
                  </span>
                  <Badge variant={status?.variant ?? "default"}>
                    {agent.status === "published" && <span className="size-1.5 rounded-full bg-success" />}
                    {status?.label ?? agent.status}
                  </Badge>
                </div>
                <h3 className="mt-4 font-display text-lg font-semibold text-text">{agent.name}</h3>
                <p className="mt-1 line-clamp-2 text-sm text-muted">
                  {agent.description || "No description yet."}
                </p>
                <div className="mt-4 flex items-center justify-between border-t border-border pt-4 text-xs text-faint">
                  <span className="font-mono">v{agent.draft_version} draft</span>
                  <span>updated {relativeTime(agent.updated_at)}</span>
                </div>
              </Link>
            );
          })}

          <button
            onClick={() => setCreating(true)}
            className="flex min-h-[180px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border-strong bg-surface/40 text-muted transition-colors hover:border-accent/40 hover:bg-accent/[0.03] hover:text-accent"
          >
            <span className="grid size-11 place-items-center rounded-lg border border-border bg-surface-2">
              <Bot className="size-5" />
            </span>
            <span className="text-sm font-medium">Create a new agent</span>
          </button>
        </div>
      )}

      <NewAgentDialog open={creating} onOpenChange={setCreating} onSubmit={onCreate} />
    </div>
  );
}
