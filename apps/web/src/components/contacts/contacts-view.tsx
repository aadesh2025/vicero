"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ContactAvatar } from "@/components/inbox/contact-avatar";
import { ChannelBadge } from "@/components/shared/channel";
import { ContactDetailPanel } from "@/components/contacts/contact-detail-panel";
import { NewContactDialog } from "@/components/contacts/new-contact-dialog";
import { listContacts, LEAD_STAGES } from "@/lib/api/contacts";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

const PAGE_SIZE = 25;

const stageVariant: Record<string, "success" | "warn" | "accent" | "default"> = {
  new: "default",
  contacted: "default",
  qualified: "warn",
  customer: "success",
  lost: "default",
};

export function ContactsView({ initialId }: { initialId?: string }) {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [q, setQ] = useState("");
  const [stage, setStage] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string | null>(initialId ?? null);
  const [adding, setAdding] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["contacts", activeOrgId, q, stage, page],
    queryFn: () =>
      listContacts({ q: q || undefined, lead_stage: stage || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    enabled: Boolean(activeOrgId),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  const applyFilter = (fn: () => void) => {
    fn();
    setPage(0); // a filtered result set has different pages
  };

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader
        title="CRM"
        description="Everyone who has talked to your agents, across every channel."
      >
        <Button variant="primary" size="default" onClick={() => setAdding(true)}>
          <Plus className="size-4" /> New contact
        </Button>
      </PageHeader>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
          <Input
            aria-label="Search contacts"
            placeholder="Search by name or channel ID…"
            value={q}
            onChange={(e) => applyFilter(() => setQ(e.target.value))}
            className="pl-8"
          />
        </div>
        <select
          aria-label="Filter by lead stage"
          value={stage}
          onChange={(e) => applyFilter(() => setStage(e.target.value))}
          className="h-[38px] rounded-lg border border-border-strong bg-surface px-2 text-sm font-medium capitalize text-text"
        >
          <option value="">All stages</option>
          {LEAD_STAGES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <div className="flex overflow-hidden rounded-card border border-border bg-surface">
        <div className="min-w-0 flex-1 overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[10.5px] font-extrabold uppercase tracking-[0.08em] text-faint">
                <th scope="col" className="px-4 py-2.5">Contact</th>
                <th scope="col" className="px-3 py-2.5">Channels</th>
                <th scope="col" className="px-3 py-2.5">Stage</th>
                <th scope="col" className="px-3 py-2.5 text-right">Convos</th>
                <th scope="col" className="px-4 py-2.5 text-right">Last active</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {isLoading && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-muted">Loading…</td>
                </tr>
              )}
              {!isLoading && items.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-muted">
                    No contacts yet — they appear as people message your agents.
                  </td>
                </tr>
              )}
              {items.map((c) => {
                const name = c.display_name || c.email || c.phone || "Unnamed contact";
                // The channel badge shows where they've messaged from — several, once
                // they've been recognised on more than one.
                const primary = c.channels[0]?.channel ?? "manual";
                return (
                  <tr
                    key={c.id}
                    onClick={() => setSelected(c.id)}
                    className={`cursor-pointer transition-colors hover:bg-surface-2 ${
                      selected === c.id ? "bg-surface-2" : ""
                    }`}
                  >
                    <th scope="row" className="px-4 py-2.5 text-left font-normal">
                      <span className="flex items-center gap-2.5">
                        <ContactAvatar
                          channel={primary}
                          name={name}
                          avatarUrl={c.channels[0]?.avatar_url ?? null}
                          size="sm"
                        />
                        <span className="min-w-0">
                          <span className="block truncate font-bold text-text">{name}</span>
                          {c.labels.length > 0 && (
                            <span className="block truncate text-[11px] font-semibold text-faint">
                              {c.labels.join(" · ")}
                            </span>
                          )}
                        </span>
                      </span>
                    </th>
                    <td className="whitespace-nowrap px-3 py-2.5 text-muted">
                      <span className="flex flex-wrap gap-1">
                        {c.channels.length === 0 && <span className="text-xs font-medium text-faint">—</span>}
                        {c.channels.map((ch) => (
                          <ChannelBadge key={ch.id} channel={ch.channel} size="sm" />
                        ))}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      {c.lead_stage ? (
                        <Badge variant={stageVariant[c.lead_stage] ?? "default"} className="capitalize">
                          {c.lead_stage}
                        </Badge>
                      ) : (
                        <span className="text-xs text-faint">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right text-xs font-bold tabular-nums text-muted">
                      {c.conversation_count}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-right text-xs font-semibold text-faint">
                      {c.last_active_at ? relativeTime(c.last_active_at) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between border-t border-border px-4 py-2.5 text-xs font-semibold text-muted">
              <span>
                {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </span>
              <span className="flex gap-2">
                <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= lastPage}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </span>
            </div>
          )}
        </div>

        {selected && <ContactDetailPanel contactId={selected} onClose={() => setSelected(null)} />}
      </div>

      {/* Opens the new contact straight away — you almost always want to fill in more. */}
      <NewContactDialog open={adding} onOpenChange={setAdding} onCreated={setSelected} />
    </div>
  );
}
