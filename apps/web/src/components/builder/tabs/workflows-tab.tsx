"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Plus, Workflow as WorkflowIcon } from "lucide-react";
import { SectionCard } from "@/components/builder/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { WorkflowCanvas } from "@/components/builder/workflow-canvas/workflow-canvas";
import { createWorkflow, listWorkflows } from "@/lib/api/workflows";
import { useSession } from "@/lib/store/session";
import { LockedRegion } from "@/components/plan/locked";
import { useCan } from "@/lib/rbac";
import type { ApiWorkflow } from "@/lib/api/types";

/** The Workflows tab (docs/17 Phase 2 item 6): a list of this agent's workflows, or the
 * canvas for whichever one is selected. Follows the same tab-owns-its-screen convention as
 * Channels/Analytics — full width, no Playground column (see the agent page's
 * FULL_WIDTH_TABS). */
/** Workflows are a paid feature; on a trial the tab is shown, locked. */
export function WorkflowsTab({ agentId }: { agentId: string }) {
  return (
    <LockedRegion feature="workflows" label="Workflows">
      <WorkflowsTabInner agentId={agentId} />
    </LockedRegion>
  );
}

function WorkflowsTabInner({ agentId }: { agentId: string }) {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canWrite = useCan("workflows:write");
  // The selected WORKFLOW OBJECT, not just its id — a freshly created workflow must open its
  // canvas immediately, not wait on the list query's invalidation to refetch and include it
  // before a by-id lookup into `workflows` would resolve to anything.
  const [selected, setSelected] = useState<ApiWorkflow | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const { data: workflows, isLoading } = useQuery({
    queryKey: ["workflows", agentId, activeOrgId],
    queryFn: () => listWorkflows(agentId),
    enabled: Boolean(activeOrgId),
  });

  const createMutation = useMutation({
    mutationFn: () => createWorkflow(agentId, newName.trim() || "Untitled workflow"),
    onSuccess: async (w) => {
      await qc.invalidateQueries({ queryKey: ["workflows", agentId, activeOrgId] });
      setCreating(false);
      setNewName("");
      setSelected(w);
    },
  });

  if (selected) {
    return (
      <div className="space-y-3">
        <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
          <ArrowLeft className="size-3.5" /> All workflows
        </Button>
        <div className="flex items-center justify-between">
          <h3 className="font-display text-base font-semibold text-text">{selected.name}</h3>
        </div>
        <WorkflowCanvas workflow={selected} agentId={agentId} />
      </div>
    );
  }

  return (
    <SectionCard
      title="Workflows"
      description="Multi-step automations this agent can run — tool calls, sub-agents, approvals, and branching, wired together visually."
    >
      {isLoading ? (
        <div className="space-y-2" aria-busy="true">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-16 w-full rounded-lg" />
          ))}
        </div>
      ) : !workflows || workflows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">No workflows yet for this agent.</p>
      ) : (
        <ul className="space-y-2">
          {workflows.map((w) => (
            <li key={w.id}>
              <button
                onClick={() => setSelected(w)}
                className="flex w-full items-center gap-3 rounded-lg border border-border bg-surface-2/40 p-3 text-left transition-colors hover:border-accent/40 hover:bg-surface-2"
              >
                <span className="grid size-9 shrink-0 place-items-center rounded-full border border-border bg-surface-2 text-muted">
                  <WorkflowIcon className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-text">{w.name}</span>
                  <span className="block truncate text-xs text-muted">
                    {w.description || "No description"}
                  </span>
                </span>
                <Badge variant={w.current_version_id ? "accent" : "warn"}>
                  {w.current_version_id ? "Published" : "Draft only"}
                </Badge>
              </button>
            </li>
          ))}
        </ul>
      )}

      {canWrite && (
        <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
          <Plus className="size-3.5" /> New workflow
        </Button>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New workflow</DialogTitle>
            <DialogDescription>Give it a name — you&apos;ll build the graph on the next screen.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <Input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Refund approval"
              onKeyDown={(e) => {
                if (e.key === "Enter" && newName.trim()) createMutation.mutate();
              }}
            />
            <Button
              className="w-full"
              variant="primary"
              disabled={!newName.trim() || createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              {createMutation.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Create workflow
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}
