"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ExternalLink, Loader2, Webhook, Workflow as WorkflowIcon } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { listAgents } from "@/lib/api/agents";
import { bindN8nWorkflow, listN8nWorkflows, type ApiN8nWorkflow } from "@/lib/api/tools";
import { LockedPanel } from "@/components/plan/locked";
import { useFeature } from "@/components/plan/use-plan";
import { useSession } from "@/lib/store/session";
import { ApiError } from "@/lib/api/client";

const N8N_URL = "http://localhost:5678";

function toToolName(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "workflow";
}

export default function AutomationsPage() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [binding, setBinding] = useState<ApiN8nWorkflow | null>(null);
  const { allowed: n8nAllowed } = useFeature("n8n");

  const { data: workflows, isLoading, error } = useQuery({
    queryKey: ["n8n-workflows", activeOrgId],
    queryFn: listN8nWorkflows,
    // Locked plans never ask n8n: the server would answer 402 anyway.
    enabled: Boolean(activeOrgId) && n8nAllowed,
    retry: false,
  });

  const unavailable = error instanceof ApiError ? error : null;

  return (
    <div className="mx-auto max-w-[1200px] space-y-6">
      <PageHeader title="Automations" description="n8n workflows your agents can trigger as tools.">
        <a href={N8N_URL} target="_blank" rel="noreferrer">
          <Button variant="outline" size="default">
            <ExternalLink /> Open n8n
          </Button>
        </a>
      </PageHeader>

      <LockedPanel feature="n8n" title="Automations are part of a paid plan">
        Connect n8n workflows your agents can trigger as tools. Upgrade to unlock them.
      </LockedPanel>

      {!n8nAllowed ? null : unavailable ? (
        <div className="flex items-center gap-3 rounded-lg border border-warn/25 bg-warn/[0.05] px-4 py-3 text-sm">
          <AlertCircle className="size-4 shrink-0 text-warn-text" />
          <div className="flex-1">
            <span className="font-medium text-text">n8n not reachable.</span>{" "}
            <span className="text-muted">
              {unavailable.code === "n8n.unconfigured"
                ? "Set N8N_API_KEY in your environment."
                : unavailable.message}
            </span>
          </div>
          <Badge variant="warn">Offline</Badge>
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-lg border border-success/25 bg-success/[0.05] px-4 py-3 text-sm">
          <span className="grid size-8 place-items-center rounded-md border border-success/30 bg-success/10 text-success-text">
            <WorkflowIcon className="size-4" />
          </span>
          <div className="flex-1">
            <span className="font-medium text-text">n8n connected</span>
            <span className="text-muted">
              {" "}
              · {N8N_URL} · {workflows?.length ?? 0} workflows
            </span>
          </div>
          <Badge variant="success">
            <span className="size-1.5 rounded-full bg-success" /> Online
          </Badge>
        </div>
      )}

      <div className={`overflow-hidden rounded-lg border border-border bg-surface ${n8nAllowed ? "" : "hidden"}`}>
        {isLoading ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {(workflows ?? []).map((wf) => (
              <li key={wf.id} className="flex items-center gap-4 px-5 py-4 transition-colors hover:bg-surface-2/40">
                <span className="grid size-10 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                  <Webhook className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium text-text">{wf.name}</span>
                    <Badge variant={wf.active ? "success" : "default"}>{wf.active ? "Active" : "Inactive"}</Badge>
                    {!wf.webhook_url && <Badge variant="warn">no webhook</Badge>}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-xs text-faint">
                    {wf.webhook_url ?? "add a Webhook trigger node to bind this workflow"}
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!wf.webhook_url}
                  onClick={() => setBinding(wf)}
                >
                  Bind as tool
                </Button>
              </li>
            ))}
            {(workflows ?? []).length === 0 && !unavailable && (
              <li className="px-5 py-10 text-center text-sm text-muted">
                No workflows in n8n yet. Import one from <span className="font-mono">infra/n8n/</span>.
              </li>
            )}
          </ul>
        )}
      </div>

      <BindDialog workflow={binding} onClose={() => setBinding(null)} />
    </div>
  );
}

function BindDialog({ workflow, onClose }: { workflow: ApiN8nWorkflow | null; onClose: () => void }) {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [mode, setMode] = useState<"sync" | "async">("sync");

  const { data: agents } = useQuery({
    queryKey: ["agents", activeOrgId],
    queryFn: listAgents,
    enabled: Boolean(workflow),
  });

  // Seed the tool name from the workflow when the dialog opens.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (workflow) setName(toToolName(workflow.name));
  }, [workflow]);

  const bind = useMutation({
    mutationFn: () =>
      bindN8nWorkflow({
        name,
        workflow_id: workflow!.id,
        workflow_name: workflow!.name,
        webhook_url: workflow!.webhook_url,
        mode,
        agent_id: agentId || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tools"] });
      setName("");
      setAgentId("");
      onClose();
    },
  });

  return (
    <Dialog open={Boolean(workflow)} onOpenChange={(o) => !o && (setName(""), onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bind “{workflow?.name}” as a tool</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            bind.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs text-muted">Tool name (the model calls this)</label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="create_ticket" />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">Agent</label>
            <select
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 text-sm text-text"
            >
              <option value="">Select an agent…</option>
              {(agents ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">Mode</label>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as "sync" | "async")}
              className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 text-sm text-text"
            >
              <option value="sync">sync — wait for Respond to Webhook</option>
              <option value="async">async — resolve later via callback</option>
            </select>
          </div>
          {bind.isError && <p className="text-sm text-error-text">{(bind.error as Error).message}</p>}
          <Button
            type="submit"
            variant="primary"
            className="w-full"
            disabled={bind.isPending || !name.trim() || !agentId}
          >
            {bind.isPending && <Loader2 className="size-4 animate-spin" />} Bind as tool
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
