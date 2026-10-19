"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Globe,
  Lock,
  Pencil,
  Play,
  Plus,
  Trash2,
  Workflow,
  Wrench,
  Zap,
} from "lucide-react";
import { SectionCard } from "@/components/builder/field";
import { GoogleCalendarCard } from "@/components/builder/google-calendar-card";
import { LockedRegion } from "@/components/plan/locked";
import { StatusPill } from "@/components/shared/status-pill";
import { ApiToolDialog } from "@/components/tools/api-tool-dialog";
import { useToolLimit } from "@/components/tools/limits";
import { McpPanel } from "@/components/tools/mcp-panel";
import { ToolLogs } from "@/components/tools/tool-logs";
import { WebhooksPanel } from "@/components/tools/webhooks-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { listConnections } from "@/lib/api/connections";
import { UPGRADE_PATH } from "@/lib/api/plan";
import {
  createTool,
  deleteTool,
  listAgentTools,
  listBuiltinTools,
  listTools,
  setAgentTools,
  toolRunSummary,
  type ApiTool,
  type ToolBinding,
} from "@/lib/api/tools";
import { useBuilder } from "@/lib/store/builder";
import { relativeTime } from "@/lib/utils";

/** Everything the agent can reach out to: built-in tools, your own APIs, MCP servers, automations and webhooks.
 *  Custom tools are a paid feature; on a trial they are shown, locked. */
export function ToolsTab() {
  const agentId = useBuilder((s) => s.agentId);
  return (
    <div className="space-y-6">
      <GoogleCalendarCard agentId={agentId} />
      <LockedRegion feature="tool_calling" label="Tools and MCP servers">
        <ToolsTabInner />
      </LockedRegion>
    </div>
  );
}

function ToolsTabInner() {
  const draft = useBuilder((s) => s.draft);
  const agentId = useBuilder((s) => s.agentId);
  const update = useBuilder((s) => s.update);
  const qc = useQueryClient();

  const [editor, setEditor] = useState<{
    tool: ApiTool | null;
    testInput?: string;
  } | null>(null);

  const { data: builtins } = useQuery({
    queryKey: ["builtin-tools"],
    queryFn: listBuiltinTools,
  });
  const { data: tools, isLoading } = useQuery({
    queryKey: ["tools", agentId],
    queryFn: () => listTools(agentId ?? undefined),
    enabled: Boolean(agentId),
  });
  const { data: bindings } = useQuery({
    queryKey: ["tool-bindings", agentId],
    queryFn: () => listAgentTools(agentId as string),
    enabled: Boolean(agentId),
  });
  const { data: summary } = useQuery({
    queryKey: ["tool-summary", agentId],
    queryFn: () => toolRunSummary(agentId ?? undefined),
    enabled: Boolean(agentId),
  });
  const { data: connections } = useQuery({
    queryKey: ["connections"],
    queryFn: listConnections,
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["tools", agentId] });
    void qc.invalidateQueries({ queryKey: ["tool-bindings", agentId] });
    void qc.invalidateQueries({ queryKey: ["tool-summary", agentId] });
    void qc.invalidateQueries({ queryKey: ["tool-run-page"] });
  };

  const toggleBuiltin = useMutation({
    mutationFn: async (name: string) => {
      const existing = (tools ?? []).find(
        (t) => t.type === "builtin" && t.name === name,
      );
      if (existing) await deleteTool(existing.id);
      else
        await createTool({
          name,
          type: "builtin",
          agent_id: agentId ?? undefined,
        });
    },
    onSuccess: refresh,
  });
  const removeTool = useMutation({
    mutationFn: (id: string) => deleteTool(id),
    onSuccess: refresh,
  });
  const setBinding = useMutation({
    mutationFn: ({ toolId, enabled }: { toolId: string; enabled: boolean }) =>
      setAgentTools(
        agentId as string,
        withEnabled(bindings ?? [], toolId, enabled),
      ),
    onSuccess: refresh,
  });

  const byName = useMemo(
    () =>
      new Map(
        (tools ?? [])
          .filter((t) => t.type === "builtin")
          .map((t) => [t.name, t]),
      ),
    [tools],
  );
  const apiTools = (tools ?? []).filter(
    (t) => t.type === "api" || t.type === "http",
  );
  const n8nTools = (tools ?? []).filter((t) => t.type === "n8n");
  const enabledOf = (id: string) =>
    (bindings ?? []).find((b) => b.tool_id === id)?.enabled ?? true;
  const stats = new Map((summary ?? []).map((s) => [s.tool_id, s]));
  const otherEnabled = (bindings ?? []).filter(
    (b) => b.enabled && !b.managed,
  ).length;
  const connectionName = (id: string | null) =>
    id ? (connections ?? []).find((c) => c.id === id)?.name : null;
  const { locked: connectionsLocked } = useToolLimit("connections");

  if (!draft) return null;

  return (
    <div className="space-y-6">
      <SectionCard
        title="Tool calling"
        description="Let the agent call tools mid-conversation."
      >
        <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
          <div className="flex-1">
            <div className="text-sm font-medium text-text">Enable tools</div>
            <div className="text-xs text-muted">
              Runs an iteration-capped loop; every call is logged.
            </div>
          </div>
          <Switch
            aria-label="Enable tools"
            checked={draft.features.tools}
            onCheckedChange={(v) => update((d) => void (d.features.tools = v))}
          />
        </div>
      </SectionCard>

      <SectionCard
        title="Built-in tools"
        description="Ready-made capabilities you can switch on."
      >
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <ul className="space-y-2">
            {(builtins ?? []).map((b) => (
              <li
                key={b.name}
                className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3"
              >
                <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                  <Zap className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-sm text-text">{b.name}</div>
                  <div className="truncate text-xs text-muted">
                    {b.description}
                  </div>
                </div>
                <Switch
                  aria-label={`${b.name} built-in`}
                  checked={byName.has(b.name)}
                  disabled={toggleBuiltin.isPending}
                  onCheckedChange={() => toggleBuiltin.mutate(b.name)}
                />
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard
        title="API tools"
        description="Call your own or a partner's API. Log in once with a connection, then build as many tools on it as you like."
      >
        {connectionsLocked && (
          <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-2/50 p-3 text-sm">
            <Lock className="size-4 text-ai" aria-hidden />
            <span className="flex-1 text-muted">
              Saved connections are part of the Pro and Business plans. You can
              still call a full URL.
            </span>
            <Link
              href={UPGRADE_PATH}
              className="font-extrabold text-ai-text hover:underline"
            >
              Upgrade
            </Link>
          </div>
        )}
        <ul className="space-y-2" aria-label="API tools">
          {apiTools.length === 0 && (
            <li className="text-sm text-muted">No API tools yet.</li>
          )}
          {apiTools.map((t) => {
            const stat = stats.get(t.id);
            const cfg = t.config as {
              method?: string;
              path?: string;
              url?: string;
            };
            return (
              <li
                key={t.id}
                className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3"
                data-testid="api-tool"
              >
                <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                  <Globe className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-text">
                      {t.name}
                    </span>
                    {t.approval_required && (
                      <Badge variant="warn">Needs approval</Badge>
                    )}
                    {t.mode === "async" && (
                      <Badge variant="info">Background</Badge>
                    )}
                    {t.rate_limit && <Badge variant="neutral">Limited</Badge>}
                    {t.type === "http" && (
                      <Badge variant="neutral">Legacy</Badge>
                    )}
                  </div>
                  <div className="truncate text-xs text-muted">
                    {String(cfg.method ?? "GET")}{" "}
                    {String(cfg.path ?? cfg.url ?? "")}
                    {connectionName(t.connection_id)
                      ? ` · ${connectionName(t.connection_id)}`
                      : ""}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-faint">
                    {stat ? (
                      <>
                        <StatusPill status={stat.last_status} />
                        <span>
                          {stat.calls_7d} call{stat.calls_7d === 1 ? "" : "s"}{" "}
                          in 7 days
                          {stat.last_run_at
                            ? ` · last ${relativeTime(stat.last_run_at)}`
                            : ""}
                        </span>
                      </>
                    ) : (
                      "Not used yet"
                    )}
                  </div>
                </div>
                <Switch
                  aria-label={`${t.name} on for this agent`}
                  checked={enabledOf(t.id)}
                  onCheckedChange={(v) =>
                    setBinding.mutate({ toolId: t.id, enabled: v })
                  }
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditor({ tool: t, testInput: undefined })}
                >
                  <Pencil /> Edit
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Delete ${t.name}`}
                  onClick={() => removeTool.mutate(t.id)}
                >
                  <Trash2 />
                </Button>
              </li>
            );
          })}
        </ul>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setEditor({ tool: null })}
        >
          <Plus /> New API tool
        </Button>
      </SectionCard>

      <McpPanel agentId={agentId} otherEnabledTools={otherEnabled} />

      <SectionCard
        title="Automations"
        description="n8n workflows this agent can start."
      >
        <ul className="space-y-2">
          {n8nTools.length === 0 && (
            <li className="text-sm text-muted">
              No automations connected to this agent.
            </li>
          )}
          {n8nTools.map((t) => (
            <li
              key={t.id}
              className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3"
            >
              <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                <Workflow className="size-4" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-mono text-sm text-text">{t.name}</div>
                <div className="truncate text-xs text-muted">
                  {t.description}
                </div>
              </div>
              <Badge variant="neutral">
                {String((t.config as { mode?: string }).mode ?? "sync")}
              </Badge>
            </li>
          ))}
        </ul>
        <Link
          href="/automations"
          className="text-sm font-bold text-accent hover:underline"
        >
          Manage automations
        </Link>
      </SectionCard>

      <WebhooksPanel agentId={agentId} />

      <ToolLogs
        agentId={agentId}
        onReplay={(toolId, input) => {
          const tool = (tools ?? []).find((t) => t.id === toolId);
          if (tool && (tool.type === "api" || tool.type === "http")) {
            setEditor({ tool, testInput: JSON.stringify(input, null, 2) });
          }
        }}
      />

      <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-2/40 p-4 text-sm text-muted">
        <Wrench className="size-4 shrink-0 text-faint" aria-hidden />
        <span className="flex-1">
          Enable tools above, switch on what you want, then try the playground.
          The agent calls them mid-conversation.
        </span>
        <Play className="size-4 shrink-0 text-faint" aria-hidden />
      </div>

      <ApiToolDialog
        open={editor !== null}
        onOpenChange={(o) => !o && setEditor(null)}
        agentId={agentId}
        tool={editor?.tool ?? null}
        initialTestInput={editor?.testInput}
        onSaved={refresh}
      />
    </div>
  );
}

/** The list `PUT /agents/{id}/tools` takes, with one tool switched on or off. MCP tools the agent's MCP selection
 *  manages are left out: the server owns them and refuses them here. */
function withEnabled(
  bindings: ToolBinding[],
  toolId: string,
  enabled: boolean,
) {
  return bindings
    .filter((b) => !b.managed)
    .map((b) => ({
      tool_id: b.tool_id,
      enabled: b.tool_id === toolId ? enabled : b.enabled,
      overrides: b.overrides,
    }));
}
