"use client";

import { api, apiForm, type UploadProgress } from "./client";
import type {
  ApiChunk,
  ApiDocument,
  ApiKnowledgeBase,
  ApiSearchResponse,
} from "./types";

// ── Knowledge bases ─────────────────────────────────────────────────────────
export function listKnowledgeBases() {
  return api<ApiKnowledgeBase[]>("/v1/knowledge", { orgScoped: true });
}

export function getKnowledgeBase(id: string) {
  return api<ApiKnowledgeBase>(`/v1/knowledge/${id}`, { orgScoped: true });
}

export function createKnowledgeBase(body: {
  name: string;
  description?: string;
  embedding_provider?: string;
  embedding_model?: string;
}) {
  return api<ApiKnowledgeBase>("/v1/knowledge", { method: "POST", orgScoped: true, body });
}

export function updateKnowledgeBase(id: string, body: Record<string, unknown>) {
  return api<ApiKnowledgeBase>(`/v1/knowledge/${id}`, { method: "PATCH", orgScoped: true, body });
}

export function deleteKnowledgeBase(id: string) {
  return api<void>(`/v1/knowledge/${id}`, { method: "DELETE", orgScoped: true });
}

// ── Documents ───────────────────────────────────────────────────────────────
export function listDocuments(kbId: string) {
  return api<ApiDocument[]>(`/v1/knowledge/${kbId}/documents`, { orgScoped: true });
}

export function addTextDocument(kbId: string, body: { text: string; filename?: string }) {
  return api<ApiDocument>(`/v1/knowledge/${kbId}/documents`, {
    method: "POST",
    orgScoped: true,
    body: { source_type: "text", ...body },
  });
}

export function addUrlDocument(kbId: string, url: string) {
  return api<ApiDocument>(`/v1/knowledge/${kbId}/documents`, {
    method: "POST",
    orgScoped: true,
    body: { source_type: "url", url },
  });
}

/** Crawl a whole site (sitemap + same-site links) into one document, one citation per page. */
export function addWebsiteDocument(kbId: string, url: string, maxPages: number) {
  return api<ApiDocument>(`/v1/knowledge/${kbId}/documents`, {
    method: "POST",
    orgScoped: true,
    body: { source_type: "website", url, max_pages: maxPages },
  });
}

export function uploadDocument(kbId: string, file: File, onProgress?: UploadProgress) {
  const form = new FormData();
  form.append("file", file);
  return apiForm<ApiDocument>(`/v1/knowledge/${kbId}/documents/upload`, form, onProgress);
}

export function reingestDocument(documentId: string) {
  return api<ApiDocument>(`/v1/knowledge/documents/${documentId}/reingest`, {
    method: "POST",
    orgScoped: true,
  });
}

export function deleteDocument(documentId: string) {
  return api<void>(`/v1/knowledge/documents/${documentId}`, { method: "DELETE", orgScoped: true });
}

export function listChunks(documentId: string) {
  return api<ApiChunk[]>(`/v1/knowledge/documents/${documentId}/chunks`, { orgScoped: true });
}

// ── Retrieval ───────────────────────────────────────────────────────────────
export function searchKnowledgeBase(
  kbId: string,
  body: { query: string; top_k?: number; score_threshold?: number; hybrid?: boolean },
) {
  return api<ApiSearchResponse>(`/v1/knowledge/${kbId}/search`, {
    method: "POST",
    orgScoped: true,
    body,
  });
}
