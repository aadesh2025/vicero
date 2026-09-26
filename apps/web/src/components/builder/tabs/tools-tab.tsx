"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Loader2, Plus, Trash2, Wrench, Zap } from "lucide-react";
import { SectionCard } from "@/components/builder/field";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { LockedRegion } from "@/components/plan/locked";
import { useBuilder } from "@/lib/store/builder";
import {
  createTool,
  deleteTool,
  listBuiltinTools,
  listToolRuns,
  listTools,
  testTool,
  type ApiTool,
} from "@/lib/api/tools";

/** Custom tools, built-ins and MCP servers are a paid feature; on a trial they are shown, locked. */
export function ToolsTab() {
  return (
    <LockedRegion feature="tool_calling" label="Tools and MCP servers">
      <ToolsTabInner />
    </LockedRegion>
  );
}

function ToolsTabInner() {
  const draft = useBuilder((s) => s.draft);
  const agentId = useBuilder((s) => s.agentId);
  const update = useBuilder((s) => s.update);
  const qc = useQueryClient();

  const [httpOpen, setHttpOpen] = useState(false);
  const [testing, setTesting] = useState<ApiTool | null>(null);

  const { data: builtins } = useQuery({ queryKey: ["builtin-tools"], queryFn: listBuiltinTools });
  const { data: tools, isLoading } = useQuery({
    queryKey: ["tools", agentId],
    queryFn: () => listTools(agentId ?? undefined),
    enabled: Boolean(agentId),
  });
  const { data: runs } = useQuery({
    queryKey: ["tool-runs", agentId],
    queryFn: () => listToolRuns(),
    enabled: Boolean(agentId),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["tools", agentId] });

  const toggleBuiltin = useMutation({
    mutationFn: async (name: string) => {
      const existing = (tools ?? []).find((t) => t.type === "builtin" && t.name === name);
      if (existing) await deleteTool(existing.id);
      else await createTool({ name, type: "builtin", agent_id: agentId ?? undefined });
    },
    onSuccess: invalidate,
  });

  const removeTool = useMutation({ mutationFn: (id: string) => deleteTool(id), onSuccess: invalidate });

  if (!draft) return null;
  const enabledTools = draft.features.tools;
  const byName = new Map((tools ?? []).filter((t) => t.type === "builtin").map((t) => [t.name, t]));
  const httpTools = (tools ?? []).filter((t) => t.type === "http");
  const toolName = (id: string) => (tools ?? []).find((t) => t.id === id)?.name ?? id.slice(0, 8);

  return (
    <div className="space-y-6">
      <SectionCard title="Tool calling" description="Let the agent call tools mid-conversation.">
        <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
          <div className="flex-1">
            <div className="text-sm font-medium text-text">Enable tools</div>
            <div className="text-xs text-muted">
              Runs an iteration-capped loop; every call is logged to tool_runs.
            </div>
          </div>
          <Switch checked={enabledTools} onCheckedChange={(v) => update((d) => void (d.features.tools = v))} />
        </div>
      </SectionCard>

      <SectionCard title="Built-in tools" description="Ready-made capabilities you can switch on.">
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <ul className="space-y-2">
            {(builtins ?? []).map((b) => {
              const on = byName.has(b.name);
              return (
                <li key={b.name} className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
                  <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                    <Zap className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-sm text-text">{b.name}</div>
                    <div className="truncate text-xs text-muted">{b.description}</div>
                  </div>
                  <Switch checked={on} disabled={toggleBuiltin.isPending} onCheckedChange={() => toggleBuiltin.mutate(b.name)} />
                </li>
              );
            })}
          </ul>
        )}
      </SectionCard>

      <SectionCard title="HTTP tools" description="Call your own APIs with a guarded, SSRF-protected request.">
        <ul className="space-y-2">
          {httpTools.map((t) => (
            <li key={t.id} className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
              <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                <Globe className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-mono text-sm text-text">{t.name}</div>
                <div className="truncate text-xs text-muted">
                  {String(t.config.method ?? "GET")} {String(t.config.url ?? "")}
                </div>
              </div>
              <Button variant="outline" size="sm" onClick={() => setTesting(t)}>
                Test
              </Button>
              <button
                onClick={() => removeTool.mutate(t.id)}
                className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                title="Delete tool"
              >
                <Trash2 className="size-4" />
              </button>
            </li>
          ))}
        </ul>
        <Button variant="outline" size="sm" onClick={() => setHttpOpen(true)}>
          <Plus /> New HTTP tool
        </Button>
      </SectionCard>

      <SectionCard title="Recent runs" description="The tool_runs log — inputs, status, and latency.">
        {(runs ?? []).length === 0 ? (
          <p className="text-sm text-muted">No tool runs yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {(runs ?? []).slice(0, 8).map((r) => (
              <li key={r.id} className="flex items-center gap-2 rounded-md border border-border bg-surface-2/40 px-3 py-2 text-xs">
                <span className="font-mono text-text">{toolName(r.tool_id)}</span>
                <Badge variant={r.status === "success" ? "success" : "error"}>{r.status}</Badge>
                <span className="ml-auto text-faint">{r.latency_ms ?? "—"} ms</span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-2/40 p-4 text-sm text-muted">
        <Wrench className="size-4 shrink-0 text-faint" />
        Enable tools above, switch on the built-ins you want, then try the playground — the agent
        calls them mid-conversation.
      </div>

      <NewHttpToolDialog open={httpOpen} onOpenChange={setHttpOpen} agentId={agentId} onCreated={invalidate} />
      <TestToolDialog tool={testing} onClose={() => setTesting(null)} />
    </div>
  );
}

function NewHttpToolDialog({
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
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [method, setMethod] = useState("GET");

  const create = useMutation({
    mutationFn: () =>
      createTool({
        name,
        type: "http",
        agent_id: agentId ?? undefined,
        config: { method, url },
        input_schema: { type: "object", properties: {}, required: [] },
      }),
    onSuccess: () => {
      setName("");
      setUrl("");
      onOpenChange(false);
      onCreated();
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New HTTP tool</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
          className="space-y-3"
        >
          <Input placeholder="tool_name (snake_case)" value={name} onChange={(e) => setName(e.target.value)} />
          <div className="flex gap-2">
            <select
              value={method}
              onChange={(e) => setMethod(e.target.value)}
              className="h-9 rounded-md border border-border bg-surface-2 px-2 text-sm text-text"
            >
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
            <Input
              placeholder="https://api.example.com/path  (use {{arg}} for arguments)"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          {create.isError && <p className="text-sm text-error-text">{(create.error as Error).message}</p>}
          <Button type="submit" variant="primary" className="w-full" disabled={create.isPending || !name.trim() || !url.trim()}>
            {create.isPending && <Loader2 className="size-4 animate-spin" />} Create tool
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TestToolDialog({ tool, onClose }: { tool: ApiTool | null; onClose: () => void }) {
  const [input, setInput] = useState("{}");
  const run = useMutation({
    mutationFn: () => testTool(tool!.id, JSON.parse(input || "{}")),
  });

  return (
    <Dialog open={Boolean(tool)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-mono">{tool?.name}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={4}
            className="w-full rounded-md border border-border bg-surface-2 p-2 font-mono text-xs text-text"
            placeholder='{"arg": "value"}'
          />
          <Button variant="primary" className="w-full" disabled={run.isPending} onClick={() => run.mutate()}>
            {run.isPending && <Loader2 className="size-4 animate-spin" />} Run test
          </Button>
          {run.data && (
            <div className="space-y-1 rounded-md border border-border bg-surface-2/40 p-3 text-xs">
              <div className="flex items-center gap-2">
                <Badge variant={run.data.status === "success" ? "success" : "error"}>{run.data.status}</Badge>
                <span className="text-faint">{run.data.latency_ms} ms</span>
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap text-muted">
                {JSON.stringify(run.data.error ?? run.data.output, null, 2)}
              </pre>
            </div>
          )}
          {run.isError && <p className="text-sm text-error-text">{(run.error as Error).message}</p>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
