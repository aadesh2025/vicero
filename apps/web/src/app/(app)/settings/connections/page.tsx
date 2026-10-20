"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Link2, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { Section } from "@/components/settings/section";
import { ConnectionDialog } from "@/components/tools/connection-dialog";
import { useToolLimit } from "@/components/tools/limits";
import { LockedPanel } from "@/components/plan/locked";
import { UpgradeNotice } from "@/components/plan/locked";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api/client";
import {
  AUTH_FIELDS,
  deleteConnection,
  listConnections,
  testConnection,
  updateConnection,
  type ApiConnection,
  type ConnectionTestResult,
} from "@/lib/api/connections";
import { useCan } from "@/lib/rbac";
import { useSession } from "@/lib/store/session";

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export default function ConnectionsPage() {
  const qc = useQueryClient();
  const orgId = useSession((s) => s.activeOrgId);
  const canManage = useCan("tools:manage");
  const { locked } = useToolLimit("connections");
  const [editing, setEditing] = useState<ApiConnection | null>(null);
  const [creating, setCreating] = useState(false);
  const [results, setResults] = useState<
    Record<string, ConnectionTestResult | "running">
  >({});
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["connections", orgId],
    queryFn: listConnections,
    enabled: Boolean(orgId) && canManage,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["connections"] });
  };

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateConnection(id, { enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteConnection(id),
    onSuccess: () => {
      setDeleteError(null);
      refresh();
    },
    onError: (e) => {
      const d =
        e instanceof ApiError
          ? (e.details as
              { tools?: string[]; mcp_servers?: string[] } | undefined)
          : undefined;
      const users = [
        ...(d?.tools ?? []),
        ...(d?.mcp_servers ?? []).map((m) => `${m} (MCP)`),
      ];
      setDeleteError(
        users.length
          ? `Still used by: ${users.join(", ")}. Delete or repoint those first.`
          : (e as Error).message,
      );
    },
  });

  async function runTest(id: string) {
    setResults((r) => ({ ...r, [id]: "running" }));
    try {
      const res = await testConnection(id);
      setResults((r) => ({ ...r, [id]: res }));
    } catch (e) {
      setResults((r) => ({
        ...r,
        [id]: {
          ok: false,
          status_code: null,
          latency_ms: null,
          error: (e as Error).message,
        },
      }));
    }
  }

  if (!canManage) {
    return (
      <Section
        title="Connections"
        description="Saved logins for the outside services your agents call."
      >
        <p className="text-sm text-muted">
          Only people who manage tools can see or change connections.
        </p>
      </Section>
    );
  }

  return (
    <Section
      title="Connections"
      description="Save how to log in to a service once, then build tools on it. Credentials are encrypted and never shown to the agent or in logs."
      action={
        canManage && !locked ? (
          <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
            <Plus /> New connection
          </Button>
        ) : undefined
      }
    >
      {locked && (
        <div className="mb-4">
          <UpgradeNotice title="Connections are part of the Pro and Business plans">
            Save logins for your own APIs and reuse them across tools. Upgrade
            to add one.
          </UpgradeNotice>
        </div>
      )}
      <LockedPanel
        feature="tool_calling"
        title="Connections are part of a paid plan"
      >
        Upgrade to connect your own APIs.
      </LockedPanel>

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : (data ?? []).length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-strong p-6 text-center">
          <Link2 className="mx-auto mb-2 size-5 text-faint" aria-hidden />
          <p className="text-sm font-bold text-text">No connections yet</p>
          <p className="mt-0.5 text-sm text-muted">
            Add one, then create an API tool that uses it.
          </p>
        </div>
      ) : (
        <ul
          className="divide-y divide-border overflow-hidden rounded-lg border border-border"
          aria-label="Connections"
        >
          {(data ?? []).map((c) => {
            const result = results[c.id];
            return (
              <li
                key={c.id}
                className="flex flex-wrap items-center gap-3 p-4"
                data-testid="connection-row"
              >
                <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                  <Link2 className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-bold text-text">
                      {c.name}
                    </span>
                    <Badge variant="neutral">
                      {AUTH_FIELDS[c.auth_type].label}
                    </Badge>
                    {!c.enabled && <Badge variant="warn">Off</Badge>}
                  </div>
                  <div className="truncate font-mono text-xs text-muted">
                    {host(c.base_url)}
                  </div>
                  <div className="text-xs text-faint">
                    {c.tools_count === 0
                      ? "Not used by any tool yet"
                      : `Used by ${c.tools_count} tool${c.tools_count === 1 ? "" : "s"}`}
                  </div>
                </div>
                {result && result !== "running" && (
                  <span
                    role="status"
                    className={`flex items-center gap-1 text-xs font-bold ${result.ok ? "text-success-text" : "text-error-text"}`}
                  >
                    {result.ok ? (
                      <Check className="size-3.5" />
                    ) : (
                      <X className="size-3.5" />
                    )}
                    {result.ok
                      ? `Reached it (${result.status_code}, ${result.latency_ms} ms)`
                      : result.error}
                  </span>
                )}
                <Switch
                  aria-label={`${c.name} enabled`}
                  checked={c.enabled}
                  onCheckedChange={(v) =>
                    toggle.mutate({ id: c.id, enabled: v })
                  }
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void runTest(c.id)}
                  disabled={result === "running"}
                >
                  {result === "running" && <Loader2 className="animate-spin" />}{" "}
                  Test
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Edit ${c.name}`}
                  onClick={() => setEditing(c)}
                >
                  <Pencil />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Delete ${c.name}`}
                  onClick={() => {
                    setDeleteError(null);
                    remove.mutate(c.id);
                  }}
                >
                  <Trash2 />
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      {deleteError && (
        <p role="alert" className="mt-3 text-sm text-error-text">
          {deleteError}
        </p>
      )}

      <ConnectionDialog
        open={creating || editing !== null}
        onOpenChange={(o) => {
          if (!o) {
            setCreating(false);
            setEditing(null);
          }
        }}
        connection={editing}
        onSaved={refresh}
      />
    </Section>
  );
}
