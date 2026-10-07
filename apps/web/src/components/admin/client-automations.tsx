"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import { StatusPill } from "@/components/shared/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  listStaffAutomations,
  listStaffRequests,
  registerAutomation,
  setAutomationStatus,
  updateStaffRequest,
  type ApiStaffRequest,
} from "@/lib/api/automations";
import { relativeTime } from "@/lib/utils";

// The protected n8n editor (second password in front of it). Platform staff only; this component is only ever
// rendered inside the staff-only admin console.
const N8N_URL = (process.env.NEXT_PUBLIC_N8N_URL || "").replace(/\/$/, "");

/** Client automations as platform staff run them: the request queue and the registry. */
export function ClientAutomations() {
  const qc = useQueryClient();
  const requests = useQuery({ queryKey: ["staff-automation-requests"], queryFn: () => listStaffRequests() });
  const registry = useQuery({ queryKey: ["staff-automation-registry"], queryFn: listStaffAutomations });
  const [registering, setRegistering] = useState<ApiStaffRequest | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["staff-automation-requests"] });
    qc.invalidateQueries({ queryKey: ["staff-automation-registry"] });
  };
  const setStatus = useMutation({
    mutationFn: (v: { id: string; status: "building" | "rejected" }) => updateStaffRequest(v.id, { status: v.status }),
    onSuccess: refresh,
  });
  const toggle = useMutation({
    mutationFn: (v: { id: string; status: "active" | "paused" }) => setAutomationStatus(v.id, v.status),
    onSuccess: refresh,
  });

  const open = (requests.data ?? []).filter((r) => r.status === "requested" || r.status === "building");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13px] font-medium text-muted">
          Requests from clients, and the workflows registered to each workspace.
        </p>
        {N8N_URL && (
          <a href={N8N_URL} target="_blank" rel="noreferrer">
            <Button variant="outline" size="default">
              <ExternalLink /> Open n8n
            </Button>
          </a>
        )}
      </div>

      <section aria-labelledby="queue-title" className="rounded-lg border border-border bg-surface">
        <h3 id="queue-title" className="border-b border-border px-5 py-3 font-display text-[15px] font-extrabold text-text">
          Request queue ({open.length})
        </h3>
        {requests.isLoading ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-10 w-full" />
          </div>
        ) : open.length === 0 ? (
          <p className="px-5 py-8 text-center text-sm font-medium text-muted">No open requests.</p>
        ) : (
          <ul className="divide-y divide-border">
            {open.map((r) => (
              <li key={r.id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-3.5">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-bold text-text">
                    {r.organization_name} <span className="font-medium text-faint">· {r.requested_by_email ?? "unknown"} · {relativeTime(r.created_at)}</span>
                  </p>
                  <p className="mt-0.5 whitespace-pre-wrap text-sm font-medium text-muted">{r.description}</p>
                  <p className="mt-0.5 font-mono text-xs text-faint">org {r.organization_id}{r.agent_id ? ` · agent ${r.agent_id}` : ""}</p>
                </div>
                <div className="flex items-center gap-2">
                  <StatusPill status={r.status} />
                  {r.status === "requested" && (
                    <Button size="sm" variant="outline" onClick={() => setStatus.mutate({ id: r.id, status: "building" })}>
                      Start building
                    </Button>
                  )}
                  <Button size="sm" variant="primary" onClick={() => setRegistering(r)}>
                    Register workflow
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setStatus.mutate({ id: r.id, status: "rejected" })}>
                    Reject
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {registering && <RegisterForm request={registering} onDone={() => (setRegistering(null), refresh())} onCancel={() => setRegistering(null)} />}

      <section aria-labelledby="registry-title" className="overflow-hidden rounded-lg border border-border bg-surface">
        <h3 id="registry-title" className="border-b border-border px-5 py-3 font-display text-[15px] font-extrabold text-text">
          Registered automations ({registry.data?.length ?? 0})
        </h3>
        {(registry.data ?? []).length === 0 ? (
          <p className="px-5 py-8 text-center text-sm font-medium text-muted">Nothing registered yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b border-border text-xs font-semibold text-faint">
                <tr>
                  <th scope="col" className="px-5 py-2.5">Name</th>
                  <th scope="col" className="px-3 py-2.5">Workspace</th>
                  <th scope="col" className="px-3 py-2.5">Status</th>
                  <th scope="col" className="px-3 py-2.5">Runs / month</th>
                  <th scope="col" className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {(registry.data ?? []).map((a) => (
                  <tr key={a.id}>
                    <td className="px-5 py-2.5 font-bold text-text">{a.name}</td>
                    <td className="px-3 py-2.5 font-medium text-muted">{a.organization_name}</td>
                    <td className="px-3 py-2.5"><StatusPill status={a.status} /></td>
                    <td className="px-3 py-2.5 font-medium text-muted">{a.runs_this_month}</td>
                    <td className="px-5 py-2.5 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={toggle.isPending}
                        onClick={() => toggle.mutate({ id: a.id, status: a.status === "active" ? "paused" : "active" })}
                      >
                        {a.status === "active" ? "Pause" : "Activate"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function RegisterForm({ request, onDone, onCancel }: { request: ApiStaffRequest; onDone: () => void; onCancel: () => void }) {
  const [workflowId, setWorkflowId] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState(`ORG-${request.organization_id} | ${request.organization_name} | `);
  const register = useMutation({
    mutationFn: () =>
      registerAutomation({
        organization_id: request.organization_id,
        agent_id: request.agent_id ?? undefined,
        request_id: request.id,
        n8n_workflow_id: workflowId.trim(),
        webhook_path: path.trim() || undefined,
        name: name.trim(),
      }),
    onSuccess: onDone,
  });
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        register.mutate();
      }}
      className="space-y-3 rounded-lg border border-border bg-surface p-5"
      aria-label="Register a workflow"
    >
      <p className="text-sm font-bold text-text">Register a workflow for {request.organization_name}</p>
      <div>
        <label htmlFor="reg-wf" className="mb-1 block text-xs font-semibold text-muted">n8n workflow id</label>
        <Input id="reg-wf" value={workflowId} onChange={(e) => setWorkflowId(e.target.value)} placeholder="abc123XYZ" />
      </div>
      <div>
        <label htmlFor="reg-path" className="mb-1 block text-xs font-semibold text-muted">Webhook path (only if an agent may call it)</label>
        <Input id="reg-path" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/webhook/…" />
      </div>
      <div>
        <label htmlFor="reg-name" className="mb-1 block text-xs font-semibold text-muted">Name (no personal data)</label>
        <Input id="reg-name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {register.isError && <p role="alert" className="text-sm font-medium text-error-text">{(register.error as Error).message}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={register.isPending || !workflowId.trim() || !name.trim()}>
          {register.isPending && <Loader2 className="size-4 animate-spin" />} Register and activate
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
