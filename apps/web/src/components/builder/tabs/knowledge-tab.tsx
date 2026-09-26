"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { BookOpen, FileText, Loader2, Search } from "lucide-react";
import Link from "next/link";
import { Field, SectionCard, SliderField } from "@/components/builder/field";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useBuilder } from "@/lib/store/builder";
import { listKnowledgeBases, searchKnowledgeBase } from "@/lib/api/knowledge";
import type { ApiCitation } from "@/lib/api/types";
import { useSession } from "@/lib/store/session";

export function KnowledgeTab() {
  const draft = useBuilder((s) => s.draft);
  const update = useBuilder((s) => s.update);
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ApiCitation[] | null>(null);

  const { data: kbs, isLoading } = useQuery({
    queryKey: ["knowledge-bases", activeOrgId],
    queryFn: listKnowledgeBases,
    enabled: Boolean(activeOrgId),
  });

  const search = useMutation({
    mutationFn: (kbId: string) =>
      searchKnowledgeBase(kbId, {
        query,
        top_k: draft?.knowledge.topK ?? 5,
        score_threshold: draft?.knowledge.scoreThreshold ?? 0,
        hybrid: draft?.knowledge.hybrid ?? true,
      }),
    onSuccess: (res) => setResults(res.citations),
  });

  if (!draft) return null;
  const k = draft.knowledge;
  const ragEnabled = draft.features.rag;

  const toggleKb = (id: string) =>
    update((d) => {
      const set = new Set(d.knowledge.attachedKbIds);
      if (set.has(id)) set.delete(id);
      else set.add(id);
      d.knowledge.attachedKbIds = Array.from(set);
      // Enable retrieval automatically once at least one KB is attached.
      if (d.knowledge.attachedKbIds.length > 0) d.features.rag = true;
    });

  const firstAttached = k.attachedKbIds[0];

  return (
    <div className="space-y-6">
      <SectionCard title="Retrieval (RAG)" description="Let the agent ground answers in your documents.">
        <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
          <div className="flex-1">
            <div className="text-sm font-medium text-text">Enable retrieval</div>
            <div className="text-xs text-muted">
              Inject relevant chunks from attached knowledge bases into each prompt.
            </div>
          </div>
          <Switch checked={ragEnabled} onCheckedChange={(v) => update((d) => void (d.features.rag = v))} />
        </div>
      </SectionCard>

      <SectionCard title="Attached knowledge bases" description="The agent retrieves context from these.">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-14 w-full rounded-md" />
            <Skeleton className="h-14 w-full rounded-md" />
          </div>
        ) : (kbs ?? []).length === 0 ? (
          <div className="rounded-md border border-dashed border-border-strong bg-surface-2/40 p-6 text-center text-sm text-muted">
            No knowledge bases yet.{" "}
            <Link href="/knowledge" className="text-accent hover:underline">
              Create one
            </Link>{" "}
            to attach it here.
          </div>
        ) : (
          <ul className="space-y-2">
            {(kbs ?? []).map((kb) => {
              const attached = k.attachedKbIds.includes(kb.id);
              return (
                <li
                  key={kb.id}
                  className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3"
                >
                  <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                    <BookOpen className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-text">{kb.name}</div>
                    <div className="text-xs text-faint">
                      {kb.document_count} docs · {kb.embedding_model}
                    </div>
                  </div>
                  <Switch checked={attached} onCheckedChange={() => toggleKb(kb.id)} />
                </li>
              );
            })}
          </ul>
        )}
      </SectionCard>

      <SectionCard title="Retrieval settings" description="Tune how context is fetched per query.">
        <div className="grid gap-6 sm:grid-cols-2">
          <SliderField label="Top K" value={k.topK} display={String(k.topK)}>
            <Slider
              value={[k.topK]}
              min={1}
              max={12}
              step={1}
              onValueChange={([v]) => update((d) => void (d.knowledge.topK = v))}
            />
          </SliderField>
          <SliderField label="Score threshold" value={k.scoreThreshold} display={k.scoreThreshold.toFixed(2)}>
            <Slider
              value={[k.scoreThreshold]}
              min={0}
              max={1}
              step={0.01}
              onValueChange={([v]) => update((d) => void (d.knowledge.scoreThreshold = v))}
            />
          </SliderField>
        </div>
        <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
          <div className="flex-1">
            <div className="text-sm font-medium text-text">Hybrid search</div>
            <div className="text-xs text-muted">Combine keyword + vector ranking (RRF).</div>
          </div>
          <Switch checked={k.hybrid} onCheckedChange={(v) => update((d) => void (d.knowledge.hybrid = v))} />
        </div>
      </SectionCard>

      <SectionCard title="Test retrieval" description="See which chunks a query would pull in.">
        <Field label="Query">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. What is the return window?"
                className="h-9 w-full rounded-md border border-border bg-surface-2 pl-9 pr-3 text-sm text-text placeholder:text-faint focus-visible:border-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
              />
            </div>
            <Button
              variant="primary"
              size="default"
              onClick={() => firstAttached && search.mutate(firstAttached)}
              disabled={!query.trim() || !firstAttached || search.isPending}
            >
              {search.isPending && <Loader2 className="size-4 animate-spin" />} Test
            </Button>
          </div>
        </Field>
        {!firstAttached && (
          <p className="text-xs text-muted">Attach a knowledge base above to test retrieval.</p>
        )}
        {search.isError && <p className="text-xs text-error-text">{(search.error as Error).message}</p>}
        {results && results.length === 0 && (
          <p className="text-xs text-muted">No chunks passed the threshold for that query.</p>
        )}
        {results && results.length > 0 && (
          <div className="space-y-2">
            {results.map((c) => {
              const source =
                (c.metadata.filename as string) || (c.metadata.source_url as string) || "document";
              return (
                <div key={c.chunk_id} className="rounded-md border border-border bg-surface-2/40 p-3">
                  <div className="mb-1.5 flex items-center gap-2">
                    <FileText className="size-3.5 text-faint" />
                    <span className="max-w-[220px] truncate font-mono text-xs text-muted">
                      {source} · #{c.ordinal}
                    </span>
                    <Badge variant="accent" className="ml-auto">
                      {c.score.toFixed(2)}
                    </Badge>
                  </div>
                  <p className="line-clamp-3 text-xs leading-relaxed text-muted">{c.content}</p>
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
