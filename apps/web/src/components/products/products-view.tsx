"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ImageIcon, Package, Plus, Search } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ProductPanel } from "@/components/products/product-panel";
import { listProducts } from "@/lib/api/products";
import { hasPermission, useRole } from "@/lib/rbac";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/store/session";

const PAGE_SIZE = 25;

export function ProductsView() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canWrite = hasPermission(useRole(), "tools:manage");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  // `null` = nothing open, "new" = creating, otherwise the id being edited.
  const [selected, setSelected] = useState<string | "new" | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["products", activeOrgId, q, page],
    queryFn: () => listProducts({ query: q || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    enabled: Boolean(activeOrgId),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader
        title="Products"
        description="What your agent can look up and show customers. Turn it on per agent under Model → Capabilities."
      >
        {canWrite && (
          <Button variant="primary" size="default" onClick={() => setSelected("new")}>
            <Plus className="size-4" /> New product
          </Button>
        )}
      </PageHeader>

      <div className="relative max-w-md">
        <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
        <Input
          aria-label="Search products"
          placeholder="Search by name, SKU or description…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(0);
          }}
          className="pl-8"
        />
      </div>

      <div className="flex overflow-hidden rounded-card border border-border bg-surface">
        <div className="min-w-0 flex-1 overflow-x-auto">
          {isLoading && (
            <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading products">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          )}

          {isError && (
            <div className="flex flex-col items-center gap-3 px-4 py-10 text-center" role="alert">
              <p className="text-sm text-error-text">Couldn&apos;t load your products.</p>
              <Button variant="outline" size="sm" onClick={() => refetch()}>
                Try again
              </Button>
            </div>
          )}

          {!isLoading && !isError && items.length === 0 && (
            <div className="flex flex-col items-center gap-3 px-4 py-14 text-center">
              <span className="grid size-11 place-items-center rounded-full bg-surface-2 text-faint">
                <Package className="size-5" aria-hidden />
              </span>
              <p className="text-sm font-semibold text-text">
                {q ? "No products match that search." : "No products yet."}
              </p>
              {!q && (
                <p className="max-w-sm text-sm text-muted">
                  Add what you sell — name, price, details and up to 5 photos — and your agent can answer questions
                  about it and send the photos on WhatsApp, Instagram and Messenger.
                </p>
              )}
              {!q && canWrite && (
                <Button variant="primary" size="sm" onClick={() => setSelected("new")}>
                  <Plus className="size-4" /> Add your first product
                </Button>
              )}
            </div>
          )}

          {!isLoading && !isError && items.length > 0 && (
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-[10.5px] font-extrabold uppercase tracking-[0.08em] text-faint">
                  <th scope="col" className="px-4 py-2.5">Product</th>
                  <th scope="col" className="px-3 py-2.5">SKU</th>
                  <th scope="col" className="px-3 py-2.5 text-right">Price</th>
                  <th scope="col" className="px-4 py-2.5 text-right">Stock</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {items.map((p) => (
                  <tr
                    key={p.id}
                    onClick={() => setSelected(p.id)}
                    className={`cursor-pointer transition-colors hover:bg-surface-2 ${
                      selected === p.id ? "bg-surface-2" : ""
                    }`}
                  >
                    <th scope="row" className="px-4 py-2.5 text-left font-normal">
                      <span className="flex items-center gap-3">
                        {p.images[0] ? (
                          // eslint-disable-next-line @next/next/no-img-element -- signed API URL, not a static asset
                          <img
                            src={p.images[0].url}
                            alt={p.images[0].alt_text ?? p.name}
                            className="size-10 rounded-md border border-border object-cover"
                          />
                        ) : (
                          <span className="grid size-10 place-items-center rounded-md border border-dashed border-border text-faint">
                            <ImageIcon className="size-4" aria-hidden />
                          </span>
                        )}
                        <span className="min-w-0">
                          <span className="block truncate font-bold text-text">{p.name}</span>
                          <span className="block text-[11px] font-semibold text-faint">
                            {p.images.length} {p.images.length === 1 ? "photo" : "photos"}
                          </span>
                        </span>
                      </span>
                    </th>
                    <td className="whitespace-nowrap px-3 py-2.5 font-mono text-xs text-muted">{p.sku ?? "—"}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right font-semibold tabular-nums text-text">
                      {p.price_minor !== null && p.currency ? formatMoney(p.price_minor, p.currency) : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <Badge variant={p.in_stock ? "success" : "default"}>{p.in_stock ? "In stock" : "Out"}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between border-t border-border px-4 py-2.5 text-xs font-semibold text-muted">
              <span>
                {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </span>
              <span className="flex gap-2">
                <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </span>
            </div>
          )}
        </div>

        {selected && (
          <ProductPanel
            key={selected}
            productId={selected === "new" ? null : selected}
            canWrite={canWrite}
            onClose={() => setSelected(null)}
            onCreated={(id) => setSelected(id)}
          />
        )}
      </div>
    </div>
  );
}
