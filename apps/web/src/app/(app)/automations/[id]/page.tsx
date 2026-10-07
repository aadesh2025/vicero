"use client";

import Link from "next/link";
import { use, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, XCircle } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { StatusPill } from "@/components/shared/status-pill";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import {
  getAutomation,
  listAutomationRuns,
  type ApiRun,
  type RunStatus,
} from "@/lib/api/automations";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

const PAGE_SIZE = 25;
const FILTERS: { value: "" | RunStatus; label: string }[] = [
  { value: "", label: "All" },
  { value: "success", label: "Succeeded" },
  { value: "failed", label: "Failed" },
];

function duration(ms: number | null) {
  if (ms === null) return "–";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export default function AutomationDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [filter, setFilter] = useState<"" | RunStatus>("");
  const [page, setPage] = useState(1);

  const detail = useQuery({
    queryKey: ["automation", activeOrgId, id],
    queryFn: () => getAutomation(id),
    enabled: Boolean(activeOrgId),
    retry: false,
    refetchInterval: 30_000,
  });
  const runs = useQuery({
    queryKey: ["automation-runs", activeOrgId, id, filter, page],
    queryFn: () => listAutomationRuns(id, { status: filter || undefined, page, pageSize: PAGE_SIZE }),
    enabled: Boolean(activeOrgId),
    retry: false,
    placeholderData: (prev) => prev,
  });

  const notFound = detail.error instanceof ApiError && detail.error.status === 404;
  const totalPages = Math.max(1, Math.ceil((runs.data?.total ?? 0) / PAGE_SIZE));

  if (notFound) {
    return (
      <div className="mx-auto max-w-[900px] space-y-4">
        <BackLink />
        <div className="rounded-card border border-border bg-surface px-6 py-12 text-center">
          <p className="font-extrabold text-text">Automation not found</p>
          <p className="mt-1 text-sm font-medium text-muted">It may have been removed, or it belongs to another workspace.</p>
        </div>
      </div>
    );
  }

  const d = detail.data;
  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <BackLink />
      <PageHeader title={d?.name ?? "Automation"} description={d ? `For ${d.agent_name ?? "all agents"}` : undefined}>
        {d && <StatusPill status={d.status} />}
      </PageHeader>

      {detail.isError && !notFound && (
        <ErrorNote onRetry={() => detail.refetch()}>We could not load this automation right now.</ErrorNote>
      )}

      <section aria-label="This month" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {detail.isLoading || !d ? (
          [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-card" />)
        ) : (
          <>
            <Stat label="Runs, all time" value={d.runs_total.toLocaleString()} />
            <Stat label="Succeeded this month" value={d.success_this_month.toLocaleString()} icon="ok" />
            <Stat label="Failed this month" value={d.failed_this_month.toLocaleString()} icon={d.failed_this_month ? "bad" : undefined} />
            <Stat label="Last run" value={d.last_run ? relativeTime(d.last_run.started_at) : "Not yet"} />
          </>
        )}
      </section>

      {d?.last_failure && (
        <div role="status" className="flex items-start gap-3 rounded-card bg-error-soft px-4 py-3 text-sm">
          <XCircle className="mt-0.5 size-4 shrink-0 text-error-text" aria-hidden />
          <div>
            <p className="font-bold text-text">Latest problem · {relativeTime(d.last_failure.started_at)}</p>
            <p className="font-medium text-muted">{d.last_failure.message}</p>
          </div>
        </div>
      )}

      <section aria-labelledby="runs-title" className="overflow-hidden rounded-card border border-border bg-surface">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3">
          <h2 id="runs-title" className="font-display text-[15px] font-extrabold text-text">
            History
          </h2>
          <div role="group" aria-label="Filter runs" className="flex gap-1">
            {FILTERS.map((f) => (
              <Button
                key={f.value || "all"}
                size="sm"
                variant={filter === f.value ? "primary" : "outline"}
                aria-pressed={filter === f.value}
                onClick={() => {
                  setFilter(f.value);
                  setPage(1);
                }}
              >
                {f.label}
              </Button>
            ))}
          </div>
        </div>

        {runs.isError ? (
          <div className="p-5">
            <ErrorNote onRetry={() => runs.refetch()}>We could not load the history right now.</ErrorNote>
          </div>
        ) : runs.isLoading ? (
          <div className="space-y-2 p-5" aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : (runs.data?.items ?? []).length === 0 ? (
          <p className="px-5 py-10 text-center text-sm font-medium text-muted">
            {filter ? "No runs match this filter." : "No runs yet. When this automation runs, it appears here."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead className="border-b border-border text-xs font-semibold text-faint">
                <tr>
                  <th scope="col" className="px-5 py-2.5">Time</th>
                  <th scope="col" className="px-3 py-2.5">Status</th>
                  <th scope="col" className="px-3 py-2.5">Took</th>
                  <th scope="col" className="px-5 py-2.5">What happened</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {runs.data!.items.map((r) => (
                  <RunRow key={r.id} run={r} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {(runs.data?.total ?? 0) > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-border px-5 py-3 text-[13px] font-medium text-muted">
            <span>
              Page {page} of {totalPages} · {runs.data!.total.toLocaleString()} runs
            </span>
            <div className="flex gap-1">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Previous page">
                <ChevronLeft />
              </Button>
              <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)} aria-label="Next page">
                <ChevronRight />
              </Button>
            </div>
          </div>
        )}
      </section>
      <p className="text-xs font-medium text-faint">History is kept for 30 days.</p>
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/automations" className="inline-flex items-center gap-1 text-sm font-bold text-accent hover:underline">
      <ArrowLeft className="size-4" aria-hidden /> All automations
    </Link>
  );
}

function Stat({ label, value, icon }: { label: string; value: string; icon?: "ok" | "bad" }) {
  return (
    <div className="rounded-card border border-border bg-surface px-4 py-3">
      <p className="text-xs font-semibold text-faint">{label}</p>
      <p className="mt-1 flex items-center gap-1.5 font-display text-xl font-extrabold text-text">
        {icon === "ok" && <CheckCircle2 className="size-4 text-success" aria-hidden />}
        {icon === "bad" && <XCircle className="size-4 text-error" aria-hidden />}
        {value}
      </p>
    </div>
  );
}

function RunRow({ run }: { run: ApiRun }) {
  return (
    <tr>
      <td className="whitespace-nowrap px-5 py-2.5 font-medium text-text" title={new Date(run.started_at).toLocaleString()}>
        {relativeTime(run.started_at)}
      </td>
      <td className="px-3 py-2.5">
        <StatusPill status={run.status} />
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 font-medium text-muted">{duration(run.duration_ms)}</td>
      <td className="px-5 py-2.5 font-medium text-muted">
        {run.message}
        {run.over_cap && (
          <span className="ml-2 text-xs font-semibold text-warn-text">Over this month&apos;s plan limit</span>
        )}
      </td>
    </tr>
  );
}

function ErrorNote({ children, onRetry }: { children: React.ReactNode; onRetry: () => void }) {
  return (
    <div role="alert" className="flex items-center gap-3 rounded-card bg-warn-soft px-4 py-3 text-sm">
      <AlertCircle className="size-4 shrink-0 text-warn-text" aria-hidden />
      <span className="font-medium text-text">{children}</span>
      <Button variant="outline" size="sm" className="ml-auto" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
