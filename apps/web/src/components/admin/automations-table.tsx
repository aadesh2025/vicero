"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getAutomationsOverview, type AdminAutomation, type AutomationOwnerKind } from "@/lib/api/admin";

/** How each owner kind reads. `untagged` and `unknown-org` are warnings, not states:
 *  under deny-by-default (ADR-040) both mean nobody can see or bind the workflow. */
const OWNER_META: Record<AutomationOwnerKind, { label: string; variant: "success" | "warn" | "accent" | "default" }> = {
  org: { label: "", variant: "success" },
  "shared-template": { label: "shared template", variant: "accent" },
  internal: { label: "internal", variant: "default" },
  untagged: { label: "untagged — hidden from everyone", variant: "warn" },
  "unknown-org": { label: "tag matches no org", variant: "warn" },
};

export function AutomationsTable() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-automations"],
    queryFn: getAutomationsOverview,
  });

  const rows = data?.workflows ?? [];
  const unowned = rows.filter((w) => w.owner_kind === "untagged" || w.owner_kind === "unknown-org").length;

  return (
    <section aria-labelledby="admin-automations" className="rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
        <div>
          <h2 id="admin-automations" className="font-display text-base font-semibold text-text">
            Automations
          </h2>
          <p className="text-sm text-muted">
            Every n8n workflow across all tenants, with the org its tags scope it to.
          </p>
        </div>
        {unowned > 0 && (
          <Badge variant="warn">
            <AlertTriangle className="size-3" /> {unowned} unowned
          </Badge>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-3 p-5" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 rounded-md" />
          ))}
        </div>
      ) : data?.error ? (
        // An unreachable or keyless n8n must not render as "no automations exist".
        <div className="flex items-start gap-3 p-5 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn-text" />
          <div>
            <p className="font-medium text-text">Couldn&apos;t reach n8n.</p>
            <p className="text-muted">{data.error}</p>
          </div>
        </div>
      ) : rows.length === 0 ? (
        <p className="p-8 text-center text-sm text-muted">No workflows found in n8n.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-5 py-2.5 font-medium">Workflow</th>
                <th className="px-5 py-2.5 font-medium">Owner</th>
                <th className="px-5 py-2.5 font-medium">State</th>
                <th className="px-5 py-2.5 font-medium">Bound to</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((wf) => (
                <AutomationRow key={wf.id} wf={wf} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function AutomationRow({ wf }: { wf: AdminAutomation }) {
  const meta = OWNER_META[wf.owner_kind] ?? OWNER_META.untagged;
  return (
    <tr className="align-top">
      <td className="px-5 py-3">
        <div className="flex items-center gap-2">
          <Workflow className="size-3.5 shrink-0 text-faint" />
          <span className="font-medium text-text">{wf.name}</span>
        </div>
        <div className="mt-0.5 font-mono text-[11px] text-faint">{wf.id}</div>
      </td>
      <td className="px-5 py-3">
        {wf.owner_kind === "org" ? (
          <>
            <div className="text-text">{wf.organization_name ?? wf.owner}</div>
            <div className="font-mono text-[11px] text-faint">{wf.owner}</div>
          </>
        ) : (
          <Badge variant={meta.variant}>{meta.label}</Badge>
        )}
        {wf.owner_kind === "unknown-org" && (
          <div className="mt-1 font-mono text-[11px] text-faint">{wf.owner}</div>
        )}
      </td>
      <td className="px-5 py-3">
        <Badge variant={wf.active ? "success" : "default"}>
          {wf.active && <span className="size-1.5 rounded-full bg-success" />}
          {wf.active ? "Active" : "Inactive"}
        </Badge>
      </td>
      <td className="px-5 py-3">
        {wf.bindings.length === 0 ? (
          <span className="text-faint">—</span>
        ) : (
          <ul className="space-y-1">
            {wf.bindings.map((b, i) => (
              <li key={`${b.organization_slug}-${b.tool_name}-${i}`} className="text-xs">
                <span className="text-text">{b.agent_name ?? "org-wide"}</span>{" "}
                <span className="text-faint">· {b.organization_name}</span>
                {!b.enabled && (
                  <Badge variant="default" className="ml-1.5">
                    disabled
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}
