"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { listAgents } from "@/lib/api/agents";
import {
  createHelpArticle,
  deleteHelpArticle,
  listHelpArticles,
  slugify,
  updateHelpArticle,
  type ApiHelpArticle,
} from "@/lib/api/help-articles";
import { useSession } from "@/lib/store/session";

export default function HelpCenterPage() {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);

  const [agentId, setAgentId] = useState("");
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [category, setCategory] = useState("");
  const [body, setBody] = useState("");
  const [published, setPublished] = useState(false);
  const [syncToKb, setSyncToKb] = useState(false);
  const [editing, setEditing] = useState<ApiHelpArticle | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: agents } = useQuery({
    queryKey: ["agents", activeOrgId],
    queryFn: listAgents,
    enabled: Boolean(activeOrgId),
  });
  const { data: articles, isLoading } = useQuery({
    queryKey: ["help-articles", activeOrgId],
    queryFn: () => listHelpArticles(),
    enabled: Boolean(activeOrgId),
  });

  const selectedAgent = (agents ?? []).find((a) => a.id === (agentId || (agents ?? [])[0]?.id));
  const effectiveAgentId = agentId || selectedAgent?.id || "";

  const invalidate = () => qc.invalidateQueries({ queryKey: ["help-articles", activeOrgId] });
  const reset = () => {
    setTitle("");
    setSlug("");
    setCategory("");
    setBody("");
    setPublished(false);
    setSyncToKb(false);
    setEditing(null);
    setError(null);
  };

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        title,
        slug: slug || slugify(title),
        body_markdown: body,
        category: category || null,
        published,
        sync_to_kb: syncToKb,
      };
      return editing
        ? updateHelpArticle(editing.id, payload)
        : createHelpArticle({ agent_id: effectiveAgentId, ...payload });
    },
    onSuccess: () => {
      reset();
      invalidate();
    },
    // "No knowledge base to sync into" and duplicate slugs both arrive typed.
    onError: (e) => setError((e as Error).message),
  });
  const remove = useMutation({ mutationFn: (id: string) => deleteHelpArticle(id), onSuccess: invalidate });

  const startEdit = (a: ApiHelpArticle) => {
    setEditing(a);
    setAgentId(a.agent_id ?? "");
    setTitle(a.title);
    setSlug(a.slug);
    setCategory(a.category ?? "");
    setBody(a.body_markdown);
    setPublished(a.published);
    setSyncToKb(a.sync_to_kb);
    setError(null);
  };

  const publicKey = selectedAgent?.public_key;

  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <PageHeader
        title="Help Center"
        description="Public articles your visitors can browse — separate from the AI's internal knowledge."
      >
        {publicKey && (
          <a
            href={`/help/${publicKey}`}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:text-text"
          >
            <ExternalLink className="size-4" /> View public page
          </a>
        )}
      </PageHeader>

      <section className="rounded-lg border border-border bg-surface p-5">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (effectiveAgentId && title.trim() && body.trim()) save.mutate();
          }}
          className="space-y-3"
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="article-agent" className="mb-1 block text-xs text-muted">
                Agent
              </label>
              <select
                id="article-agent"
                value={effectiveAgentId}
                onChange={(e) => setAgentId(e.target.value)}
                disabled={Boolean(editing)}
                className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 text-sm text-text disabled:opacity-60"
              >
                {(agents ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="article-category" className="mb-1 block text-xs text-muted">
                Category
              </label>
              <Input
                id="article-category"
                placeholder="Billing"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="article-title" className="mb-1 block text-xs text-muted">
                Title
              </label>
              <Input
                id="article-title"
                placeholder="How do refunds work?"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="article-slug" className="mb-1 block text-xs text-muted">
                Slug
              </label>
              <Input
                id="article-slug"
                placeholder={title ? slugify(title) : "auto-generated-from-title"}
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                className="font-mono"
              />
            </div>
          </div>

          <div>
            <label htmlFor="article-body" className="mb-1 block text-xs text-muted">
              Body (Markdown)
            </label>
            <textarea
              id="article-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={10}
              placeholder="## Refunds&#10;&#10;We refund within 30 days…"
              className="w-full rounded-md border border-border bg-surface-2 p-3 font-mono text-sm text-text placeholder:text-faint focus-visible:border-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
            />
          </div>

          <div className="flex flex-wrap items-center gap-6">
            <label className="flex items-center gap-2 text-sm text-text">
              <Switch checked={published} onCheckedChange={setPublished} aria-label="Published" />
              Published
            </label>
            <label className="flex items-center gap-2 text-sm text-text">
              <Switch checked={syncToKb} onCheckedChange={setSyncToKb} aria-label="Also teach the AI" />
              Also teach the AI
              <span className="text-xs text-faint">(adds it to this agent&rsquo;s knowledge base)</span>
            </label>
          </div>

          <div className="flex items-center gap-2">
            <Button
              type="submit"
              variant="primary"
              disabled={save.isPending || !effectiveAgentId || !title.trim() || !body.trim()}
            >
              {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              {editing ? "Save article" : "Create article"}
            </Button>
            {editing && (
              <Button type="button" variant="outline" onClick={reset}>
                <X className="size-4" /> Cancel
              </Button>
            )}
          </div>
          {error && <p className="text-sm text-error-text">{error}</p>}
        </form>
      </section>

      <ul aria-label="Articles" className="divide-y divide-border overflow-hidden rounded-lg border border-border">
        {isLoading && <li className="p-4 text-sm text-muted">Loading…</li>}
        {!isLoading && (articles ?? []).length === 0 && (
          <li className="p-4 text-sm text-muted">No articles yet.</li>
        )}
        {(articles ?? []).map((a) => (
          <li key={a.id} className="flex items-start gap-3 p-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-text">{a.title}</span>
                <Badge variant={a.published ? "success" : "default"}>
                  {a.published ? "Published" : "Draft"}
                </Badge>
                {a.category && <Badge variant="default">{a.category}</Badge>}
                {a.kb_document_id && <Badge variant="accent">In knowledge base</Badge>}
              </div>
              <code className="text-xs text-faint">/{a.slug}</code>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                onClick={() => startEdit(a)}
                aria-label={`Edit ${a.title}`}
                className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text"
              >
                <Pencil className="size-4" />
              </button>
              <button
                onClick={() => remove.mutate(a.id)}
                aria-label={`Delete ${a.title}`}
                className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
