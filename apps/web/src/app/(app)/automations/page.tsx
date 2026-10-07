"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowUpRight, ExternalLink, Loader2, Plus, Workflow } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { UpgradeNotice } from "@/components/plan/locked";
import { StatusPill } from "@/components/shared/status-pill";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { listAgents } from "@/lib/api/agents";
import {
  getAutomationUsage,
  listAutomationRequests,
  listAutomations,
  requestAutomation,
  type ApiAutomationCard,
  type ApiAutomationRequest,
} from "@/lib/api/automations";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

// Public URL of the staff-only n8n editor, baked in at build time. Shown to platform staff ONLY: a client never
// sees n8n, and the host itself sits behind a second password.
const N8N_URL = (process.env.NEXT_PUBLIC_N8N_URL || "").replace(/\/$/, "");

export default function AutomationsPage() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const isStaff = useSession((s) => Boolean(s.user?.is_staff));
  const [asking, setAsking] = useState(false);

  const usage = useQuery({
    queryKey: ["automation-usage", activeOrgId],
    queryFn: getAutomationUsage,
    enabled: Boolean(activeOrgId),
    retry: false,
  });
  const list = useQuery({
    queryKey: ["automations", activeOrgId],
    queryFn: listAutomations,
    enabled: Boolean(activeOrgId),
    retry: false,
  });
  const requests = useQuery({
    queryKey: ["automation-requests", activeOrgId],
    queryFn: listAutomationRequests,
    enabled: Boolean(activeOrgId),
    retry: false,
  });

  const included = usage.data ? usage.data.included : true; // never lock on a failed read: the server decides
  const openRequests = (requests.data ?? []).filter((r) => r.status === "requested" || r.status === "building");
  const automations = list.data ?? [];

  return (
    <div className="mx-auto max-w-[1200px] space-y-6">
      <PageHeader title="Automations" description="Work we run for you in the background, and how it is going.">
        {isStaff && N8N_URL && (
          <a href={N8N_URL} target="_blank" rel="noreferrer">
            <Button variant="outline" size="default">
              <ExternalLink /> Open n8n
            </Button>
          </a>
        )}
        <Button
          variant="primary"
          size="default"
          disabled={!usage.data?.can_request}
          onClick={() => setAsking(true)}
          title={usage.data && !usage.data.can_request ? "Your plan has no free automation slot" : undefined}
        >
          <Plus /> Request automation
        </Button>
      </PageHeader>

      {usage.data && !included && (
        <UpgradeNotice title="Automations are part of a paid plan">
          Tell us what you want done automatically and we build it for you. Upgrade your plan to start.
        </UpgradeNotice>
      )}
      {usage.data && included && !usage.data.can_request && (
        <UpgradeNotice title="You are using every automation your plan includes">
          Your plan includes {usage.data.automations_limit} automations. Upgrade to add more.
        </UpgradeNotice>
      )}

      {usage.data && included && (
        <p className="text-[13px] font-medium text-muted">
          {usage.data.automations_used} of {usage.data.automations_limit ?? "unlimited"} automations ·{" "}
          {usage.data.runs_this_month.toLocaleString()} of{" "}
          {usage.data.runs_limit === null ? "unlimited" : usage.data.runs_limit.toLocaleString()} runs this month
        </p>
      )}

      {list.isError && (
        <div role="alert" className="flex items-center gap-3 rounded-card bg-warn-soft px-4 py-3 text-sm">
          <AlertCircle className="size-4 shrink-0 text-warn-text" aria-hidden />
          <span className="font-medium text-text">We could not load your automations right now. Try again in a moment.</span>
          <Button variant="outline" size="sm" className="ml-auto" onClick={() => list.refetch()}>
            Retry
          </Button>
        </div>
      )}

      {list.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-40 w-full rounded-card" />
          ))}
        </div>
      ) : automations.length > 0 ? (
        <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {automations.map((a) => (
            <li key={a.id}>
              <AutomationCard automation={a} />
            </li>
          ))}
        </ul>
      ) : (
        !list.isError && included && (
          <div className="rounded-card border border-border bg-surface px-6 py-12 text-center">
            <span className="mx-auto grid size-11 place-items-center rounded-[10px] bg-ai-soft text-ai">
              <Workflow className="size-5" aria-hidden />
            </span>
            <p className="mt-3 font-extrabold text-text">No automations yet</p>
            <p className="mx-auto mt-1 max-w-md text-sm font-medium text-muted">
              Describe a task you want done for you, like sending a booking confirmation or updating a spreadsheet.
              Our team builds it, tests it, and turns it on. It shows up here with its history.
            </p>
          </div>
        )
      )}

      {openRequests.length > 0 && <RequestList requests={openRequests} />}

      <RequestDialog open={asking} onClose={() => setAsking(false)} />
    </div>
  );
}

function AutomationCard({ automation: a }: { automation: ApiAutomationCard }) {
  return (
    <Link
      href={`/automations/${a.id}`}
      className="block rounded-card border border-border bg-surface p-5 transition-colors hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-[15px] font-extrabold text-text">{a.name}</p>
          <p className="mt-0.5 truncate text-[13px] font-medium text-muted">{a.agent_name ?? "All agents"}</p>
        </div>
        <StatusPill status={a.status} />
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-3 text-[13px]">
        <div>
          <dt className="font-semibold text-faint">Last run</dt>
          <dd className="mt-0.5 font-bold text-text">{a.last_run_at ? relativeTime(a.last_run_at) : "Not yet"}</dd>
        </div>
        <div>
          <dt className="font-semibold text-faint">Success</dt>
          <dd className="mt-0.5 font-bold text-text">
            {a.success_rate === null ? "–" : `${Math.round(a.success_rate * 100)}%`}
          </dd>
        </div>
        <div>
          <dt className="font-semibold text-faint">Runs this month</dt>
          <dd className="mt-0.5 font-bold text-text">{a.runs_this_month.toLocaleString()}</dd>
        </div>
      </dl>
      <div className="mt-4 flex items-center justify-between text-[13px]">
        {a.last_status ? <StatusPill status={a.last_status}>Last: {a.last_status}</StatusPill> : <span />}
        <span className="inline-flex items-center gap-1 font-bold text-accent">
          Details <ArrowUpRight className="size-3.5" aria-hidden />
        </span>
      </div>
    </Link>
  );
}

function RequestList({ requests }: { requests: ApiAutomationRequest[] }) {
  return (
    <section aria-labelledby="open-requests" className="rounded-card border border-border bg-surface">
      <h2 id="open-requests" className="border-b border-border px-5 py-3 font-display text-[15px] font-extrabold text-text">
        Being set up
      </h2>
      <ul className="divide-y divide-border">
        {requests.map((r) => (
          <li key={r.id} className="flex items-start justify-between gap-4 px-5 py-3.5">
            <div className="min-w-0">
              <p className="text-sm font-medium text-text">{r.description}</p>
              <p className="mt-0.5 text-xs font-medium text-faint">Asked {relativeTime(r.created_at)}</p>
            </div>
            <StatusPill status={r.status}>{r.status === "building" ? "We are building it" : "Received"}</StatusPill>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RequestDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [description, setDescription] = useState("");
  const [agentId, setAgentId] = useState("");

  const agents = useQuery({ queryKey: ["agents", activeOrgId], queryFn: listAgents, enabled: open });

  const send = useMutation({
    mutationFn: () => requestAutomation({ description: description.trim(), agent_id: agentId || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["automation-requests", activeOrgId] });
      qc.invalidateQueries({ queryKey: ["automation-usage", activeOrgId] });
      setDescription("");
      setAgentId("");
      onClose();
    },
  });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Request an automation</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label htmlFor="automation-need" className="mb-1 block text-xs font-semibold text-muted">
              What do you need?
            </label>
            <Textarea
              id="automation-need"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={5}
              maxLength={2000}
              placeholder="For example: when a customer books an appointment, add it to our Google Sheet and send them a confirmation message."
            />
            <p className="mt-1 text-xs font-medium text-faint">Do not include passwords or card numbers. We will ask if we need access.</p>
          </div>
          <div>
            <label htmlFor="automation-agent" className="mb-1 block text-xs font-semibold text-muted">
              Which agent is it for? (optional)
            </label>
            <select
              id="automation-agent"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-2 text-sm font-medium text-text"
            >
              <option value="">Any agent</option>
              {(agents.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
          {send.isError && (
            <p role="alert" className="text-sm font-medium text-error-text">
              {(send.error as Error).message}
            </p>
          )}
          <Button type="submit" variant="primary" className="w-full" disabled={send.isPending || description.trim().length < 10}>
            {send.isPending && <Loader2 className="size-4 animate-spin" />} Send request
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
