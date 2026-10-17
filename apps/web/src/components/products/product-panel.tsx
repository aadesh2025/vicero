"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, ImagePlus, Loader2, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import {
  MAX_PRODUCT_IMAGES,
  PRODUCT_CURRENCIES,
  createProduct,
  deleteProduct,
  deleteProductImage,
  getProduct,
  minorToInput,
  parsePriceToMinor,
  reorderProductImages,
  updateProduct,
  uploadProductImage,
  type ApiProduct,
  type ProductInput,
} from "@/lib/api/products";

interface Draft {
  name: string;
  sku: string;
  price: string;
  currency: string;
  description: string;
  attributes: { key: string; value: string }[];
  inStock: boolean;
  url: string;
}

function toDraft(p: ApiProduct | null): Draft {
  return {
    name: p?.name ?? "",
    sku: p?.sku ?? "",
    price: minorToInput(p?.price_minor ?? null),
    currency: p?.currency ?? "INR",
    description: p?.description ?? "",
    attributes: Object.entries(p?.attributes ?? {}).map(([key, value]) => ({ key, value })),
    inStock: p?.in_stock ?? true,
    url: p?.url ?? "",
  };
}

/** Right-hand editor for one product: details on top, photos below. Used for both create and edit. */
export function ProductPanel({
  productId,
  canWrite,
  onClose,
  onCreated,
}: {
  productId: string | null;
  canWrite: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const { data: product, isLoading } = useQuery({
    queryKey: ["product", productId],
    queryFn: () => getProduct(productId as string),
    enabled: productId !== null,
  });

  if (productId !== null && (isLoading || !product)) {
    return (
      <aside
        aria-label="Product details"
        className="flex w-[380px] shrink-0 items-center justify-center border-l border-border bg-surface"
      >
        <Loader2 className="size-5 animate-spin text-faint" aria-label="Loading product" />
      </aside>
    );
  }
  // Keyed on the server's last-updated stamp so a fresh server copy resets the form, but typing never does.
  return (
    <ProductForm
      key={product?.id ?? "new"}
      product={product ?? null}
      canWrite={canWrite}
      onClose={onClose}
      onCreated={onCreated}
    />
  );
}

function ProductForm({
  product,
  canWrite,
  onClose,
  onCreated,
}: {
  product: ApiProduct | null;
  canWrite: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<Draft>(() => toDraft(product));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => {
    setDraft((d) => ({ ...d, [k]: v }));
    setSaved(false);
  };

  const refresh = (p: ApiProduct) => {
    qc.setQueryData(["product", p.id], p);
    qc.invalidateQueries({ queryKey: ["products"] });
  };

  const buildInput = (): ProductInput | null => {
    const price = parsePriceToMinor(draft.price);
    if (price === undefined) {
      setError("Price must be a number like 1299 or 1299.50.");
      return null;
    }
    if (!draft.name.trim()) {
      setError("Give the product a name.");
      return null;
    }
    const attributes: Record<string, string> = {};
    for (const { key, value } of draft.attributes) {
      if (key.trim()) attributes[key.trim()] = value.trim();
    }
    setError(null);
    return {
      name: draft.name.trim(),
      sku: draft.sku.trim() || null,
      price_minor: price,
      currency: price === null ? null : draft.currency,
      description: draft.description.trim(),
      attributes,
      in_stock: draft.inStock,
      url: draft.url.trim() || null,
    };
  };

  const save = useMutation({
    mutationFn: async () => {
      const input = buildInput();
      if (!input) throw new Error("");
      return product ? updateProduct(product.id, input) : createProduct(input);
    },
    onSuccess: (p) => {
      refresh(p);
      setSaved(true);
      if (!product) onCreated(p.id);
    },
    onError: (e) => {
      const message = (e as Error).message;
      if (message) setError(message);
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteProduct((product as ApiProduct).id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["products"] });
      onClose();
    },
    onError: (e) => setError((e as Error).message),
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      let latest = product as ApiProduct;
      for (const file of files) latest = await uploadProductImage(latest.id, file);
      return latest;
    },
    onSuccess: (p) => {
      setError(null);
      refresh(p);
    },
    onError: (e) => setError((e as Error).message),
  });

  const removeImage = useMutation({
    mutationFn: (imageId: string) => deleteProductImage((product as ApiProduct).id, imageId),
    onSuccess: refresh,
    onError: (e) => setError((e as Error).message),
  });

  const reorder = useMutation({
    mutationFn: (ids: string[]) => reorderProductImages((product as ApiProduct).id, ids),
    onSuccess: refresh,
    onError: (e) => setError((e as Error).message),
  });

  const images = product?.images ?? [];
  const move = (index: number, by: -1 | 1) => {
    const ids = images.map((i) => i.id);
    const target = index + by;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    reorder.mutate(ids);
  };

  const readOnly = !canWrite;

  return (
    <aside
      aria-label="Product details"
      className="flex max-h-[calc(100vh-12rem)] w-[380px] shrink-0 flex-col overflow-y-auto border-l border-border bg-surface"
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-extrabold text-text">{product ? "Edit product" : "New product"}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-md p-1 text-faint hover:bg-surface-2 hover:text-text"
        >
          <X className="size-4" />
        </button>
      </div>

      <form
        className="space-y-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!readOnly) save.mutate();
        }}
      >
        <Field id="product-name" label="Name">
          <Input
            id="product-name"
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
            disabled={readOnly}
            placeholder="Black cotton kurta"
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field id="product-sku" label="SKU">
            <Input
              id="product-sku"
              value={draft.sku}
              onChange={(e) => set("sku", e.target.value)}
              disabled={readOnly}
              placeholder="KUR-001"
            />
          </Field>
          <Field id="product-price" label="Price">
            <div className="flex gap-1.5">
              <Input
                id="product-price"
                inputMode="decimal"
                value={draft.price}
                onChange={(e) => set("price", e.target.value)}
                disabled={readOnly}
                placeholder="1299"
              />
              <select
                aria-label="Currency"
                value={draft.currency}
                onChange={(e) => set("currency", e.target.value)}
                disabled={readOnly}
                className="h-[38px] rounded-lg border border-border-strong bg-surface px-1.5 text-sm font-medium text-text"
              >
                {PRODUCT_CURRENCIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          </Field>
        </div>

        <Field id="product-description" label="Details">
          <Textarea
            id="product-description"
            value={draft.description}
            onChange={(e) => set("description", e.target.value)}
            disabled={readOnly}
            placeholder="Material, fit, care, anything a customer might ask about."
            rows={4}
          />
        </Field>

        <fieldset className="space-y-2">
          <legend className="mb-1 block text-xs text-muted">Attributes</legend>
          {draft.attributes.map((a, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <Input
                aria-label={`Attribute ${i + 1} name`}
                placeholder="Size"
                value={a.key}
                disabled={readOnly}
                onChange={(e) =>
                  set(
                    "attributes",
                    draft.attributes.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)),
                  )
                }
              />
              <Input
                aria-label={`Attribute ${i + 1} value`}
                placeholder="M, L, XL"
                value={a.value}
                disabled={readOnly}
                onChange={(e) =>
                  set(
                    "attributes",
                    draft.attributes.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)),
                  )
                }
              />
              {!readOnly && (
                <button
                  type="button"
                  aria-label={`Remove attribute ${i + 1}`}
                  onClick={() =>
                    set(
                      "attributes",
                      draft.attributes.filter((_, j) => j !== i),
                    )
                  }
                  className="rounded-md p-1.5 text-faint hover:bg-surface-2 hover:text-text"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          ))}
          {!readOnly && draft.attributes.length < 20 && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => set("attributes", [...draft.attributes, { key: "", value: "" }])}
            >
              <Plus className="size-3.5" /> Add attribute
            </Button>
          )}
        </fieldset>

        <Field id="product-url" label="Buy link">
          <Input
            id="product-url"
            type="url"
            value={draft.url}
            onChange={(e) => set("url", e.target.value)}
            disabled={readOnly}
            placeholder="https://yourshop.com/kurta"
          />
        </Field>

        <div className="flex items-center justify-between rounded-md border border-border bg-surface-2/50 px-3 py-2">
          <label htmlFor="product-stock" className="text-sm font-medium text-text">
            In stock
          </label>
          <Switch
            id="product-stock"
            checked={draft.inStock}
            disabled={readOnly}
            onCheckedChange={(v) => set("inStock", v)}
          />
        </div>

        {error && (
          <p role="alert" className="text-sm text-error-text">
            {error}
          </p>
        )}

        {!readOnly && (
          <div className="flex items-center gap-2">
            <Button type="submit" variant="primary" className="flex-1" disabled={save.isPending}>
              {save.isPending && <Loader2 className="size-4 animate-spin" />}
              {product ? "Save changes" : "Create product"}
            </Button>
            {product && (
              <Button
                type="button"
                variant="outline"
                aria-label="Delete product"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm(`Delete “${product.name}”? Your agent will stop offering it.`)) remove.mutate();
                }}
              >
                <Trash2 className="size-4" />
              </Button>
            )}
          </div>
        )}
        {saved && (
          <p role="status" className="text-xs font-semibold text-success-text">
            Saved.
          </p>
        )}
      </form>

      <section aria-label="Photos" className="space-y-2 border-t border-border p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-extrabold uppercase tracking-[0.08em] text-faint">Photos</h3>
          <span className="text-xs text-muted">
            {images.length}/{MAX_PRODUCT_IMAGES}
          </span>
        </div>

        {!product && <p className="text-sm text-muted">Create the product first, then add photos.</p>}

        {product && images.length === 0 && <p className="text-sm text-muted">No photos yet.</p>}

        {images.length > 0 && (
          <ul className="grid grid-cols-3 gap-2">
            {images.map((img, i) => (
              <li key={img.id} className="group relative overflow-hidden rounded-md border border-border">
                {/* eslint-disable-next-line @next/next/no-img-element -- signed API URL, not a static asset */}
                <img
                  src={img.url}
                  alt={img.alt_text ?? `${product?.name ?? "Product"} photo ${i + 1}`}
                  className="aspect-square w-full object-cover"
                />
                {!readOnly && (
                  <div className="absolute inset-x-0 bottom-0 flex justify-between bg-black/55 px-1 py-0.5 text-white">
                    <button
                      type="button"
                      aria-label={`Move photo ${i + 1} left`}
                      disabled={i === 0 || reorder.isPending}
                      onClick={() => move(i, -1)}
                      className="rounded p-0.5 hover:bg-white/20 disabled:opacity-30"
                    >
                      <ArrowLeft className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete photo ${i + 1}`}
                      disabled={removeImage.isPending}
                      onClick={() => removeImage.mutate(img.id)}
                      className="rounded p-0.5 hover:bg-white/20"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Move photo ${i + 1} right`}
                      disabled={i === images.length - 1 || reorder.isPending}
                      onClick={() => move(i, 1)}
                      className="rounded p-0.5 hover:bg-white/20 disabled:opacity-30"
                    >
                      <ArrowRight className="size-3.5" />
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {product && !readOnly && images.length < MAX_PRODUCT_IMAGES && (
          <>
            <input
              ref={fileInput}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              multiple
              className="sr-only"
              aria-label="Upload photos"
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []).slice(0, MAX_PRODUCT_IMAGES - images.length);
                e.target.value = "";
                if (files.length) upload.mutate(files);
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={upload.isPending}
              onClick={() => fileInput.current?.click()}
            >
              {upload.isPending ? <Loader2 className="size-4 animate-spin" /> : <ImagePlus className="size-4" />}
              Add photos
            </Button>
          </>
        )}
      </section>
    </aside>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs text-muted">
        {label}
      </label>
      {children}
    </div>
  );
}
