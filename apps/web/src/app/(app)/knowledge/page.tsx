"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Database, Loader2, Plus } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { createKnowledgeBase, listKnowledgeBases } from "@/lib/api/knowledge";
import { useSession } from "@/lib/store/session";
import { relativeTime } from "@/lib/utils";

export default function KnowledgePage() {
  const router = useRouter();
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const { data: kbs, isLoading } = useQuery({
    queryKey: ["knowledge-bases", activeOrgId],
    queryFn: listKnowledgeBases,
    enabled: Boolean(activeOrgId),
  });

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () => createKnowledgeBase({ name, description: description || undefined }),
    onSuccess: (kb) => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases", activeOrgId] });
      router.push(`/knowledge/${kb.id}`);
    },
  });

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader title="Knowledge" description="Documents your agents retrieve answers from.">
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus /> New knowledge base
        </Button>
      </PageHeader>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[164px] rounded-card" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {(kbs ?? []).map((kb) => (
            <Link
              key={kb.id}
              href={`/knowledge/${kb.id}`}
              className="group relative overflow-hidden rounded-card border border-border bg-surface p-5 shadow-card transition-colors hover:border-border-strong"
            >
              <div className="flex items-start justify-between">
                <span className="grid size-11 place-items-center rounded-[10px] bg-info-soft text-info">
                  <BookOpen className="size-5" />
                </span>
                <span className="text-xs font-semibold text-faint">{relativeTime(kb.updated_at)}</span>
              </div>
              <h3 className="mt-4 font-display text-lg font-extrabold text-text">{kb.name}</h3>
              {kb.description && (
                <p className="mt-1 line-clamp-2 text-sm font-medium text-muted">{kb.description}</p>
              )}
              <div className="mt-4 flex items-center gap-5 border-t border-border pt-4 text-sm">
                <div>
                  <div className="font-extrabold tabular-nums text-text">{kb.document_count}</div>
                  <div className="text-[11px] font-semibold text-faint">documents</div>
                </div>
                <div>
                  <div className="font-mono text-xs font-semibold text-muted">{kb.embedding_model}</div>
                  <div className="text-[11px] font-semibold text-faint">embeddings</div>
                </div>
              </div>
            </Link>
          ))}

          <button
            onClick={() => setCreating(true)}
            className="flex min-h-[164px] flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong text-muted transition-colors hover:border-info/40 hover:bg-info-soft hover:text-info"
          >
            <span className="grid size-11 place-items-center rounded-[10px] bg-info-soft text-info">
              <Database className="size-5" />
            </span>
            <span className="text-sm font-bold">Create a knowledge base</span>
          </button>
        </div>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New knowledge base</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
            className="space-y-4"
          >
            <Input
              autoFocus
              placeholder="Knowledge base name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Textarea
              placeholder="Description (optional)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
            {create.isError && (
              <p className="text-sm text-error-text">{(create.error as Error).message}</p>
            )}
            <Button
              type="submit"
              variant="primary"
              className="w-full"
              disabled={create.isPending || !name.trim()}
            >
              {create.isPending && <Loader2 className="size-4 animate-spin" />} Create
            </Button>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
