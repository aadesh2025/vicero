"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { createApiKey, deleteApiKey, listApiKeys, revokeApiKey, type ApiKeyCreated } from "@/lib/api/settings";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";
import { relativeTime } from "@/lib/utils";

export default function ApiKeysPage() {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canManage = useCan("tools:manage");
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const { data: keys } = useQuery({
    queryKey: ["apikeys", activeOrgId],
    queryFn: listApiKeys,
    enabled: Boolean(activeOrgId),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["apikeys", activeOrgId] });
  const create = useMutation({
    mutationFn: () => createApiKey(name),
    onSuccess: (k) => {
      setCreated(k);
      setName("");
      invalidate();
    },
  });
  const revoke = useMutation({ mutationFn: (id: string) => revokeApiKey(id), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id: string) => deleteApiKey(id), onSuccess: invalidate });

  return (
    <div className="space-y-6">
      <Section title="API keys" description="Programmatic access to the BotForge API. Send as X-API-Key or Bearer.">
        {canManage && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
            className="flex gap-2"
          >
            <Input placeholder="Key name (e.g. production server)" value={name} onChange={(e) => setName(e.target.value)} />
            <Button type="submit" variant="primary" disabled={create.isPending || !name.trim()}>
              {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />} Create
            </Button>
          </form>
        )}

        <div className="mt-4 divide-y divide-border overflow-hidden rounded-lg border border-border">
          {(keys ?? []).length === 0 && <p className="p-4 text-sm text-muted">No API keys yet.</p>}
          {(keys ?? []).map((k) => (
            <div key={k.id} className="flex items-center gap-3 p-4">
              <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                <KeyRound className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-text">{k.name}</span>
                  {k.revoked_at ? <Badge variant="error">revoked</Badge> : <Badge variant="success">active</Badge>}
                </div>
                <div className="font-mono text-xs text-faint">
                  {k.key_prefix}••• · {k.last_used_at ? `used ${relativeTime(k.last_used_at)}` : "never used"}
                </div>
              </div>
              {canManage && !k.revoked_at && (
                <Button variant="outline" size="sm" onClick={() => revoke.mutate(k.id)}>
                  Revoke
                </Button>
              )}
              {canManage && (
                <button
                  onClick={() => remove.mutate(k.id)}
                  className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                  title="Delete"
                >
                  <Trash2 className="size-4" />
                </button>
              )}
            </div>
          ))}
        </div>
        {!canManage && <p className="text-xs text-faint">You need the admin or editor role to manage API keys.</p>}
      </Section>

      <Dialog open={Boolean(created)} onOpenChange={(o) => !o && setCreated(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Copy your API key</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted">This is the only time the full key is shown. Store it securely.</p>
          <div className="flex items-center gap-2 rounded-md border border-border bg-surface-2 p-2">
            <code className="flex-1 truncate font-mono text-xs text-text">{created?.key}</code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(created?.key ?? "");
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className="rounded-md p-1.5 text-faint hover:text-text"
            >
              {copied ? <Check className="size-4 text-success-text" /> : <Copy className="size-4" />}
            </button>
          </div>
          <Button variant="primary" className="w-full" onClick={() => setCreated(null)}>
            Done
          </Button>
        </DialogContent>
      </Dialog>
    </div>
  );
}
