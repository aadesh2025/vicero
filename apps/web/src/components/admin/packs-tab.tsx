"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { listPacks, markPackInvoiced } from "@/lib/api/admin";
import { formatMoney } from "@/lib/money";

/** Packs sold but not yet invoiced — the monthly reconciliation view (docs/22 §8.2, §9.7). */
export function PacksTab() {
  const qc = useQueryClient();
  const { data: packs, isLoading } = useQuery({ queryKey: ["admin-packs"], queryFn: () => listPacks(false) });
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const markInvoiced = useMutation({
    mutationFn: (id: string) => markPackInvoiced(id, true),
    onMutate: (id) => setPendingId(id),
    onSuccess: async () => {
      setError(null);
      await qc.invalidateQueries({ queryKey: ["admin-packs"] });
    },
    onError: (e: Error) => setError(e.message),
    onSettled: () => setPendingId(null),
  });

  if (isLoading) {
    return <p className="p-8 text-center text-sm text-muted">Loading uninvoiced packs…</p>;
  }

  const rows = packs ?? [];

  return (
    <section className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border p-4">
        <h3 className="font-display text-sm font-bold text-text">Uninvoiced packs</h3>
        <p className="text-xs text-muted">Packs sold but not yet billed — for monthly reconciliation.</p>
      </div>

      {error && (
        <p role="alert" className="px-4 pt-3 text-sm text-error-text">
          {error}
        </p>
      )}

      {rows.length === 0 ? (
        <p className="p-8 text-center text-sm text-muted">Nothing to invoice — every pack sold is already billed.</p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <div>
                <div className="text-sm font-medium text-text">{p.organization_name ?? p.organization_id}</div>
                <div className="text-xs text-faint">
                  {p.extra_messages?.toLocaleString() ?? "—"} messages ·{" "}
                  {p.amount_minor !== null ? formatMoney(p.amount_minor, p.currency) : "—"} ·{" "}
                  {new Date(p.created_at).toLocaleDateString()}
                  {p.note ? ` · ${p.note}` : ""}
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={markInvoiced.isPending && pendingId === p.id}
                onClick={() => markInvoiced.mutate(p.id)}
              >
                {markInvoiced.isPending && pendingId === p.id && <Loader2 className="size-4 animate-spin" />} Mark invoiced
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
