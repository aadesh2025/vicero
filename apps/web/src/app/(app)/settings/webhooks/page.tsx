"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Send, Trash2, Webhook as WebhookIcon } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  createWebhook,
  deleteWebhook,
  listWebhookDeliveries,
  listWebhooks,
  testWebhook,
  updateWebhook,
  webhookEventCatalog,
  type Webhook,
} from "@/lib/api/settings";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";
import { relativeTime } from "@/lib/utils";

export default function WebhooksPage() {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canManage = useCan("tools:manage");
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<Webhook | null>(null);

  const { data: hooks } = useQuery({
    queryKey: ["webhooks", activeOrgId],
    queryFn: listWebhooks,
    enabled: Boolean(activeOrgId),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["webhooks", activeOrgId] });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateWebhook(id, { enabled }),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: (id: string) => deleteWebhook(id), onSuccess: invalidate });
  const test = useMutation({ mutationFn: (id: string) => testWebhook(id) });

  return (
    <div className="space-y-6">
      <Section title="Outbound webhooks" description="POST signed events to your endpoints as things happen.">
        {canManage && (
          <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
            <Plus /> New webhook
          </Button>
        )}
        <div className="mt-4 divide-y divide-border overflow-hidden rounded-lg border border-border">
          {(hooks ?? []).length === 0 && <p className="p-4 text-sm text-muted">No webhooks yet.</p>}
          {(hooks ?? []).map((h) => (
            <div key={h.id} className="flex items-center gap-3 p-4">
              <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                <WebhookIcon className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-sm text-text">{h.url}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {h.events.map((e) => (
                    <Badge key={e} variant="default">
                      {e}
                    </Badge>
                  ))}
                </div>
              </div>
              <Button variant="outline" size="sm" onClick={() => setViewing(h)}>
                Deliveries
              </Button>
              {canManage && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => test.mutate(h.id)}
                    disabled={test.isPending}
                    title="Send a test event"
                  >
                    <Send className="size-4" />
                  </Button>
                  <Switch checked={h.enabled} onCheckedChange={(enabled) => toggle.mutate({ id: h.id, enabled })} />
                  <button
                    onClick={() => remove.mutate(h.id)}
                    className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                    title="Delete"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      </Section>

      <CreateWebhookDialog open={creating} onOpenChange={setCreating} onCreated={invalidate} />
      <DeliveriesDialog hook={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

function CreateWebhookDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: () => void;
}) {
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["*"]);
  const { data: catalog } = useQuery({ queryKey: ["webhook-events"], queryFn: webhookEventCatalog, enabled: open });

  const create = useMutation({
    mutationFn: () => createWebhook(url, events),
    onSuccess: () => {
      setUrl("");
      setEvents(["*"]);
      onOpenChange(false);
      onCreated();
    },
  });

  const toggleEvent = (e: string) =>
    setEvents((prev) => (prev.includes(e) ? prev.filter((x) => x !== e) : [...prev.filter((x) => x !== "*"), e]));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New webhook</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
          className="space-y-3"
        >
          <Input placeholder="https://your-server.com/webhooks/botforge" value={url} onChange={(e) => setUrl(e.target.value)} />
          <div>
            <label className="mb-1 flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={events.includes("*")}
                onChange={() => setEvents(events.includes("*") ? [] : ["*"])}
              />
              All events (*)
            </label>
            <div className="flex flex-wrap gap-1.5">
              {(catalog ?? []).map((e) => (
                <button
                  key={e}
                  type="button"
                  onClick={() => toggleEvent(e)}
                  className={`rounded-full border px-2 py-1 text-[11px] ${
                    events.includes(e)
                      ? "border-accent/40 bg-accent/[0.08] text-accent"
                      : "border-border bg-surface-2 text-muted"
                  }`}
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
          {create.isError && <p className="text-sm text-error-text">{(create.error as Error).message}</p>}
          <Button type="submit" variant="primary" className="w-full" disabled={create.isPending || !url.trim() || !events.length}>
            {create.isPending && <Loader2 className="size-4 animate-spin" />} Create
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeliveriesDialog({ hook, onClose }: { hook: Webhook | null; onClose: () => void }) {
  const { data: deliveries } = useQuery({
    queryKey: ["webhook-deliveries", hook?.id],
    queryFn: () => listWebhookDeliveries(hook!.id),
    enabled: Boolean(hook),
    refetchInterval: 3000,
  });

  return (
    <Dialog open={Boolean(hook)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="truncate font-mono text-sm">{hook?.url}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-1.5 overflow-y-auto scroll-thin">
          {(deliveries ?? []).length === 0 && <p className="text-sm text-muted">No deliveries yet.</p>}
          {(deliveries ?? []).map((d) => (
            <div key={d.id} className="flex items-center gap-2 rounded-md border border-border bg-surface-2/40 px-3 py-2 text-xs">
              <span className="font-mono text-text">{d.event}</span>
              <Badge variant={d.status === "delivered" ? "success" : d.status === "failed" ? "error" : "warn"}>
                {d.status}
              </Badge>
              <span className="text-faint">{d.response_status ?? "—"}</span>
              <span className="ml-auto text-faint">
                {d.attempts} attempt{d.attempts === 1 ? "" : "s"} · {relativeTime(d.created_at)}
              </span>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
