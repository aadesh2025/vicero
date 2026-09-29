"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AdminOrg } from "@/lib/api/admin";
import { formatBytes, UsageMeter } from "@/components/admin/usage-meter";
import { orgStatusLabel, PaymentBadge, PlanBadge } from "@/components/admin/billing-shared";
import { ChangePlanDialog } from "@/components/admin/change-plan-dialog";
import { RevokePlanDialog } from "@/components/admin/revoke-plan-dialog";
import { AddPacksDialog } from "@/components/admin/add-packs-dialog";
import { OrgDetailDrawer } from "@/components/admin/org-detail-drawer";

type Action = { org: AdminOrg; type: "change" | "revoke" | "packs" | "detail" } | null;

export function OrgBillingTable({ orgs, isLoading }: { orgs: AdminOrg[]; isLoading: boolean }) {
  const [action, setAction] = useState<Action>(null);

  return (
    <section className="rounded-lg border border-border bg-surface">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-faint">
              <th className="px-5 py-3 font-medium">Workspace</th>
              <th className="px-5 py-3 font-medium">Plan</th>
              <th className="px-5 py-3 font-medium">Status</th>
              <th className="px-5 py-3 font-medium">Messages</th>
              <th className="px-5 py-3 font-medium">Storage</th>
              <th className="px-5 py-3 font-medium">Payment</th>
              <th className="px-5 py-3 font-medium">Note</th>
              <th className="px-5 py-3 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {isLoading && (
              <tr>
                <td colSpan={8} className="px-5 py-6 text-center text-muted">
                  Loading workspaces…
                </td>
              </tr>
            )}
            {!isLoading && orgs.length === 0 && (
              <tr>
                <td colSpan={8} className="px-5 py-6 text-center text-muted">
                  No workspaces match this filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              orgs.map((o) => {
                const status = orgStatusLabel(o);
                return (
                  <tr key={o.id} className="align-top hover:bg-surface-2/50">
                    <td className="px-5 py-3">
                      <button
                        type="button"
                        className="text-left font-medium text-text hover:underline"
                        onClick={() => setAction({ org: o, type: "detail" })}
                      >
                        {o.name}
                      </button>
                      <div className="text-xs text-faint">{o.owner_email ?? "—"}</div>
                    </td>
                    <td className="px-5 py-3">
                      <PlanBadge plan={o.plan} />
                    </td>
                    <td className="px-5 py-3">
                      <Badge variant={status.tone}>{status.text}</Badge>
                    </td>
                    <td className="px-5 py-3">
                      <UsageMeter used={o.messages_used} limit={o.messages_limit} label={`${o.name} messages used`} />
                    </td>
                    <td className="px-5 py-3">
                      <UsageMeter
                        used={o.storage_bytes_used}
                        limit={o.storage_bytes_limit}
                        label={`${o.name} storage used`}
                        format={formatBytes}
                      />
                    </td>
                    <td className="px-5 py-3">
                      <PaymentBadge state={o.payment_state} />
                    </td>
                    <td className="max-w-[160px] truncate px-5 py-3 text-xs text-muted" title={o.plan_note ?? undefined}>
                      {o.plan_note ?? "—"}
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap gap-1.5">
                        <Button size="sm" variant="outline" onClick={() => setAction({ org: o, type: "change" })}>
                          Change plan
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => setAction({ org: o, type: "packs" })}>
                          Add messages
                        </Button>
                        <Button size="sm" variant="destructive" onClick={() => setAction({ org: o, type: "revoke" })}>
                          Revoke
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {action?.type === "change" && (
        <ChangePlanDialog org={action.org} open onOpenChange={(v) => !v && setAction(null)} />
      )}
      {action?.type === "revoke" && (
        <RevokePlanDialog org={action.org} open onOpenChange={(v) => !v && setAction(null)} />
      )}
      {action?.type === "packs" && (
        <AddPacksDialog org={action.org} open onOpenChange={(v) => !v && setAction(null)} />
      )}
      {action?.type === "detail" && (
        <OrgDetailDrawer orgId={action.org.id} open onOpenChange={(v) => !v && setAction(null)} />
      )}
    </section>
  );
}
