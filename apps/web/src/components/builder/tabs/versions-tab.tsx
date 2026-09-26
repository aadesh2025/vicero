"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Loader2, RotateCcw, Rocket } from "lucide-react";
import { SectionCard } from "@/components/builder/field";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { listVersions, publishVersion, rollbackVersion } from "@/lib/api/agents";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";
import { relativeTime } from "@/lib/utils";

/** Real version history for one agent (`GET /v1/agents/{id}/versions`).
 *
 * `currentVersionId` is the agent's `current_version_id` — the published version actually
 * serving traffic, which is not necessarily the newest one after a rollback. */
export function VersionsTab({
  agentId,
  currentVersionId,
}: {
  agentId: string;
  currentVersionId: string | null;
}) {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canPublish = useCan("agents:publish");
  const [busy, setBusy] = useState<number | null>(null);

  const { data: versions, isLoading } = useQuery({
    queryKey: ["agent-versions", agentId, activeOrgId],
    queryFn: () => listVersions(agentId),
    enabled: Boolean(activeOrgId),
  });

  const act = useMutation({
    mutationFn: ({ kind, version }: { kind: "publish" | "rollback"; version: number }) =>
      kind === "publish" ? publishVersion(agentId, version) : rollbackVersion(agentId, version),
    onMutate: ({ version }) => setBusy(version),
    onSettled: async () => {
      setBusy(null);
      // The agent row carries current_version_id, so both queries are now stale.
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["agent-versions", agentId, activeOrgId] }),
        qc.invalidateQueries({ queryKey: ["agent", agentId, activeOrgId] }),
      ]);
    },
  });

  // Newest first — the draft an editor is working on belongs at the top.
  const rows = [...(versions ?? [])].sort((a, b) => b.version - a.version);

  return (
    <SectionCard title="Version history" description="Publish drafts, compare, and roll back safely.">
      {isLoading ? (
        <div className="space-y-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex gap-4 p-2">
              <Skeleton className="size-8 shrink-0 rounded-full" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-3.5 w-32" />
                <Skeleton className="h-3 w-48" />
              </div>
            </div>
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">
          No versions yet — save a change on the Persona or Model tab to create the first draft.
        </p>
      ) : (
        <ol className="relative space-y-1 before:absolute before:left-[15px] before:top-2 before:h-[calc(100%-1rem)] before:w-px before:bg-border">
          {rows.map((v) => {
            const isCurrent = currentVersionId !== null && v.id === currentVersionId;
            const working = busy === v.version;
            return (
              <li
                key={v.id}
                className="relative flex gap-4 rounded-md p-2 transition-colors hover:bg-surface-2/40"
              >
                <span
                  className={
                    "z-10 mt-0.5 grid size-8 shrink-0 place-items-center rounded-full border " +
                    (isCurrent
                      ? "border-accent/40 bg-accent/15 text-accent"
                      : v.is_published
                        ? "border-border bg-surface-2 text-muted"
                        : "border-warn/40 bg-warn/10 text-warn-text")
                  }
                >
                  <GitBranch className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-text">v{v.version}</span>
                    {isCurrent && <Badge variant="accent">Current</Badge>}
                    {!v.is_published && <Badge variant="warn">Draft</Badge>}
                  </div>
                  <p className="mt-0.5 line-clamp-1 text-sm text-muted">
                    {v.system_prompt?.trim() || "No system prompt set."}
                  </p>
                  <p className="mt-0.5 text-xs text-faint">
                    {v.is_published ? "Published" : "Created"} {relativeTime(v.created_at)}
                  </p>
                </div>
                {canPublish && (
                  <div className="flex items-center gap-2">
                    {!v.is_published && (
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={working}
                        onClick={() => act.mutate({ kind: "publish", version: v.version })}
                      >
                        {working ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Rocket className="size-3.5" />
                        )}{" "}
                        Publish
                      </Button>
                    )}
                    {v.is_published && !isCurrent && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={working}
                        onClick={() => act.mutate({ kind: "rollback", version: v.version })}
                      >
                        {working ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <RotateCcw className="size-3.5" />
                        )}{" "}
                        Roll back
                      </Button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </SectionCard>
  );
}
