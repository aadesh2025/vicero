"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  KeyRound,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  Webhook as WebhookIcon,
} from "lucide-react";
import { Field, SectionCard } from "@/components/builder/field";
import { StatusPill } from "@/components/shared/status-pill";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE } from "@/lib/api/config";
import {
  createInboundHook,
  deleteInboundHook,
  listInboundHookEvents,
  listInboundHooks,
  rotateInboundHookSecret,
  updateInboundHook,
  type InboundHook,
} from "@/lib/api/hooks";
import { UPGRADE_PATH } from "@/lib/api/plan";
import {
  createAgentWebhook,
  deleteWebhook,
  listAgentWebhooks,
  listWebhookDeliveries,
  testWebhook,
  updateWebhook,
  webhookEventCatalog,
  type Webhook,
} from "@/lib/api/settings";
import { listWorkflows } from "@/lib/api/workflows";
import { relativeTime } from "@/lib/utils";
import { newKv } from "./api-tool-form";
import { KeyValueEditor } from "./editors";
import {
  ACTION_LABELS,
  CHANNELS,
  emptyHookForm,
  hookProblems,
  hookToBody,
  sampleCurl,
  type HookForm as HookFormState,
} from "./hook-form";
import { useToolLimit } from "./limits";

function LockNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-2/50 p-3 text-sm">
      <Lock className="size-4 text-ai" aria-hidden />
      <span className="flex-1 text-muted">{children}</span>
      <Link
        href={UPGRADE_PATH}
        className="font-extrabold text-ai-text hover:underline"
      >
        Upgrade
      </Link>
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => {
        void navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? <Check /> : <Copy />} {done ? "Copied" : label}
    </Button>
  );
}

/** Both directions of webhooks for one agent (docs/28 T5.5): events leaving for your systems, and the URLs your
 *  systems call to make this agent do something. */
export function WebhooksPanel({ agentId }: { agentId: string | null }) {
  return (
    <div className="space-y-6">
      <OutboundSection agentId={agentId} />
      <InboundSection agentId={agentId} />
    </div>
  );
}

// ── Outbound ─────────────────────────────────────────────────────────────────
function OutboundSection({ agentId }: { agentId: string | null }) {
  const qc = useQueryClient();
  const { locked } = useToolLimit("webhooks");
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<Webhook | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const { data } = useQuery({
    queryKey: ["agent-webhooks", agentId],
    queryFn: () => listAgentWebhooks(agentId as string),
    enabled: Boolean(agentId) && !locked,
  });
  const refresh = () =>
    void qc.invalidateQueries({ queryKey: ["agent-webhooks", agentId] });
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateWebhook(id, { enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteWebhook(id),
    onSuccess: refresh,
  });

  async function sendTest(id: string) {
    setTestResult((r) => ({ ...r, [id]: "Sending..." }));
    try {
      const d = await testWebhook(id);
      setTestResult((r) => ({
        ...r,
        [id]:
          d.status === "delivered"
            ? `Delivered (${d.response_status})`
            : `Not delivered (${d.status})`,
      }));
    } catch (e) {
      setTestResult((r) => ({ ...r, [id]: (e as Error).message }));
    }
  }

  return (
    <SectionCard
      title="Send events to your systems"
      description="Signed POSTs for this agent's events: messages, tool calls, leads, finished conversations."
    >
      {locked ? (
        <LockNote>Webhooks are part of the Pro and Business plans.</LockNote>
      ) : (
        <>
          <ul className="space-y-2" aria-label="Outbound webhooks">
            {(data ?? []).length === 0 && (
              <li className="text-sm text-muted">
                No endpoints for this agent yet.
              </li>
            )}
            {(data ?? []).map((h) => (
              <li
                key={h.id}
                className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3"
              >
                <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                  <WebhookIcon className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-xs text-text">
                    {h.url}
                  </div>
                  <div className="text-xs text-muted">
                    {h.events.includes("*")
                      ? "Every event"
                      : `${h.events.length} events`}
                  </div>
                </div>
                {testResult[h.id] && (
                  <span role="status" className="text-xs text-muted">
                    {testResult[h.id]}
                  </span>
                )}
                <Switch
                  aria-label={`${h.url} enabled`}
                  checked={h.enabled}
                  onCheckedChange={(v) =>
                    toggle.mutate({ id: h.id, enabled: v })
                  }
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void sendTest(h.id)}
                >
                  <Send /> Test
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setViewing(h)}
                >
                  Deliveries
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Delete endpoint"
                  onClick={() => remove.mutate(h.id)}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCreating(true)}
            disabled={!agentId}
          >
            <Plus /> Add endpoint
          </Button>
        </>
      )}
      <EndpointDialog
        open={creating}
        onOpenChange={setCreating}
        agentId={agentId}
        onCreated={refresh}
      />
      <DeliveriesDialog hook={viewing} onClose={() => setViewing(null)} />
    </SectionCard>
  );
}

function EndpointDialog({
  open,
  onOpenChange,
  agentId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  agentId: string | null;
  onCreated: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <EndpointForm
          agentId={agentId}
          onClose={() => onOpenChange(false)}
          onCreated={onCreated}
        />
      </DialogContent>
    </Dialog>
  );
}

function EndpointForm({
  agentId,
  onClose,
  onCreated,
}: {
  agentId: string | null;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [url, setUrl] = useState("");
  const [all, setAll] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [secret, setSecret] = useState<string | null>(null);
  const { data: catalog } = useQuery({
    queryKey: ["webhook-events"],
    queryFn: webhookEventCatalog,
  });

  const create = useMutation({
    mutationFn: () =>
      createAgentWebhook(
        agentId as string,
        url.trim(),
        all ? ["*"] : [...picked],
      ),
    onSuccess: (w) => {
      setSecret(w.secret);
      onCreated();
    },
  });

  return (
    <>
      <DialogHeader>
        <DialogTitle>Add endpoint</DialogTitle>
        <DialogDescription>
          Receives only this agent&apos;s events.
        </DialogDescription>
      </DialogHeader>
      {secret ? (
        <div className="space-y-3">
          <p className="text-sm text-muted">
            Copy the signing secret now. It is not shown again.
          </p>
          <pre
            aria-label="Signing secret"
            className="overflow-x-auto rounded-md bg-surface-2 p-3 font-mono text-xs text-text"
          >
            {secret}
          </pre>
          <div className="flex justify-end gap-2">
            <CopyButton text={secret} label="Copy secret" />
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (/^https?:\/\//i.test(url.trim())) create.mutate();
          }}
        >
          <Field label="URL">
            <Input
              aria-label="Endpoint URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/vicero-events"
              className="font-mono text-[13px]"
            />
          </Field>
          <label className="flex items-center gap-2 text-sm font-bold text-text">
            <Switch
              aria-label="Every event"
              checked={all}
              onCheckedChange={setAll}
            />{" "}
            Send every event
          </label>
          {!all && (
            <ul className="grid gap-1.5 sm:grid-cols-2" aria-label="Events">
              {(catalog ?? []).map((ev) => (
                <li key={ev} className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    id={`ev-${ev}`}
                    checked={picked.has(ev)}
                    onChange={(e) => {
                      const next = new Set(picked);
                      if (e.target.checked) next.add(ev);
                      else next.delete(ev);
                      setPicked(next);
                    }}
                    className="size-4 accent-[var(--accent)]"
                  />
                  <label htmlFor={`ev-${ev}`} className="font-mono text-text">
                    {ev}
                  </label>
                </li>
              ))}
            </ul>
          )}
          {create.isError && (
            <p role="alert" className="text-sm text-error-text">
              {(create.error as Error).message}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={
                create.isPending ||
                !/^https?:\/\//i.test(url.trim()) ||
                (!all && picked.size === 0)
              }
            >
              {create.isPending && <Loader2 className="animate-spin" />} Add
              endpoint
            </Button>
          </div>
        </form>
      )}
    </>
  );
}

function DeliveriesDialog({
  hook,
  onClose,
}: {
  hook: Webhook | null;
  onClose: () => void;
}) {
  const { data } = useQuery({
    queryKey: ["webhook-deliveries", hook?.id],
    queryFn: () => listWebhookDeliveries(hook!.id),
    enabled: Boolean(hook),
  });
  return (
    <Dialog open={Boolean(hook)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Deliveries</DialogTitle>
          <DialogDescription className="break-all font-mono text-xs">
            {hook?.url}
          </DialogDescription>
        </DialogHeader>
        {(data ?? []).length === 0 ? (
          <p className="text-sm text-muted">Nothing sent yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {(data ?? []).map((d) => (
              <li
                key={d.id}
                className="flex items-center gap-2 rounded-md border border-border bg-surface-2/40 px-3 py-2 text-xs"
              >
                <span className="font-mono text-text">{d.event}</span>
                <StatusPill status={d.status} />
                <span className="ml-auto text-faint">
                  {d.response_status ?? "-"} · {d.attempts} tr
                  {d.attempts === 1 ? "y" : "ies"} ·{" "}
                  {relativeTime(d.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── Inbound ──────────────────────────────────────────────────────────────────
function InboundSection({ agentId }: { agentId: string | null }) {
  const qc = useQueryClient();
  const { locked } = useToolLimit("inbound_hooks");
  const [creating, setCreating] = useState(false);
  const [revealed, setRevealed] = useState<InboundHook | null>(null);
  const [viewing, setViewing] = useState<InboundHook | null>(null);

  const { data } = useQuery({
    queryKey: ["inbound-hooks", agentId],
    queryFn: () => listInboundHooks(agentId as string),
    enabled: Boolean(agentId) && !locked,
  });
  const refresh = () =>
    void qc.invalidateQueries({ queryKey: ["inbound-hooks", agentId] });
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateInboundHook(id, { enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteInboundHook(id),
    onSuccess: refresh,
  });
  const rotate = useMutation({
    mutationFn: (id: string) => rotateInboundHookSecret(id),
    onSuccess: (h) => {
      setRevealed(h);
      refresh();
    },
  });

  return (
    <SectionCard
      title="Let other systems trigger this agent"
      description="A private URL your shop, CRM or form tool calls. Each hook does one thing."
    >
      {locked ? (
        <LockNote>
          Inbound hooks are part of the Pro and Business plans.
        </LockNote>
      ) : (
        <>
          <ul className="space-y-2" aria-label="Inbound hooks">
            {(data ?? []).length === 0 && (
              <li className="text-sm text-muted">No inbound hooks yet.</li>
            )}
            {(data ?? []).map((h) => (
              <li
                key={h.id}
                className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3"
                data-testid="inbound-hook"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-text">
                      {h.name}
                    </span>
                    <Badge variant="neutral">
                      {ACTION_LABELS[h.action].title}
                    </Badge>
                  </div>
                  <div className="truncate font-mono text-xs text-muted">
                    {h.url_path}
                  </div>
                  <div className="text-xs text-faint">
                    {h.last_called_at
                      ? `Last called ${relativeTime(h.last_called_at)}`
                      : "Not called yet"}
                  </div>
                </div>
                <CopyButton
                  text={`${API_BASE}${h.url_path}`}
                  label="Copy URL"
                />
                <Switch
                  aria-label={`${h.name} enabled`}
                  checked={h.enabled}
                  onCheckedChange={(v) =>
                    toggle.mutate({ id: h.id, enabled: v })
                  }
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setViewing(h)}
                >
                  Recent calls
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`New secret for ${h.name}`}
                  onClick={() => rotate.mutate(h.id)}
                  title="Make a new secret"
                >
                  <RefreshCw />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Delete ${h.name}`}
                  onClick={() => remove.mutate(h.id)}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCreating(true)}
            disabled={!agentId}
          >
            <Plus /> New inbound hook
          </Button>
        </>
      )}
      <HookDialog
        open={creating}
        onOpenChange={setCreating}
        agentId={agentId}
        onCreated={(h) => {
          setRevealed(h);
          refresh();
        }}
      />
      <SecretDialog hook={revealed} onClose={() => setRevealed(null)} />
      <HookEventsDialog hook={viewing} onClose={() => setViewing(null)} />
    </SectionCard>
  );
}

function HookDialog({
  open,
  onOpenChange,
  agentId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  agentId: string | null;
  onCreated: (h: InboundHook) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <HookForm
          agentId={agentId}
          onClose={() => onOpenChange(false)}
          onCreated={onCreated}
        />
      </DialogContent>
    </Dialog>
  );
}

function HookForm({
  agentId,
  onClose,
  onCreated,
}: {
  agentId: string | null;
  onClose: () => void;
  onCreated: (h: InboundHook) => void;
}) {
  const [f, setF] = useState<HookFormState>(emptyHookForm);
  const set = <K extends keyof HookFormState>(k: K, v: HookFormState[K]) =>
    setF((x) => ({ ...x, [k]: v }));
  const { data: workflows } = useQuery({
    queryKey: ["agent-workflows", agentId],
    queryFn: () => listWorkflows(agentId as string),
    enabled: Boolean(agentId) && f.action === "run_workflow",
  });

  const problems = hookProblems(f);
  const create = useMutation({
    mutationFn: () => createInboundHook(agentId as string, hookToBody(f)),
    onSuccess: (h) => {
      onCreated(h);
      onClose();
    },
  });
  const fields = f.mapping.map((m) => m.key.trim()).filter(Boolean);
  const needsContact =
    f.action === "send_message" || f.action === "set_variables";

  return (
    <>
      <DialogHeader>
        <DialogTitle>New inbound hook</DialogTitle>
        <DialogDescription>{ACTION_LABELS[f.action].help}</DialogDescription>
      </DialogHeader>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input
              aria-label="Hook name"
              value={f.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="Order shipped"
            />
          </Field>
          <Field label="What it does">
            <select
              aria-label="Hook action"
              value={f.action}
              onChange={(e) =>
                set("action", e.target.value as HookFormState["action"])
              }
              className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
            >
              {(
                Object.keys(ACTION_LABELS) as (keyof typeof ACTION_LABELS)[]
              ).map((a) => (
                <option key={a} value={a}>
                  {ACTION_LABELS[a].title}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="How callers prove who they are">
          <select
            aria-label="Hook authentication"
            value={f.authMode}
            onChange={(e) =>
              set("authMode", e.target.value as HookFormState["authMode"])
            }
            className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
          >
            <option value="hmac">Signed request (recommended)</option>
            <option value="bearer">Bearer secret</option>
          </select>
        </Field>
        <Field
          label="Fields to read from the request"
          description="Only these are used; everything else in the request is ignored. Selector example: $.order.id"
        >
          <KeyValueEditor
            rows={f.mapping}
            onChange={(r) => set("mapping", r)}
            keyLabel="Field name"
            valueLabel="Selector"
            keyPlaceholder="order_id"
            valuePlaceholder="$.order.id"
            candidates={[]}
            addLabel="Add field"
            template={false}
          />
        </Field>

        {needsContact && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Channel">
              <select
                aria-label="Channel"
                value={f.channel}
                onChange={(e) =>
                  set("channel", e.target.value as HookFormState["channel"])
                }
                className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
              >
                {CHANNELS.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </Field>
            <Field
              label="Which field is the customer's id"
              description="Their phone number, handle or visitor id on that channel."
            >
              <select
                aria-label="Contact field"
                value={f.contactField}
                onChange={(e) => set("contactField", e.target.value)}
                className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
              >
                {[...new Set([f.contactField, ...fields])].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </Field>
          </div>
        )}
        {(f.action === "send_message" || f.action === "agent_turn") && (
          <Field
            label={
              f.action === "agent_turn"
                ? "Update the agent receives"
                : "Message to send"
            }
            description="Use {{field}} to insert a value from the request."
          >
            <Textarea
              aria-label="Hook message"
              rows={3}
              value={f.message}
              onChange={(e) => set("message", e.target.value)}
              placeholder="Order {{order_id}} has shipped."
            />
          </Field>
        )}
        {f.action === "send_message" && f.channel === "whatsapp" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Or an approved WhatsApp template"
              description="Used instead of the message, so it works after 24 hours."
            >
              <Input
                aria-label="Template name"
                value={f.template}
                onChange={(e) => set("template", e.target.value)}
                placeholder="order_update"
              />
            </Field>
            <Field
              label="Template fields in order"
              description="Comma separated field names."
            >
              <Input
                aria-label="Template fields"
                value={f.templateParams}
                onChange={(e) => set("templateParams", e.target.value)}
                placeholder="order_id"
              />
            </Field>
          </div>
        )}
        {f.action === "run_workflow" && (
          <Field label="Workflow">
            <select
              aria-label="Workflow"
              value={f.workflowId}
              onChange={(e) => set("workflowId", e.target.value)}
              className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
            >
              <option value="">Choose one...</option>
              {(workflows ?? []).map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </Field>
        )}

        {problems.length > 0 && f.name.trim() && (
          <ul
            aria-label="Things to fix"
            className="space-y-1 rounded-lg bg-warn-soft px-3 py-2 text-xs font-medium text-warn-text"
          >
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        {create.isError && (
          <p role="alert" className="text-sm text-error-text">
            {(create.error as Error).message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={problems.length > 0 || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending && <Loader2 className="animate-spin" />} Create
            hook
          </Button>
        </div>
      </div>
    </>
  );
}

function SecretDialog({
  hook,
  onClose,
}: {
  hook: InboundHook | null;
  onClose: () => void;
}) {
  if (!hook) return null;
  const url = `${API_BASE}${hook.url_path}`;
  const curl = sampleCurl(
    API_BASE,
    hook.url_path,
    hook.auth_mode,
    Object.entries(hook.payload_mapping).map(([k, v]) => newKv(k, v)),
  );
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{hook.name}</DialogTitle>
          <DialogDescription>
            Copy the secret now. It is shown once; make a new one if you lose
            it.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <Field label="URL">
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md bg-surface-2 px-3 py-2 font-mono text-xs text-text">
                {url}
              </code>
              <CopyButton text={url} label="Copy URL" />
            </div>
          </Field>
          <Field label="Secret">
            <div className="flex items-center gap-2">
              <code
                aria-label="Hook secret"
                className="min-w-0 flex-1 truncate rounded-md bg-surface-2 px-3 py-2 font-mono text-xs text-text"
              >
                {hook.secret ??
                  "Only shown when the hook is created or its secret is remade."}
              </code>
              {hook.secret && (
                <CopyButton text={hook.secret} label="Copy secret" />
              )}
            </div>
          </Field>
          <Field
            label="Try it"
            description="Set HOOK_SECRET in your shell first. The secret is never written into this example."
          >
            <pre
              aria-label="Sample call"
              className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface-2 p-3 font-mono text-xs text-muted"
            >
              {curl}
            </pre>
            <CopyButton text={curl} label="Copy example" />
          </Field>
          <div className="flex justify-end">
            <Button variant="primary" onClick={onClose}>
              <KeyRound /> I have saved the secret
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function HookEventsDialog({
  hook,
  onClose,
}: {
  hook: InboundHook | null;
  onClose: () => void;
}) {
  const { data } = useQuery({
    queryKey: ["inbound-hook-events", hook?.id],
    queryFn: () => listInboundHookEvents(hook!.id, 20),
    enabled: Boolean(hook),
  });
  return (
    <Dialog open={Boolean(hook)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Recent calls</DialogTitle>
          <DialogDescription>
            The last 20 calls to {hook?.name}, with secrets removed. Kept for 30
            days.
          </DialogDescription>
        </DialogHeader>
        {(data ?? []).length === 0 ? (
          <p className="text-sm text-muted">No calls yet.</p>
        ) : (
          <ul className="space-y-1.5" aria-label="Recent calls">
            {(data ?? []).map((e) => (
              <li
                key={e.id}
                className="rounded-md border border-border bg-surface-2/40 px-3 py-2 text-xs"
              >
                <div className="flex items-center gap-2">
                  <StatusPill
                    status={e.status === "duplicate" ? "info" : e.status}
                  />
                  <span className="text-muted">{e.http_status ?? "-"}</span>
                  {e.error && (
                    <span className="text-error-text">{e.error}</span>
                  )}
                  <span className="ml-auto text-faint">
                    {e.latency_ms ?? "-"} ms · {relativeTime(e.created_at)}
                  </span>
                </div>
                {e.payload_sanitized && (
                  <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded bg-surface p-2 font-mono text-muted">
                    {JSON.stringify(e.payload_sanitized, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
