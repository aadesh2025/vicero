"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  createCannedResponse,
  deleteCannedResponse,
  listCannedResponses,
  updateCannedResponse,
  type ApiCannedResponse,
} from "@/lib/api/canned-responses";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";

export default function CannedResponsesPage() {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canManage = useCan("inbox:handle");
  const [shortcut, setShortcut] = useState("");
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState<ApiCannedResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: items, isLoading } = useQuery({
    queryKey: ["canned-responses", activeOrgId],
    queryFn: () => listCannedResponses(),
    enabled: Boolean(activeOrgId),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["canned-responses", activeOrgId] });
  const reset = () => {
    setShortcut("");
    setContent("");
    setEditing(null);
    setError(null);
  };

  const save = useMutation({
    mutationFn: () =>
      editing
        ? updateCannedResponse(editing.id, { shortcut, content })
        : createCannedResponse(shortcut, content),
    onSuccess: () => {
      reset();
      invalidate();
    },
    // Duplicate shortcuts and malformed ones both come back typed — show the reason.
    onError: (e) => setError((e as Error).message),
  });
  const remove = useMutation({ mutationFn: (id: string) => deleteCannedResponse(id), onSuccess: invalidate });

  const startEdit = (item: ApiCannedResponse) => {
    setEditing(item);
    setShortcut(item.shortcut);
    setContent(item.content);
    setError(null);
  };

  return (
    <div className="space-y-6">
      <Section
        title="Canned responses"
        description="Reusable replies. Type / followed by a shortcut in the inbox to insert one."
      >
        {canManage && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (shortcut.trim() && content.trim()) save.mutate();
            }}
            className="space-y-3"
          >
            <div className="flex items-center gap-2">
              <span className="text-sm text-faint">/</span>
              <Input
                aria-label="Shortcut"
                placeholder="refund"
                value={shortcut}
                onChange={(e) => setShortcut(e.target.value)}
                className="max-w-[220px] font-mono"
              />
              {editing && (
                <Button type="button" variant="outline" size="sm" onClick={reset}>
                  <X className="size-4" /> Cancel
                </Button>
              )}
            </div>
            <textarea
              aria-label="Response text"
              placeholder="The reply to insert…"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-border bg-surface-2 p-3 text-sm text-text placeholder:text-faint focus-visible:border-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
            />
            <div className="flex items-center gap-3">
              <Button
                type="submit"
                variant="primary"
                disabled={save.isPending || !shortcut.trim() || !content.trim()}
              >
                {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
                {editing ? "Save changes" : "Add response"}
              </Button>
              <p className="text-xs text-faint">
                Lowercase letters, digits, hyphens and underscores — no spaces.
              </p>
            </div>
            {error && <p className="text-sm text-error-text">{error}</p>}
          </form>
        )}

        <div className="mt-4 divide-y divide-border overflow-hidden rounded-lg border border-border">
          {isLoading && <p className="p-4 text-sm text-muted">Loading…</p>}
          {!isLoading && (items ?? []).length === 0 && (
            <p className="p-4 text-sm text-muted">No canned responses yet.</p>
          )}
          {(items ?? []).map((item) => (
            <div key={item.id} className="flex items-start gap-3 p-3">
              <code className="mt-0.5 shrink-0 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-accent">
                /{item.shortcut}
              </code>
              <p className="min-w-0 flex-1 whitespace-pre-wrap text-sm text-text">{item.content}</p>
              {canManage && (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => startEdit(item)}
                    aria-label={`Edit /${item.shortcut}`}
                    className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text"
                  >
                    <Pencil className="size-4" />
                  </button>
                  <button
                    onClick={() => remove.mutate(item.id)}
                    aria-label={`Delete /${item.shortcut}`}
                    className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
