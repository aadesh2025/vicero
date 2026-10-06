"use client";

import { useQuery } from "@tanstack/react-query";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { getAdminOrg, listAdminOrgs } from "@/lib/api/admin";
import { formatBytes, UsageMeter } from "@/components/admin/usage-meter";
import { PaymentBadge, PlanBadge } from "@/components/admin/billing-shared";
import { formatMoney } from "@/lib/money";

/** The dispute view — "I already paid for August" gets answered here (docs/22 §9.4).
 *
 * No Sheet/Drawer primitive exists in this codebase yet, so this reuses the wide-`Dialog`
 * pattern `NewAgentDialog` already established rather than introducing a new overlay type. */
export function OrgDetailDrawer({
  orgId,
  open,
  onOpenChange,
}: {
  orgId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: detail, isLoading } = useQuery({
    queryKey: ["admin-org", orgId],
    queryFn: () => getAdminOrg(orgId),
    enabled: open,
  });

  const { data: siblings } = useQuery({
    queryKey: ["admin-orgs-by-owner", detail?.owner_email],
    queryFn: () => listAdminOrgs({ q: detail?.owner_email ?? "" }),
    enabled: open && Boolean(detail?.owner_email),
  });
  const otherOrgs = (siblings ?? []).filter((o) => o.id !== orgId && o.owner_email === detail?.owner_email);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        {isLoading || !detail ? (
          <div className="space-y-3 py-6" aria-busy="true">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-24 rounded-lg" />
            <Skeleton className="h-32 rounded-lg" />
          </div>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {detail.name} <PlanBadge plan={detail.plan} />
              </DialogTitle>
            </DialogHeader>

            <div className="space-y-5">
              <section className="grid grid-cols-2 gap-3 rounded-lg border border-border bg-surface-2/40 p-4 text-sm sm:grid-cols-4">
                <div>
                  <div className="text-xs font-bold uppercase tracking-wide text-faint">Owner</div>
                  <div className="text-text">{detail.owner_email ?? "—"}</div>
                </div>
                <div>
                  <div className="text-xs font-bold uppercase tracking-wide text-faint">Expires</div>
                  <div className="text-text">
                    {detail.plan_expires_at ? new Date(detail.plan_expires_at).toLocaleDateString() : "no expiry"}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-bold uppercase tracking-wide text-faint">Payment</div>
                  <PaymentBadge state={detail.payment_state} />
                </div>
                <div>
                  <div className="text-xs font-bold uppercase tracking-wide text-faint">Missed (cap)</div>
                  <div className={detail.unanswered_messages > 0 ? "font-bold text-warn-text" : "text-text"}>
                    {detail.unanswered_messages}
                  </div>
                </div>
                {detail.plan_note && (
                  <div className="col-span-2 sm:col-span-4">
                    <div className="text-xs font-bold uppercase tracking-wide text-faint">Note</div>
                    <div className="text-text">{detail.plan_note}</div>
                  </div>
                )}
              </section>

              <section className="grid grid-cols-2 gap-4">
                <div>
                  <div className="mb-1 text-xs font-bold uppercase tracking-wide text-faint">Messages</div>
                  <UsageMeter used={detail.messages_used} limit={detail.messages_limit} label="Messages used" />
                </div>
                <div>
                  <div className="mb-1 text-xs font-bold uppercase tracking-wide text-faint">Storage</div>
                  <UsageMeter
                    used={detail.storage_bytes_used}
                    limit={detail.storage_bytes_limit}
                    label="Storage used"
                    format={formatBytes}
                  />
                </div>
              </section>

              <section>
                <h4 className="mb-2 text-sm font-bold text-text">Grant history</h4>
                {detail.recent_grants.length === 0 ? (
                  <p className="text-sm text-muted">No grants recorded yet.</p>
                ) : (
                  <ul className="max-h-48 space-y-2 overflow-y-auto">
                    {detail.recent_grants.map((g) => (
                      <li key={g.id} className="rounded-md border border-border p-2.5 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-bold text-text">
                            {g.action}
                            {g.from_plan && g.to_plan ? ` — ${g.from_plan} → ${g.to_plan}` : g.to_plan ? ` — ${g.to_plan}` : ""}
                          </span>
                          <span className="text-faint">{new Date(g.created_at).toLocaleDateString()}</span>
                        </div>
                        <div className="mt-1 text-muted">
                          {g.actor_email ?? "system"}
                          {g.amount_minor ? ` · ${formatMoney(g.amount_minor, g.currency)}` : ""}
                          {g.expires_at ? ` · expires ${new Date(g.expires_at).toLocaleDateString()}` : ""}
                        </div>
                        {g.note && <div className="mt-1 text-faint">{g.note}</div>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section>
                <h4 className="mb-2 text-sm font-bold text-text">Payment history</h4>
                {detail.billing_cycles.length === 0 ? (
                  <p className="text-sm text-muted">No billing cycles yet.</p>
                ) : (
                  <ul className="max-h-48 space-y-2 overflow-y-auto">
                    {detail.billing_cycles.map((c) => (
                      <li key={c.id} className="flex items-center justify-between gap-2 rounded-md border border-border p-2.5 text-xs">
                        <div>
                          <div className="font-bold text-text">
                            {new Date(c.period_start).toLocaleDateString()} – {new Date(c.period_end).toLocaleDateString()}
                          </div>
                          <div className="text-muted">
                            {formatMoney(c.amount_minor, c.currency)}
                            {c.method ? ` · ${c.method}` : ""}
                            {c.reference ? ` · ${c.reference}` : ""}
                          </div>
                        </div>
                        <PaymentBadge state={c.payment_state} />
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {otherOrgs.length > 0 && (
                <section>
                  <h4 className="mb-2 text-sm font-bold text-text">Other workspaces owned by this client</h4>
                  <ul className="space-y-1.5">
                    {otherOrgs.map((o) => (
                      <li key={o.id} className="flex items-center gap-2 text-sm">
                        <span className="text-text">{o.name}</span>
                        <PlanBadge plan={o.plan} />
                        <PaymentBadge state={o.payment_state} />
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
