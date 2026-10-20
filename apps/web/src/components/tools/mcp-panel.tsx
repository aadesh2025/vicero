"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  Lock,
  Plug,
  Plus,
  RefreshCw,
  Settings2,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { SectionCard, Field } from "@/components/builder/field";
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
import { listConnections } from "@/lib/api/connections";
import {
  MAX_TOOLS_PER_AGENT,
  createMcpServer,
  deleteMcpServer,
  listAgentMcp,
  listMcpServers,
  refreshMcpTools,
  setAgentMcp,
  updateMcpServer,
  type McpBinding,
  type McpServer,
  type McpToolOverride,
} from "@/lib/api/mcp";
import { UPGRADE_PATH } from "@/lib/api/plan";
import { relativeTime } from "@/lib/utils";
import { useToolLimit } from "./limits";
import {
  enabledCount,
  isEnabled,
  roomLeft,
  setOverride,
  toggleTool,
  type BindingPayload,
} from "./mcp-selection";

const NO_CONNECTION = "__none__";

/** MCP servers for one agent (docs/28 T5.4): add a server, discover its tools, then tick the ones THIS agent may use.
 *  Nothing is offered to the agent until you tick it, and a turn never talks to the server to find out what it has. */
export function McpPanel({
  agentId,
  otherEnabledTools,
}: {
  agentId: string | null;
  otherEnabledTools: number;
}) {
  const qc = useQueryClient();
  const { locked } = useToolLimit("mcp_servers");
  const [adding, setAdding] = useState(false);
  const [settings, setSettings] = useState<{
    server: McpServer;
    tool: string;
  } | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const servers = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: listMcpServers,
    enabled: !locked,
  });
  const bindings = useQuery({
    queryKey: ["agent-mcp", agentId],
    queryFn: () => listAgentMcp(agentId as string),
    enabled: Boolean(agentId) && !locked,
  });
  const bound = bindings.data ?? [];
  const refreshAll = () => {
    void qc.invalidateQueries({ queryKey: ["mcp-servers"] });
    void qc.invalidateQueries({ queryKey: ["agent-mcp", agentId] });
    void qc.invalidateQueries({ queryKey: ["tools", agentId] });
  };

  const save = useMutation({
    mutationFn: (payload: BindingPayload[]) =>
      setAgentMcp(agentId as string, payload),
    onSuccess: (data) => {
      setMessage(null);
      qc.setQueryData(["agent-mcp", agentId], data);
      void qc.invalidateQueries({ queryKey: ["tools", agentId] });
    },
    onError: (e) => setMessage((e as Error).message),
  });
  const refresh = useMutation({
    mutationFn: (id: string) => refreshMcpTools(id),
    onSuccess: (res) => {
      setMessage(res.ok ? null : `Could not reach the server: ${res.error}`);
      refreshAll();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteMcpServer(id),
    onSuccess: refreshAll,
    onError: (e) => setMessage((e as Error).message),
  });
  const toggleServer = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateMcpServer(id, { enabled }),
    onSuccess: refreshAll,
  });

  const left = roomLeft(bound, otherEnabledTools);
  const used = enabledCount(bound) + otherEnabledTools;

  const description = locked
    ? "Connect your own MCP servers. Part of the Pro and Business plans."
    : "Connect a server, then choose which of its tools this agent may use.";

  return (
    <SectionCard title="MCP servers" description={description}>
      {locked ? (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-2/50 p-3 text-sm">
          <Lock className="size-4 text-ai" aria-hidden />
          <span className="flex-1 text-muted">
            MCP servers are not included in your plan.
          </span>
          <Link
            href={UPGRADE_PATH}
            className="font-extrabold text-ai-text hover:underline"
          >
            Upgrade
          </Link>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-bold text-muted" aria-live="polite">
              {used} of {MAX_TOOLS_PER_AGENT} tools in use by this agent
            </span>
            <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
              <Plus /> Add server
            </Button>
          </div>
          {message && (
            <p role="alert" className="text-sm text-error-text">
              {message}
            </p>
          )}
          {(servers.data ?? []).length === 0 ? (
            <p className="text-sm text-muted">No MCP servers yet.</p>
          ) : (
            <ul className="space-y-3" aria-label="MCP servers">
              {(servers.data ?? []).map((s) => (
                <li
                  key={s.id}
                  className="rounded-lg border border-border bg-surface-2/40 p-3"
                  data-testid="mcp-server"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                      <Plug className="size-4" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-bold text-text">
                        {s.name}
                      </div>
                      <div className="truncate font-mono text-xs text-muted">
                        {s.url_or_command}
                      </div>
                    </div>
                    <Badge variant="neutral">
                      {s.transport === "streamable_http"
                        ? "Streamable HTTP"
                        : s.transport}
                    </Badge>
                    <Switch
                      aria-label={`${s.name} enabled`}
                      checked={s.enabled}
                      onCheckedChange={(v) =>
                        toggleServer.mutate({ id: s.id, enabled: v })
                      }
                    />
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => refresh.mutate(s.id)}
                      disabled={refresh.isPending && refresh.variables === s.id}
                    >
                      {refresh.isPending && refresh.variables === s.id ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <RefreshCw />
                      )}
                      Refresh tools
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Delete ${s.name}`}
                      onClick={() => remove.mutate(s.id)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                  <p className="mt-1 text-xs text-faint">
                    {s.tools_cached_at
                      ? `${s.tools.length} tools found ${relativeTime(s.tools_cached_at)}`
                      : "Tools not discovered yet. Press Refresh tools."}
                    {s.last_error && (
                      <span className="text-error-text">
                        {" "}
                        · Last check failed: {s.last_error}
                      </span>
                    )}
                  </p>
                  {s.tools.length > 0 && (
                    <ul
                      className="mt-2 grid gap-1.5 sm:grid-cols-2"
                      aria-label={`${s.name} tools`}
                    >
                      {s.tools.map((t) => {
                        const on = isEnabled(bound, s.id, t.name);
                        const blocked = !on && left <= 0;
                        return (
                          <li
                            key={t.name}
                            className="flex items-start gap-2 rounded-md border border-border bg-surface px-2.5 py-2"
                          >
                            <input
                              type="checkbox"
                              id={`mcp-${s.id}-${t.name}`}
                              aria-label={`Allow ${t.name}`}
                              checked={on}
                              disabled={blocked || save.isPending || !agentId}
                              onChange={(e) => {
                                const next = toggleTool(
                                  bound,
                                  s.id,
                                  t.name,
                                  e.target.checked,
                                  otherEnabledTools,
                                );
                                if (next) save.mutate(next);
                              }}
                              className="mt-0.5 size-4 accent-[var(--accent)]"
                            />
                            <label
                              htmlFor={`mcp-${s.id}-${t.name}`}
                              className="min-w-0 flex-1 cursor-pointer"
                            >
                              <span className="block truncate font-mono text-xs text-text">
                                {t.name}
                              </span>
                              <span className="line-clamp-2 text-[11px] text-muted">
                                {t.description || "No description"}
                              </span>
                            </label>
                            {on && (
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                aria-label={`Settings for ${t.name}`}
                                onClick={() =>
                                  setSettings({ server: s, tool: t.name })
                                }
                              >
                                <Settings2 />
                              </Button>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  {left <= 0 && s.tools.length > 0 && (
                    <p className="mt-2 text-xs text-warn-text">
                      This agent is at {MAX_TOOLS_PER_AGENT} tools. Switch one
                      off, or split the work across agents.
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <AddServerDialog
        open={adding}
        onOpenChange={setAdding}
        onCreated={refreshAll}
      />
      <ToolSettingsDialog
        target={settings}
        bindings={bound}
        onClose={() => setSettings(null)}
        onSave={(payload) => {
          save.mutate(payload);
          setSettings(null);
        }}
      />
    </SectionCard>
  );
}

function AddServerDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <AddServerForm
          onClose={() => onOpenChange(false)}
          onCreated={onCreated}
        />
      </DialogContent>
    </Dialog>
  );
}

function AddServerForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [connection, setConnection] = useState(NO_CONNECTION);
  const { data: connections } = useQuery({
    queryKey: ["connections"],
    queryFn: listConnections,
  });

  const create = useMutation({
    mutationFn: async () => {
      const server = await createMcpServer({
        name: name.trim(),
        url_or_command: url.trim(),
        transport: "streamable_http",
        connection_id: connection === NO_CONNECTION ? null : connection,
        headers:
          token.trim() && connection === NO_CONNECTION
            ? { Authorization: `Bearer ${token.trim()}` }
            : undefined,
      });
      await refreshMcpTools(server.id).catch(() => undefined); // find its tools straight away; a failure shows on the card
      return server;
    },
    onSuccess: () => {
      onCreated();
      onClose();
    },
  });

  return (
    <>
      <DialogHeader>
        <DialogTitle>Add MCP server</DialogTitle>
        <DialogDescription>
          A server that speaks the Model Context Protocol over HTTP.
        </DialogDescription>
      </DialogHeader>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && /^https?:\/\//i.test(url.trim())) create.mutate();
        }}
      >
        <Field
          label="Name"
          description="Becomes the prefix of its tools, e.g. crm__lookup_order."
        >
          <Input
            aria-label="Server name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="CRM"
          />
        </Field>
        <Field label="Server URL">
          <Input
            aria-label="Server URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://mcp.example.com/mcp"
            className="font-mono text-[13px]"
          />
        </Field>
        <Field
          label="Sign in with"
          description="A saved connection, or a bearer token for this server only."
        >
          <select
            aria-label="Connection"
            value={connection}
            onChange={(e) => setConnection(e.target.value)}
            className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
          >
            <option value={NO_CONNECTION}>A token typed here</option>
            {(connections ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        {connection === NO_CONNECTION && (
          <Field label="Bearer token (optional)">
            <Input
              aria-label="Bearer token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="font-mono text-[13px]"
            />
          </Field>
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
              !name.trim() ||
              !/^https?:\/\//i.test(url.trim())
            }
          >
            {create.isPending && <Loader2 className="animate-spin" />} Add and
            find tools
          </Button>
        </div>
      </form>
    </>
  );
}

function ToolSettingsDialog({
  target,
  bindings,
  onClose,
  onSave,
}: {
  target: { server: McpServer; tool: string } | null;
  bindings: McpBinding[];
  onClose: () => void;
  onSave: (payload: BindingPayload[]) => void;
}) {
  if (!target) return null;
  // Keyed by the tool, so opening another tool starts from its own saved settings.
  return (
    <ToolSettingsForm
      key={`${target.server.id}/${target.tool}`}
      target={target}
      bindings={bindings}
      onClose={onClose}
      onSave={onSave}
    />
  );
}

function ToolSettingsForm({
  target,
  bindings,
  onClose,
  onSave,
}: {
  target: { server: McpServer; tool: string };
  bindings: McpBinding[];
  onClose: () => void;
  onSave: (payload: BindingPayload[]) => void;
}) {
  const saved: McpToolOverride =
    bindings.find((b) => b.server_id === target.server.id)?.tool_overrides[
      target.tool
    ] ?? {};
  const [description, setDescription] = useState(saved.description ?? "");
  const [approval, setApproval] = useState(Boolean(saved.approval_required));
  const [perMinute, setPerMinute] = useState(
    saved.rate_limit?.per_minute ? String(saved.rate_limit.per_minute) : "",
  );
  const minute = Number(perMinute);
  const save = () => {
    const override: McpToolOverride = {
      description,
      approval_required: approval,
    };
    if (Number.isInteger(minute) && minute > 0)
      override.rate_limit = { per_minute: minute };
    onSave(setOverride(bindings, target.server.id, target.tool, override));
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono">{target.tool}</DialogTitle>
          <DialogDescription>
            How this agent uses the tool. Other agents are not affected.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <Field
            label="What the agent is told"
            description="Replaces the server's description for this agent. Leave empty to keep it."
          >
            <Textarea
              aria-label="Description for this agent"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>
          <label className="flex items-start gap-3 rounded-lg border border-border bg-surface-2/50 p-3">
            <Switch
              aria-label="Require approval"
              checked={approval}
              onCheckedChange={setApproval}
            />
            <span>
              <span className="block text-sm font-bold text-text">
                Ask a teammate before it runs
              </span>
              <span className="block text-xs text-muted">
                Each call waits in the Inbox under Tool approvals.
              </span>
            </span>
          </label>
          <Field label="Most calls per minute">
            <Input
              aria-label="Most calls per minute"
              inputMode="numeric"
              value={perMinute}
              onChange={(e) => setPerMinute(e.target.value)}
              placeholder="no limit"
              className="w-32"
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" onClick={save}>
              Save
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
