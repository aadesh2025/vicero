"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Field } from "@/components/builder/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { listAgents } from "@/lib/api/agents";
import { listTools } from "@/lib/api/tools";
import { listWorkflows } from "@/lib/api/workflows";
import { useSession } from "@/lib/store/session";
import { NODE_TYPE_BY_ID } from "./node-types";
import type { WorkflowNodeType } from "@/lib/api/types";

const TRANSFORM_OPS = ["uppercase", "lowercase", "trim", "to_number", "length", "concat"] as const;

export interface SelectedNode {
  id: string;
  nodeType: WorkflowNodeType;
  label: string;
  config: Record<string, unknown>;
}

/** The right-hand config editor for whichever node is selected. One component covering all 13
 * node types (a switch on `nodeType`) rather than 13 files — most types need 1-3 fields, and
 * splitting them out would scatter very little logic across a lot of boilerplate. */
export function PropertiesPanel({
  node,
  agentId,
  currentWorkflowId,
  onChange,
  onLabelChange,
  onClose,
  onDelete,
}: {
  node: SelectedNode;
  agentId: string;
  currentWorkflowId: string;
  onChange: (config: Record<string, unknown>) => void;
  onLabelChange: (label: string) => void;
  onClose: () => void;
  onDelete: () => void;
}) {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const meta = NODE_TYPE_BY_ID[node.nodeType];
  const c = node.config ?? {};
  const set = (key: string, value: unknown) => onChange({ ...c, [key]: value });

  const { data: tools } = useQuery({
    queryKey: ["tools", agentId, activeOrgId],
    queryFn: () => listTools(agentId),
    enabled: Boolean(activeOrgId) && node.nodeType === "tool",
  });
  const { data: agents } = useQuery({
    queryKey: ["agents-list", activeOrgId],
    queryFn: () => listAgents(),
    enabled: Boolean(activeOrgId) && node.nodeType === "agent",
  });
  const { data: workflows } = useQuery({
    queryKey: ["workflows", agentId, activeOrgId],
    queryFn: () => listWorkflows(agentId),
    enabled: Boolean(activeOrgId) && node.nodeType === "sub_agent",
  });

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-l border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-text">
          {meta && <meta.icon className="size-4" />}
          {meta?.label ?? node.nodeType}
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        <Field label="Node label" description="Shown on the canvas — for your own reference only.">
          <Input value={node.label} onChange={(e) => onLabelChange(e.target.value)} placeholder={meta?.label} />
        </Field>

        {node.nodeType === "message" && (
          <Field label="Content" description="{{variable}} placeholders are rendered at run time.">
            <Textarea
              rows={5}
              value={(c.content as string) ?? ""}
              onChange={(e) => set("content", e.target.value)}
              placeholder="Hi {{user_name}}, ..."
            />
          </Field>
        )}

        {node.nodeType === "condition" && (
          <Field label="Expression" description="var.path OP literal — e.g. intent == 'booking'.">
            <Input
              value={(c.expression as string) ?? ""}
              onChange={(e) => set("expression", e.target.value)}
              placeholder="lead.score > 50"
            />
          </Field>
        )}

        {node.nodeType === "switch" && (
          <>
            <Field label="Variable" description="The value to match against each case.">
              <Input value={(c.variable as string) ?? ""} onChange={(e) => set("variable", e.target.value)} />
            </Field>
            <CasesEditor
              cases={(c.cases as Record<string, string>) ?? {}}
              onChange={(cases) => set("cases", cases)}
            />
            <Field label="Default branch" description="Used when nothing matches.">
              <Input
                value={(c.default_branch as string) ?? "default"}
                onChange={(e) => set("default_branch", e.target.value)}
              />
            </Field>
          </>
        )}

        {node.nodeType === "set_variable" && (
          <>
            <Field label="Variable name">
              <Input value={(c.key as string) ?? ""} onChange={(e) => set("key", e.target.value)} />
            </Field>
            <Field label="Value" description="A literal, or {{another_variable}}.">
              <Input value={(c.value as string) ?? ""} onChange={(e) => set("value", e.target.value)} />
            </Field>
          </>
        )}

        {node.nodeType === "transform" && (
          <>
            <Field label="Operation">
              <Select value={(c.operation as string) ?? "trim"} onValueChange={(v) => set("operation", v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TRANSFORM_OPS.map((op) => (
                    <SelectItem key={op} value={op}>
                      {op}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Variable">
              <Input value={(c.variable as string) ?? ""} onChange={(e) => set("variable", e.target.value)} />
            </Field>
            <Field label="Target variable" description="Defaults to the source variable.">
              <Input
                value={(c.target_variable as string) ?? ""}
                onChange={(e) => set("target_variable", e.target.value)}
              />
            </Field>
            {c.operation === "concat" && (
              <>
                <Field label="Concat with (variable)">
                  <Input
                    value={(c.with_variable as string) ?? ""}
                    onChange={(e) => set("with_variable", e.target.value)}
                  />
                </Field>
                <Field label="…or a fixed literal" description="Used only if the field above is empty.">
                  <Input
                    value={(c.with_literal as string) ?? ""}
                    onChange={(e) => set("with_literal", e.target.value)}
                  />
                </Field>
              </>
            )}
          </>
        )}

        {node.nodeType === "approval" && (
          <Field label="Approval message" description="What the operator sees in the Inbox.">
            <Textarea
              rows={4}
              value={(c.message as string) ?? ""}
              onChange={(e) => set("message", e.target.value)}
              placeholder="Approve this refund?"
            />
          </Field>
        )}

        {node.nodeType === "tool" && (
          <>
            <Field label="Tool">
              <Select value={(c.tool_name as string) ?? ""} onValueChange={(v) => set("tool_name", v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a tool…" />
                </SelectTrigger>
                <SelectContent>
                  {(tools ?? []).map((t) => (
                    <SelectItem key={t.id} value={t.name}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {/* Matches the existing agentic-runtime tool config UI's own pattern (the "Test
                tool" dialog on the Tools tab) — a raw JSON textarea, not a dynamic field-per-
                argument form, since a tool's argument shape varies per tool and isn't known
                here. Keyed by node id so switching nodes resets the local edit buffer instead
                of leaking one node's in-progress (possibly invalid) JSON into another's. */}
            <ToolArgumentsField
              key={node.id}
              value={(c.arguments as Record<string, unknown>) ?? {}}
              onChange={(args) => set("arguments", args)}
            />
            <Field label="Result variable" description="Where the sanitized result is stored.">
              <Input
                value={(c.result_variable as string) ?? ""}
                onChange={(e) => set("result_variable", e.target.value)}
              />
            </Field>
          </>
        )}

        {node.nodeType === "agent" && (
          <>
            <Field label="Agent">
              <Select value={(c.agent_id as string) ?? ""} onValueChange={(v) => set("agent_id", v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select an agent…" />
                </SelectTrigger>
                <SelectContent>
                  {(agents ?? []).map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Message" description="Rendered with {{variables}} before it's sent.">
              <Textarea rows={3} value={(c.message as string) ?? ""} onChange={(e) => set("message", e.target.value)} />
            </Field>
            <Field label="Result variable">
              <Input
                value={(c.result_variable as string) ?? ""}
                onChange={(e) => set("result_variable", e.target.value)}
              />
            </Field>
          </>
        )}

        {node.nodeType === "sub_agent" && (
          <>
            <Field label="Workflow">
              <Select value={(c.workflow_id as string) ?? ""} onValueChange={(v) => set("workflow_id", v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a workflow…" />
                </SelectTrigger>
                <SelectContent>
                  {(workflows ?? [])
                    .filter((w) => w.id !== currentWorkflowId)
                    .map((w) => (
                      <SelectItem key={w.id} value={w.id}>
                        {w.name}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Result variable">
              <Input
                value={(c.result_variable as string) ?? ""}
                onChange={(e) => set("result_variable", e.target.value)}
              />
            </Field>
          </>
        )}

        {node.nodeType === "loop" && (
          <>
            <Field label="List variable">
              <Input
                value={(c.list_variable as string) ?? ""}
                onChange={(e) => set("list_variable", e.target.value)}
              />
            </Field>
            <Field label="Item variable" description="Set to the current item on each iteration.">
              <Input
                value={(c.item_variable as string) ?? ""}
                onChange={(e) => set("item_variable", e.target.value)}
              />
            </Field>
            <Field label="Index variable" description="Optional.">
              <Input
                value={(c.index_variable as string) ?? ""}
                onChange={(e) => set("index_variable", e.target.value)}
              />
            </Field>
            <Field label="Max iterations" description="Optional local cap, on top of the run's step budget.">
              <Input
                type="number"
                value={(c.max_iterations as number) ?? ""}
                onChange={(e) => set("max_iterations", e.target.value ? Number(e.target.value) : undefined)}
              />
            </Field>
          </>
        )}

        {node.nodeType === "delay" && (
          <Field label="Duration (seconds)" description="Shape only — delay nodes aren't executable yet.">
            <Input
              type="number"
              value={(c.duration_seconds as number) ?? 60}
              onChange={(e) => set("duration_seconds", Number(e.target.value))}
            />
          </Field>
        )}

        {(node.nodeType === "start" || node.nodeType === "end") && (
          <p className="text-sm text-muted">This node has no configuration.</p>
        )}
      </div>

      {node.nodeType !== "start" && (
        <div className="border-t border-border p-4">
          <Button variant="destructive" size="sm" className="w-full" onClick={onDelete}>
            Delete node
          </Button>
        </div>
      )}
    </aside>
  );
}

function CasesEditor({
  cases,
  onChange,
}: {
  cases: Record<string, string>;
  onChange: (cases: Record<string, string>) => void;
}) {
  const entries = Object.entries(cases);
  const update = (idx: number, key: string, value: string) => {
    const next = [...entries];
    next[idx] = [key, value];
    onChange(Object.fromEntries(next));
  };
  const remove = (idx: number) => {
    onChange(Object.fromEntries(entries.filter((_, i) => i !== idx)));
  };
  return (
    <Field label="Cases" description="Literal value → branch name.">
      <div className="space-y-2">
        {entries.map(([key, value], idx) => (
          <div key={idx} className="flex items-center gap-1.5">
            <Input
              className="h-8 text-xs"
              value={key}
              placeholder="value"
              onChange={(e) => update(idx, e.target.value, value)}
            />
            <span className="text-faint">→</span>
            <Input
              className="h-8 text-xs"
              value={value}
              placeholder="branch"
              onChange={(e) => update(idx, key, e.target.value)}
            />
            <Button variant="ghost" size="icon-sm" onClick={() => remove(idx)} aria-label="Remove case">
              <X className="size-3.5" />
            </Button>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={() => onChange({ ...cases, "": "" })}>
          + Add case
        </Button>
      </div>
    </Field>
  );
}

function ToolArgumentsField({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (args: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState<string | null>(null);

  return (
    <Field
      label="Arguments (JSON)"
      description="String values are rendered with {{variables}} at run time, same as a Message node."
    >
      <Textarea
        rows={5}
        className="font-mono text-xs"
        value={text}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          try {
            const parsed: unknown = JSON.parse(next || "{}");
            if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
              throw new Error("must be a JSON object");
            }
            setError(null);
            onChange(parsed as Record<string, unknown>);
          } catch {
            // Not propagated to the graph until it's valid again — the last-known-good
            // arguments stay in effect rather than saving something unparseable.
            setError("Invalid JSON — not saved until fixed.");
          }
        }}
        placeholder={'{"query": "{{search_term}}"}'}
      />
      {error && <p className="text-xs text-error-text">{error}</p>}
    </Field>
  );
}
