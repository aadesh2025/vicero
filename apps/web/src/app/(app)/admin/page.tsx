"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, Building2, CircleDollarSign, Database, MessagesSquare, Server, Users, Zap } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { StatCard } from "@/components/dashboard/stat-card";
import { AutomationsTable } from "@/components/admin/automations-table";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  getAdminHealth,
  getPlatformUsage,
  listAdminOrgs,
  listAdminUsers,
  listFeatureFlags,
  upsertFeatureFlag,
  type FeatureFlag,
} from "@/lib/api/admin";
import { useSession } from "@/lib/store/session";
import { compact, usd } from "@/lib/utils";

export default function AdminPage() {
  const router = useRouter();
  const { user, ready } = useSession();
  const staff = Boolean(user?.is_staff);

  // Route guard: real enforcement is the API's 403, but non-staff shouldn't see the console.
  useEffect(() => {
    if (ready && !staff) router.replace("/dashboard");
  }, [ready, staff, router]);

  const { data: usage } = useQuery({ queryKey: ["admin-usage"], queryFn: getPlatformUsage, enabled: staff });
  const { data: health } = useQuery({ queryKey: ["admin-health"], queryFn: getAdminHealth, enabled: staff, refetchInterval: 15_000 });
  const { data: orgs } = useQuery({ queryKey: ["admin-orgs"], queryFn: listAdminOrgs, enabled: staff });
  const { data: users } = useQuery({ queryKey: ["admin-users"], queryFn: listAdminUsers, enabled: staff });

  if (!ready || !staff) {
    return <div className="grid min-h-[40vh] place-items-center text-sm text-muted">Checking access…</div>;
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader title="Platform admin" description="Cross-tenant operations. Visible to platform staff only.">
        <Badge variant="accent">
          <Server className="size-3" /> staff
        </Badge>
      </PageHeader>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Organizations" value={compact(usage?.organizations ?? 0)} icon={Building2} />
        <StatCard label="Users" value={compact(usage?.users ?? 0)} icon={Users} />
        <StatCard label="Agents" value={compact(usage?.agents ?? 0)} icon={Bot} />
        <StatCard label="Conversations" value={compact(usage?.conversations ?? 0)} icon={MessagesSquare} />
        <StatCard
          label="Tokens"
          value={compact((usage?.tokens_prompt ?? 0) + (usage?.tokens_completion ?? 0))}
          icon={Zap}
          hint="prompt + completion"
        />
        <StatCard label="Spend" value={usd((usage?.cost_micros ?? 0) / 1_000_000)} icon={CircleDollarSign} invertDelta />
      </div>

      <HealthPanel health={health} />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <TopOrgs rows={usage?.top_orgs ?? []} />
        <FeatureFlags />
      </div>

      <AutomationsTable />

      <OrgsTable orgs={orgs ?? []} />
      <UsersTable users={users ?? []} />
    </div>
  );
}

function HealthPill({ ok, label, icon: Icon }: { ok: boolean; label: string; icon: typeof Database }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-surface-2 px-3 py-2">
      <Icon className="size-4 text-faint" />
      <span className="text-sm text-text">{label}</span>
      <Badge variant={ok ? "success" : "error"} className="ml-auto">
        {ok ? "healthy" : "down"}
      </Badge>
    </div>
  );
}

function HealthPanel({ health }: { health: { database: boolean; redis: boolean } | undefined }) {
  return (
    <section className="rounded-lg border border-border bg-surface p-5">
      <div className="mb-4 flex items-center gap-2">
        <Activity className="size-4 text-accent" />
        <h3 className="font-display text-base font-semibold text-text">System health</h3>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <HealthPill ok={Boolean(health?.database)} label="PostgreSQL" icon={Database} />
        <HealthPill ok={Boolean(health?.redis)} label="Redis" icon={Server} />
      </div>
    </section>
  );
}

function TopOrgs({ rows }: { rows: { organization_id: string; name: string; tokens_prompt: number; tokens_completion: number; cost_micros: number }[] }) {
  return (
    <section className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border p-5">
        <h3 className="font-display text-base font-semibold text-text">Top orgs by tokens</h3>
        <p className="text-sm text-muted">Highest consumption across the platform.</p>
      </div>
      <ul className="divide-y divide-border">
        {rows.length === 0 && <li className="px-5 py-4 text-sm text-muted">No usage recorded yet.</li>}
        {rows.map((r, i) => (
          <li key={r.organization_id} className="flex items-center gap-3 px-5 py-3">
            <span className="font-mono text-xs text-faint">{String(i + 1).padStart(2, "0")}</span>
            <span className="flex-1 truncate text-sm text-text">{r.name}</span>
            <span className="font-mono text-sm text-muted">{compact(r.tokens_prompt + r.tokens_completion)} tok</span>
            <span className="font-mono text-xs text-faint">{usd(r.cost_micros / 1_000_000)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function FeatureFlags() {
  const qc = useQueryClient();
  const { data: flags } = useQuery({ queryKey: ["admin-flags"], queryFn: listFeatureFlags });
  const toggle = useMutation({
    mutationFn: (f: FeatureFlag) => upsertFeatureFlag(f.key, !f.enabled, f.description),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-flags"] }),
  });

  return (
    <section className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border p-5">
        <h3 className="font-display text-base font-semibold text-text">Feature flags</h3>
        <p className="text-sm text-muted">Platform-wide rollout switches.</p>
      </div>
      <ul className="divide-y divide-border">
        {(flags ?? []).length === 0 && <li className="px-5 py-4 text-sm text-muted">No flags defined.</li>}
        {(flags ?? []).map((f) => (
          <li key={f.key} className="flex items-center gap-3 px-5 py-3">
            <div className="min-w-0 flex-1">
              <div className="font-mono text-sm text-text">{f.key}</div>
              {f.description && <div className="truncate text-xs text-faint">{f.description}</div>}
            </div>
            <Switch checked={f.enabled} disabled={toggle.isPending} onCheckedChange={() => toggle.mutate(f)} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function OrgsTable({
  orgs,
}: {
  orgs: {
    id: string;
    name: string;
    slug: string | null;
    members: number;
    member_list?: { email: string; role: string; status: string; is_staff: boolean }[];
    agents: number;
    agents_with_unpublished_changes?: number;
    workflows_awaiting_review?: number;
    deleted: boolean;
    created_at: string;
  }[];
}) {
  return (
    <section className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border p-5">
        <h3 className="font-display text-base font-semibold text-text">Organizations</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-faint">
              <th className="px-5 py-3 font-medium">Name</th>
              <th className="px-5 py-3 font-medium">Slug</th>
              <th className="px-5 py-3 font-medium">Members &amp; access</th>
              <th className="px-5 py-3 text-right font-medium">Agents</th>
              <th className="px-5 py-3 text-right font-medium">Awaiting review</th>
              <th className="px-5 py-3 text-right font-medium">Workflow review</th>
              <th className="px-5 py-3 font-medium">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {orgs.length === 0 && (
              <tr>
                <td colSpan={6} className="px-5 py-4 text-muted">No organizations.</td>
              </tr>
            )}
            {orgs.map((o) => (
              <tr key={o.id} className="hover:bg-surface-2/50">
                <td className="px-5 py-3 text-text">
                  <span className="flex items-center gap-2">
                    {o.name}
                    {o.deleted && <Badge variant="error">deleted</Badge>}
                  </span>
                </td>
                <td className="px-5 py-3 font-mono text-xs text-muted">{o.slug ?? "—"}</td>
                <td className="px-5 py-3">
                  {/* Who can reach this client's agent, and at what level. A bare count
                      answered "how many", which was never the question staff had. */}
                  {(o.member_list ?? []).length === 0 ? (
                    <span className="text-faint">{o.members || "no members"}</span>
                  ) : (
                    <ul className="space-y-1">
                      {(o.member_list ?? []).map((m) => (
                        <li key={`${o.id}-${m.email}`} className="flex items-center gap-2">
                          <span className="font-mono text-xs text-text">{m.email}</span>
                          <RoleChip role={m.role} />
                          {m.is_staff && (
                            <span className="rounded border border-border px-1 py-0.5 text-[10px] text-faint">
                              staff
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="px-5 py-3 text-right font-mono text-muted">{o.agents}</td>
                <td className="px-5 py-3 text-right">
                  {/* Clients can save but not publish — this is how staff notice work waiting. */}
                  {o.agents_with_unpublished_changes ? (
                    <Badge variant="warn">{o.agents_with_unpublished_changes} unpublished</Badge>
                  ) : (
                    <span className="font-mono text-muted">—</span>
                  )}
                </td>
                <td className="px-5 py-3 text-right">
                  {/* docs/17 Phase 4: unlike the agents column, this reads a real status a
                      workflow author set (WorkflowVersion.status == "in_review"), not a
                      version-number proxy. */}
                  {o.workflows_awaiting_review ? (
                    <Badge variant="warn">{o.workflows_awaiting_review} in review</Badge>
                  ) : (
                    <span className="font-mono text-muted">—</span>
                  )}
                </td>
                <td className="px-5 py-3 text-faint">{new Date(o.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type AdminUser = {
  id: string;
  email: string;
  is_staff: boolean;
  is_active: boolean;
  orgs: number;
  memberships?: { organization_id: string; organization_name: string; role: string }[];
  created_at: string;
};

/** Role colours, ordered by how much damage the role can do. `owner` and `admin` can publish
 *  to a client's live agent; `viewer` cannot. Staff scanning this list are looking for the
 *  first two, so those are the ones that carry colour. */
const ROLE_STYLE: Record<string, string> = {
  owner: "border-ember/40 bg-ember/10 text-accent",
  admin: "border-warn/40 bg-warn/10 text-warn-text",
  editor: "border-info/30 bg-info/10 text-info-text",
  operator: "border-border bg-surface-2 text-muted",
  viewer: "border-border bg-surface-2 text-faint",
};

function RoleChip({ role }: { role: string }) {
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${ROLE_STYLE[role] ?? ROLE_STYLE.viewer}`}>
      {role}
    </span>
  );
}

function UsersTable({ users }: { users: AdminUser[] }) {
  return (
    <section className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border p-5">
        <h3 className="font-display text-base font-semibold text-text">Users</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-faint">
              <th className="px-5 py-3 font-medium">Email</th>
              <th className="px-5 py-3 font-medium">Status</th>
              <th className="px-5 py-3 font-medium">Organizations &amp; access</th>
              <th className="px-5 py-3 font-medium">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {users.length === 0 && (
              <tr>
                <td colSpan={4} className="px-5 py-4 text-muted">No users.</td>
              </tr>
            )}
            {users.map((u) => (
              <tr key={u.id} className="hover:bg-surface-2/50">
                <td className="px-5 py-3 text-text">
                  <span className="flex items-center gap-2">
                    {u.email}
                    {u.is_staff && <Badge variant="accent">staff</Badge>}
                  </span>
                </td>
                <td className="px-5 py-3">
                  <Badge variant={u.is_active ? "success" : "outline"}>{u.is_active ? "active" : "disabled"}</Badge>
                </td>
                <td className="px-5 py-3">
                  {/* The same relationship from the other side: which client workspaces this
                      address can reach, and at what level. */}
                  {(u.memberships ?? []).length === 0 ? (
                    <span className="text-faint">{u.orgs ? `${u.orgs} org(s)` : "none"}</span>
                  ) : (
                    <ul className="space-y-1">
                      {(u.memberships ?? []).map((m) => (
                        <li key={`${u.id}-${m.organization_id}`} className="flex items-center gap-2">
                          <span className="text-xs text-text">{m.organization_name}</span>
                          <RoleChip role={m.role} />
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="px-5 py-3 text-faint">{new Date(u.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
