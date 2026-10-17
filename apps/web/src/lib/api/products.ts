"use client";

import { api, apiForm } from "./client";

export const PRODUCT_CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED"] as const;
export const MAX_PRODUCT_IMAGES = 5;

export interface ApiProductImage {
  id: string;
  media_id: string;
  /** A signed, expiring link usable in an <img> tag. */
  url: string;
  position: number;
  alt_text: string | null;
}

export interface ApiProduct {
  id: string;
  name: string;
  sku: string | null;
  price_minor: number | null;
  currency: string | null;
  description: string;
  attributes: Record<string, string>;
  in_stock: boolean;
  url: string | null;
  is_active: boolean;
  images: ApiProductImage[];
  created_at: string;
  updated_at: string;
}

export interface ProductListResult {
  items: ApiProduct[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProductInput {
  name: string;
  sku: string | null;
  price_minor: number | null;
  currency: string | null;
  description: string;
  attributes: Record<string, string>;
  in_stock: boolean;
  url: string | null;
}

export function listProducts(filters: { query?: string; limit?: number; offset?: number } = {}) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== "") params.set(k, String(v));
  }
  const qs = params.size ? `?${params}` : "";
  return api<ProductListResult>(`/v1/products${qs}`, { orgScoped: true });
}

export function getProduct(id: string) {
  return api<ApiProduct>(`/v1/products/${id}`, { orgScoped: true });
}

export function createProduct(body: ProductInput) {
  return api<ApiProduct>("/v1/products", { method: "POST", body, orgScoped: true });
}

export function updateProduct(id: string, body: Partial<ProductInput>) {
  return api<ApiProduct>(`/v1/products/${id}`, { method: "PATCH", body, orgScoped: true });
}

export function deleteProduct(id: string) {
  return api<void>(`/v1/products/${id}`, { method: "DELETE", orgScoped: true });
}

export function uploadProductImage(productId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiForm<ApiProduct>(`/v1/products/${productId}/images`, form);
}

export function deleteProductImage(productId: string, imageId: string) {
  return api<ApiProduct>(`/v1/products/${productId}/images/${imageId}`, { method: "DELETE", orgScoped: true });
}

export function reorderProductImages(productId: string, imageIds: string[]) {
  return api<ApiProduct>(`/v1/products/${productId}/images/order`, {
    method: "PUT",
    body: { image_ids: imageIds },
    orgScoped: true,
  });
}

/** "1299.50" -> 129950. Blank is "no price". NaN / negative is rejected by returning `undefined`. */
export function parsePriceToMinor(input: string): number | null | undefined {
  const text = input.trim().replace(/,/g, "");
  if (!text) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return undefined;
  return Math.round(parseFloat(text) * 100);
}

export function minorToInput(minor: number | null): string {
  if (minor === null) return "";
  return minor % 100 === 0 ? String(minor / 100) : (minor / 100).toFixed(2);
}
