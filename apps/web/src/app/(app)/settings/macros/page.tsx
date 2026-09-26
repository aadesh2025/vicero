"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  createMacro,
  deleteMacro,
  listMacros,
  updateMacro,
  type ApiMacro,
  type MacroAction,
  type MacroActionType,
} from "@/lib/api/macros";
import { listCannedResponses } from "@/lib/api/canned-responses";
import { listMembers } from "@/lib/api/orgs";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";

const ACTION_LABELS: Record<MacroActionType, string> = {
  reply: "Send a reply",
  add_tag: "Add a tag",
  assign: "Assign to",
  resolve: "Resolve & close",
};

function blankAction(type: MacroActionType): MacroAction {
  return { type, params: {} };
}

/** Human summary of one step, for the saved-macro list. */
function describe(action: MacroAction): string {
  if (action.type === "reply") {
    return action.params.canned_response_id
      ? "Reply with a canned response"
      : `Reply: “${(action.params.text ?? "").slice(0, 40)}”`;
  }
  if (action.type === "add_tag") return `Tag: ${action.params.tag}`;
  if (action.type === "assign") return "Assign";
  return "Resolve & close";
}

export default function MacrosPage() {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canManage = useCan("inbox:handle");

  const [name, setName] = useState("");
  const [actions, setActions] = useState<MacroAction[]>([]);
  const [editing, setEditing] = useState<ApiMacro | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: macros, isLoading } = useQuery({
    queryKey: ["macros", activeOrgId],
    queryFn: listMacros,
    enabled: Boolean(activeOrgId),
  });
  const { data: canned } = useQuery({
    queryKey: ["canned-responses", activeOrgId],
    queryFn: () => listCannedResponses(),
    enabled: Boolean(activeOrgId),
  });
  const { data: members } = useQuery({
    queryKey: ["members", activeOrgId],
    queryFn: () => listMembers(activeOrgId!),
    enabled: Boolean(activeOrgId),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["macros", activeOrgId] });
  const reset = () => {
    setName("");
    setActions([]);
    setEditing(null);
    setError(null);
  };

  const save = useMutation({
    mutationFn: () => (editing ? updateMacro(editing.id, { name, actions }) : createMacro(name, actions)),
    onSuccess: () => {
      reset();
      invalidate();
    },
    onError: (e) => setError((e as Error).message),
  });
  const remove = useMutation({ mutationFn: (id: string) => deleteMacro(id), onSuccess: invalidate });

  const setParam = (i: number, key: string, value: string) =>
    setActions((list) =>
      list.map((a, idx) => (idx === i ? { ...a, params: { ...a.params, [key]: value } } : a)),
    );
  const move = (i: number, delta: number) =>
    setActions((list) => {
      const next = [...list];
      const target = i + delta;
      if (target < 0 || target >= next.length) return list;
      [next[i], next[target]] = [next[target], next[i]];
      return next;
    });

  const startEdit = (m: ApiMacro) => {
    setEditing(m);
    setName(m.name);
    setActions(m.actions);
    setError(null);
  };

  // Mirrors the server's per-type rules, so an incomplete step is caught before the round trip.
  const complete = actions.every((a) =>
    a.type === "reply"
      ? Boolean(a.params.text?.trim() || a.params.canned_response_id)
      : a.type === "add_tag"
        ? Boolean(a.params.tag?.trim())
        : a.type === "assign"
          ? Boolean(a.params.user_id)
          : true,
  );

  return (
    <div className="space-y-6">
      <Section
        title="Macros"
        description="One click to run a sequence of inbox actions — reply, tag, assign, resolve."
      >
        {canManage && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim() && actions.length && complete) save.mutate();
            }}
            className="space-y-3"
          >
            <div className="flex items-center gap-2">
              <Input
                aria-label="Macro name"
                placeholder="Macro name (e.g. Refund and close)"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              {editing && (
                <Button type="button" variant="outline" size="sm" onClick={reset}>
                  <X className="size-4" /> Cancel
                </Button>
              )}
            </div>

            <ol className="space-y-2">
              {actions.map((action, i) => (
                <li
                  key={i}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface-2/50 p-2.5"
                >
                  <span className="font-mono text-xs text-faint">{i + 1}</span>
                  <span className="text-sm text-text">{ACTION_LABELS[action.type]}</span>

                  {action.type === "reply" && (
                    <>
                      <select
                        aria-label={`Step ${i + 1} canned response`}
                        value={action.params.canned_response_id ?? ""}
                        onChange={(e) => setParam(i, "canned_response_id", e.target.value)}
                        className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-text"
                      >
                        <option value="">Free text…</option>
                        {(canned ?? []).map((c) => (
                          <option key={c.id} value={c.id}>
                            /{c.shortcut}
                          </option>
                        ))}
                      </select>
                      {!action.params.canned_response_id && (
                        <Input
                          aria-label={`Step ${i + 1} reply text`}
                          placeholder="Reply text"
                          value={action.params.text ?? ""}
                          onChange={(e) => setParam(i, "text", e.target.value)}
                          className="h-8 min-w-[200px] flex-1 text-xs"
                        />
                      )}
                    </>
                  )}

                  {action.type === "add_tag" && (
                    <Input
                      aria-label={`Step ${i + 1} tag`}
                      placeholder="Tag"
                      value={action.params.tag ?? ""}
                      onChange={(e) => setParam(i, "tag", e.target.value)}
                      className="h-8 max-w-[200px] text-xs"
                    />
                  )}

                  {action.type === "assign" && (
                    <select
                      aria-label={`Step ${i + 1} assignee`}
                      value={action.params.user_id ?? ""}
                      onChange={(e) => setParam(i, "user_id", e.target.value)}
                      className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-text"
                    >
                      <option value="">Pick a teammate…</option>
                      {(members ?? []).map((m) => (
                        <option key={m.user_id} value={m.user_id}>
                          {m.full_name || m.email}
                        </option>
                      ))}
                    </select>
                  )}

                  <div className="ml-auto flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => move(i, -1)}
                      disabled={i === 0}
                      aria-label={`Move step ${i + 1} up`}
                      className="rounded p-1 text-faint hover:text-text disabled:opacity-30"
                    >
                      <ArrowUp className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => move(i, 1)}
                      disabled={i === actions.length - 1}
                      aria-label={`Move step ${i + 1} down`}
                      className="rounded p-1 text-faint hover:text-text disabled:opacity-30"
                    >
                      <ArrowDown className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setActions((l) => l.filter((_, idx) => idx !== i))}
                      aria-label={`Remove step ${i + 1}`}
                      className="rounded p-1 text-faint hover:text-error-text"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ol>

            <div className="flex flex-wrap items-center gap-2">
              {(Object.keys(ACTION_LABELS) as MacroActionType[]).map((type) => (
                <Button
                  key={type}
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setActions((l) => [...l, blankAction(type)])}
                >
                  <Plus className="size-3.5" /> {ACTION_LABELS[type]}
                </Button>
              ))}
            </div>

            <Button
              type="submit"
              variant="primary"
              disabled={save.isPending || !name.trim() || actions.length === 0 || !complete}
            >
              {save.isPending && <Loader2 className="size-4 animate-spin" />}
              {editing ? "Save changes" : "Create macro"}
            </Button>
            {!complete && actions.length > 0 && (
              <p className="text-xs text-warn-text">Every step needs its details filled in.</p>
            )}
            {error && <p className="text-sm text-error-text">{error}</p>}
          </form>
        )}

        <div className="mt-4 divide-y divide-border overflow-hidden rounded-lg border border-border">
          {isLoading && <p className="p-4 text-sm text-muted">Loading…</p>}
          {!isLoading && (macros ?? []).length === 0 && (
            <p className="p-4 text-sm text-muted">No macros yet.</p>
          )}
          {(macros ?? []).map((m) => (
            <div key={m.id} className="flex items-start gap-3 p-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-text">{m.name}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {m.actions.map((a, i) => (
                    <Badge key={i} variant="default">
                      {describe(a)}
                    </Badge>
                  ))}
                </div>
              </div>
              {canManage && (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => startEdit(m)}
                    aria-label={`Edit ${m.name}`}
                    className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text"
                  >
                    <Pencil className="size-4" />
                  </button>
                  <button
                    onClick={() => remove.mutate(m.id)}
                    aria-label={`Delete ${m.name}`}
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
