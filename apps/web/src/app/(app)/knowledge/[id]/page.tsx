"use client";

import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Eye,
  FileUp,
  Globe,
  Link2,
  Loader2,
  RefreshCw,
  Trash2,
  Type,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { PiiBadge, PiiSummary } from "@/components/knowledge/pii-flags";
import { DeleteKnowledgeBase } from "@/components/knowledge/delete-kb";
import { describeProgress, formatBytes, runLimited } from "@/components/knowledge/ingest-progress";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DocIcon } from "@/components/shared/doc-icon";
import type { DocType } from "@/lib/mock/knowledge";
import type { ApiDocStatus, ApiDocument } from "@/lib/api/types";
import {
  addTextDocument,
  addUrlDocument,
  addWebsiteDocument,
  deleteDocument,
  getKnowledgeBase,
  listChunks,
  listDocuments,
  reingestDocument,
  uploadDocument,
} from "@/lib/api/knowledge";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

const statusMeta: Record<ApiDocStatus, { label: string; variant: "success" | "info" | "error" | "default" }> = {
  ready: { label: "Ready", variant: "success" },
  processing: { label: "Processing", variant: "info" },
  queued: { label: "Queued", variant: "info" },
  failed: { label: "Failed", variant: "error" },
};

function docType(doc: ApiDocument): DocType {
  if (doc.source_type === "url" || doc.source_type === "website") return "url";
  const name = (doc.filename ?? "").toLowerCase();
  if (name.endsWith(".pdf")) return "pdf";
  if (name.endsWith(".docx") || name.endsWith(".doc")) return "docx";
  if (name.endsWith(".csv")) return "csv";
  if (name.endsWith(".md")) return "md";
  return "txt";
}

function docName(doc: ApiDocument): string {
  return doc.filename || doc.source_url || "document";
}

/** Files uploaded at once. Enough to fill a slow uplink, few enough not to starve each other. */
const UPLOAD_CONCURRENCY = 3;
const DEFAULT_MAX_PAGES = 25;
const MAX_PAGES_LIMIT = 100;

interface UploadRow {
  key: string;
  name: string;
  loaded: number;
  total: number;
  error?: string;
}

export default function KnowledgeDetailPage() {
  const params = useParams<{ id: string }>();
  const kbId = params.id;
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const fileInput = useRef<HTMLInputElement>(null);

  const [urlOpen, setUrlOpen] = useState(false);
  const [textOpen, setTextOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [urlMode, setUrlMode] = useState<"website" | "page">("website");
  const [maxPages, setMaxPages] = useState(DEFAULT_MAX_PAGES);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [text, setText] = useState("");
  const [textName, setTextName] = useState("");
  const [chunksFor, setChunksFor] = useState<ApiDocument | null>(null);

  const { data: kb } = useQuery({
    queryKey: ["kb", kbId],
    queryFn: () => getKnowledgeBase(kbId),
    enabled: Boolean(activeOrgId),
  });

  const { data: docs, isLoading } = useQuery({
    queryKey: ["kb-docs", kbId],
    queryFn: () => listDocuments(kbId),
    enabled: Boolean(activeOrgId),
    // Poll while anything is still ingesting so status/chunk counts update live.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((d) => d.status === "queued" || d.status === "processing")
        ? 2000
        : false,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["kb-docs", kbId] });
    qc.invalidateQueries({ queryKey: ["kb", kbId] });
  };

  const patchUpload = (key: string, patch: Partial<UploadRow>) =>
    setUploads((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  // Several files at once, each with its own byte-level progress. Before, one file at a time behind
  // a bare spinner: on a slow link a 2 MB PDF looked frozen for 40 s while it was still uploading.
  const startUploads = async (files: File[]) => {
    const rows = files.map((f, i) => ({
      key: `${Date.now()}-${i}-${f.name}`,
      name: f.name,
      loaded: 0,
      total: f.size,
    }));
    setUploads((prev) => [...prev.filter((r) => !r.error), ...rows]);
    await runLimited(
      files.map((file, i) => ({ file, key: rows[i].key })),
      UPLOAD_CONCURRENCY,
      async ({ file, key }) => {
        try {
          await uploadDocument(kbId, file, (loaded, total) => patchUpload(key, { loaded, total }));
          setUploads((prev) => prev.filter((r) => r.key !== key));
          invalidate();
        } catch (err) {
          patchUpload(key, { error: (err as Error).message || "Upload failed." });
        }
      },
    );
  };
  const uploading = uploads.some((u) => !u.error);

  const addUrl = useMutation({
    mutationFn: () =>
      urlMode === "website"
        ? addWebsiteDocument(kbId, url, Math.min(Math.max(1, maxPages || 1), MAX_PAGES_LIMIT))
        : addUrlDocument(kbId, url),
    onSuccess: () => {
      setUrl("");
      setUrlOpen(false);
      invalidate();
    },
  });
  const addText = useMutation({
    mutationFn: () => addTextDocument(kbId, { text, filename: textName || undefined }),
    onSuccess: () => {
      setText("");
      setTextName("");
      setTextOpen(false);
      invalidate();
    },
  });
  const reingest = useMutation({
    mutationFn: (id: string) => reingestDocument(id),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteDocument(id),
    onSuccess: invalidate,
  });

  const rows = docs ?? [];

  return (
    <div className="mx-auto max-w-[1200px] space-y-6">
      <div className="flex items-center gap-3">
        <Link
          href="/knowledge"
          className="grid size-8 place-items-center rounded-md border border-border bg-surface text-muted transition-colors hover:text-text"
          aria-label="Back to knowledge"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-text">
            {kb?.name ?? "Knowledge base"}
          </h1>
          <p className="text-sm text-muted">
            {kb ? `${kb.document_count} documents · ${kb.embedding_model} embeddings` : " "}
          </p>
        </div>
      </div>

      {/* Add documents */}
      <div className="rounded-lg border border-border bg-surface p-5">
        {/*
          `accept` mirrors `app/rag/formats.ACCEPTED`. It is a courtesy, not the control — a
          file can still arrive by drag-and-drop or a direct API call, so the server refuses
          independently (docs/14 K3-1/K3-2). Keep the two in step: a format offered here but
          refused there reads to the client as a broken upload.
        */}
        <input
          ref={fileInput}
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.docx,.txt,.csv,.md,.markdown,.json,.html,.htm,.rst,.log,.yaml,.yml"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) void startUploads(files);
            e.target.value = "";
          }}
        />
        <div className="grid gap-4 md:grid-cols-3">
          <button
            onClick={() => fileInput.current?.click()}
            className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-strong bg-surface-2/40 p-6 text-center transition-colors hover:border-accent/40 hover:bg-accent/[0.03] disabled:opacity-60"
          >
            {uploading ? (
              <Loader2 className="size-6 animate-spin text-accent" />
            ) : (
              <FileUp className="size-6 text-accent" />
            )}
            <div className="text-sm font-medium text-text">Upload files</div>
            <div className="text-xs text-faint">PDF, DOCX, TXT, CSV, MD · several at once</div>
          </button>
          <button
            onClick={() => setUrlOpen(true)}
            className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-strong bg-surface-2/40 p-6 text-center transition-colors hover:border-accent/40 hover:bg-accent/[0.03]"
          >
            <Globe className="size-6 text-accent" />
            <div className="text-sm font-medium text-text">Add a website</div>
            <div className="text-xs text-faint">Whole site or one page</div>
          </button>
          <button
            onClick={() => setTextOpen(true)}
            className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-strong bg-surface-2/40 p-6 text-center transition-colors hover:border-accent/40 hover:bg-accent/[0.03]"
          >
            <Type className="size-6 text-accent" />
            <div className="text-sm font-medium text-text">Paste text</div>
            <div className="text-xs text-faint">Raw content</div>
          </button>
        </div>
        {uploads.length > 0 && (
          <ul className="mt-4 space-y-2" aria-label="Uploads in progress">
            {uploads.map((u) => {
              const pct = u.total > 0 ? Math.round((u.loaded / u.total) * 100) : 0;
              return (
                <li key={u.key} className="rounded-md border border-border bg-surface-2/40 px-3 py-2">
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <span className="truncate font-medium text-text">{u.name}</span>
                    {u.error ? (
                      <button
                        onClick={() => setUploads((rows) => rows.filter((r) => r.key !== u.key))}
                        className="text-faint hover:text-text"
                        aria-label={`Dismiss ${u.name}`}
                      >
                        <X className="size-3.5" />
                      </button>
                    ) : (
                      <span className="shrink-0 font-mono text-faint">
                        {pct < 100
                          ? `${formatBytes(u.loaded)} / ${formatBytes(u.total)}`
                          : "Uploaded · saving"}
                      </span>
                    )}
                  </div>
                  {u.error ? (
                    <p className="mt-1 text-xs text-error-text">{u.error}</p>
                  ) : (
                    <div
                      className="mt-1.5 h-1 overflow-hidden rounded-full bg-border"
                      role="progressbar"
                      aria-valuenow={pct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={`Uploading ${u.name}`}
                    >
                      <div className="h-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {!isLoading && rows.length > 0 && <PiiSummary documents={rows} />}

      {/* Documents table */}
      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        <div className="flex items-center justify-between border-b border-border p-5">
          <h3 className="font-display text-base font-semibold text-text">Documents</h3>
          <span className="text-sm text-muted">{rows.length} items</span>
        </div>
        <div className="overflow-x-auto scroll-thin">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Chunks</th>
                <th className="px-5 py-3 font-medium">Added</th>
                <th className="px-5 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {isLoading && (
                <tr>
                  <td colSpan={5} className="px-5 py-4">
                    <Skeleton className="h-6 w-full" />
                  </td>
                </tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-5 py-10 text-center text-sm text-muted">
                    No documents yet. Upload a file, add a URL, or paste text to get started.
                  </td>
                </tr>
              )}
              {rows.map((doc) => {
                const status = statusMeta[doc.status];
                const busy = doc.status === "processing" || doc.status === "queued";
                return (
                  <tr key={doc.id} className="transition-colors hover:bg-surface-2/40">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2.5">
                        <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-faint">
                          <DocIcon type={docType(doc)} />
                        </span>
                        <span className="flex min-w-0 flex-col gap-1">
                          <span className="max-w-[320px] truncate font-medium text-text">
                            {docName(doc)}
                          </span>
                          {doc.source_type === "website" && doc.status === "ready" && doc.crawl && (
                            <span className="text-xs text-faint">{doc.crawl.pages.length} pages read</span>
                          )}
                          <PiiBadge flags={doc.pii_flags} />
                        </span>
                      </div>
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        {busy && <Loader2 className="size-3.5 animate-spin text-info-text" />}
                        <Badge variant={status.variant}>{status.label}</Badge>
                        {doc.status === "failed" && doc.error_message && (
                          <span title={doc.error_message}>
                            <AlertCircle className="size-3.5 text-error-text" />
                          </span>
                        )}
                        {doc.status === "ready" && (doc.crawl?.warnings.length ?? 0) > 0 && (
                          <span title={doc.crawl?.warnings.join("\n")}>
                            <AlertCircle className="size-3.5 text-warn-text" />
                          </span>
                        )}
                      </div>
                      {busy && <IngestProgressLine doc={doc} />}
                      {doc.status === "failed" && doc.error_message && (
                        <p className="mt-1 max-w-[280px] text-xs text-error-text">{doc.error_message}</p>
                      )}
                    </td>
                    <td className="px-5 py-3 font-mono text-muted">{doc.chunk_count || "—"}</td>
                    <td className="px-5 py-3 text-faint">{relativeTime(doc.created_at)}</td>
                    <td className="px-5 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => setChunksFor(doc)}
                          disabled={doc.status !== "ready"}
                          title="View chunks"
                          className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text disabled:opacity-40"
                        >
                          <Eye className="size-4" />
                        </button>
                        <button
                          onClick={() => reingest.mutate(doc.id)}
                          disabled={busy}
                          title="Re-ingest"
                          className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text disabled:opacity-40"
                        >
                          <RefreshCw className="size-4" />
                        </button>
                        <button
                          onClick={() => remove.mutate(doc.id)}
                          title="Delete"
                          className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                        >
                          <Trash2 className="size-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {kb && (
        <DeleteKnowledgeBase
          kbId={kb.id}
          name={kb.name}
          documentCount={kb.document_count}
          attachedAgents={kb.attached_agents}
        />
      )}

      {/* URL dialog */}
      <Dialog open={urlOpen} onOpenChange={setUrlOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add a website</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              addUrl.mutate();
            }}
            className="space-y-4"
          >
            <Input
              autoFocus
              type="url"
              placeholder="https://yourbusiness.com"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              aria-label="Website address"
            />
            <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="What to read">
              {(
                [
                  ["website", Globe, "Whole website", "Follows the sitemap and links"],
                  ["page", Link2, "This page only", "Just the address above"],
                ] as const
              ).map(([mode, Icon, title, hint]) => (
                <button
                  key={mode}
                  type="button"
                  role="radio"
                  aria-checked={urlMode === mode}
                  onClick={() => setUrlMode(mode)}
                  className={`rounded-md border p-3 text-left transition-colors ${
                    urlMode === mode
                      ? "border-accent bg-accent/[0.06]"
                      : "border-border hover:border-border-strong"
                  }`}
                >
                  <div className="flex items-center gap-2 text-sm font-medium text-text">
                    <Icon className="size-4 text-accent" /> {title}
                  </div>
                  <div className="mt-0.5 text-xs text-faint">{hint}</div>
                </button>
              ))}
            </div>
            {urlMode === "website" && (
              <label className="flex items-center justify-between gap-3 text-sm text-muted">
                <span>Maximum pages</span>
                <Input
                  type="number"
                  min={1}
                  max={MAX_PAGES_LIMIT}
                  value={maxPages}
                  onChange={(e) => setMaxPages(Number(e.target.value))}
                  className="w-24"
                  aria-label="Maximum pages"
                />
              </label>
            )}
            <p className="text-xs text-faint">
              Pages built entirely with JavaScript (some 3D or app-style sites) only expose their title and
              description; paste their text instead for complete answers.
            </p>
            {addUrl.isError && <p className="text-sm text-error-text">{(addUrl.error as Error).message}</p>}
            <Button type="submit" variant="primary" className="w-full" disabled={addUrl.isPending || !url.trim()}>
              {addUrl.isPending && <Loader2 className="size-4 animate-spin" />}{" "}
              {urlMode === "website" ? "Crawl & ingest" : "Fetch & ingest"}
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      {/* Text dialog */}
      <Dialog open={textOpen} onOpenChange={setTextOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Paste text</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              addText.mutate();
            }}
            className="space-y-4"
          >
            <Input
              placeholder="Title (optional)"
              value={textName}
              onChange={(e) => setTextName(e.target.value)}
            />
            <Textarea
              autoFocus
              placeholder="Paste raw content to index…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
            />
            {addText.isError && <p className="text-sm text-error-text">{(addText.error as Error).message}</p>}
            <Button type="submit" variant="primary" className="w-full" disabled={addText.isPending || !text.trim()}>
              {addText.isPending && <Loader2 className="size-4 animate-spin" />} Add & ingest
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      <ChunkViewer doc={chunksFor} onClose={() => setChunksFor(null)} />
    </div>
  );
}

function IngestProgressLine({ doc }: { doc: ApiDocument }) {
  const { label, percent } = describeProgress(doc.progress);
  return (
    <div className="mt-1.5 w-48">
      <div className="text-xs text-faint">{label}</div>
      {percent !== null && (
        <div
          className="mt-1 h-1 overflow-hidden rounded-full bg-border"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label}
        >
          <div className="h-full bg-info-text transition-[width]" style={{ width: `${percent}%` }} />
        </div>
      )}
    </div>
  );
}

function ChunkViewer({ doc, onClose }: { doc: ApiDocument | null; onClose: () => void }) {
  const { data: chunks, isLoading } = useQuery({
    queryKey: ["chunks", doc?.id],
    queryFn: () => listChunks(doc!.id),
    enabled: Boolean(doc),
  });
  const title = useMemo(() => (doc ? docName(doc) : ""), [doc]);

  return (
    <Dialog open={Boolean(doc)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="truncate">{title}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-2 overflow-y-auto scroll-thin pr-1">
          {isLoading && <Skeleton className="h-24 w-full" />}
          {(chunks ?? []).map((c) => (
            <div key={c.id} className="rounded-md border border-border bg-surface-2/40 p-3">
              <div className="mb-1.5 flex items-center gap-2 text-xs text-faint">
                <span className="font-mono">#{c.ordinal}</span>
                <span>· {c.token_count} tokens</span>
                {typeof c.metadata?.source_url === "string" && doc?.source_type === "website" && (
                  <span className="truncate">· {c.metadata.source_url}</span>
                )}
              </div>
              <p className="whitespace-pre-wrap text-xs leading-relaxed text-muted">{c.content}</p>
            </div>
          ))}
          {!isLoading && (chunks ?? []).length === 0 && (
            <p className="py-6 text-center text-sm text-muted">No chunks.</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
