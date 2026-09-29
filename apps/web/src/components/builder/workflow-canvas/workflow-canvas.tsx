"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, Rocket, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCan } from "@/lib/rbac";
import { useSession } from "@/lib/store/session";
import {
  createWorkflowVersion,
  getWorkflowRun,
  listWorkflowRunSteps,
  listWorkflowRuns,
  listWorkflowVersions,
  publishWorkflowVersion,
  resumeWorkflowRun,
  testRunWorkflow,
} from "@/lib/api/workflows";
import type { ApiWorkflow, ApiWorkflowStep, WorkflowGraph, WorkflowNodeType } from "@/lib/api/types";
import { relativeTime } from "@/lib/utils";
import { NodePalette } from "./node-palette";
import { PropertiesPanel } from "./properties-panel";
import { WorkflowNode, type WorkflowNodeData } from "./workflow-node";
import { NODE_TYPE_BY_ID } from "./node-types";
import { flowToGraph, graphToFlow, newNodeId } from "./graph-convert";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const nodeTypes = { workflowNode: WorkflowNode };

// A brand-new workflow starts as a small but genuinely VALID graph — `validate_graph` requires
// exactly one start and at least one end node, so a bare Start alone would fail the very first
// save. Pre-wiring them means a freshly created workflow is saveable (and test-runnable)
// immediately, not just once an author has built something real.
const EMPTY_GRAPH: WorkflowGraph = {
  nodes: [
    { id: "start", type: "start", position: { x: 120, y: 100 } },
    { id: "end", type: "end", position: { x: 120, y: 260 } },
  ],
  edges: [{ source: "start", target: "end" }],
};

const RUN_TERMINAL = new Set(["completed", "failed", "cancelled", "budget_exceeded"]);

function CanvasInner({ workflow, agentId }: { workflow: ApiWorkflow; agentId: string }) {
  const qc = useQueryClient();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canWrite = useCan("workflows:write");
  const canPublish = useCan("workflows:publish");
  const { screenToFlowPosition } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);

  const { data: versions } = useQuery({
    queryKey: ["workflow-versions", workflow.id, activeOrgId],
    queryFn: () => listWorkflowVersions(workflow.id),
    enabled: Boolean(activeOrgId),
  });
  const latest = useMemo(
    () => (versions && versions.length > 0 ? versions.reduce((a, b) => (b.version > a.version ? b : a)) : null),
    [versions],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(graphToFlow(EMPTY_GRAPH).nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(graphToFlow(EMPTY_GRAPH).edges);
  const [dirty, setDirty] = useState(false);
  const [savedLabel, setSavedLabel] = useState<string | null>(null);
  const loadedVersionId = useRef<string | null>(null);

  // Seed the canvas once per version (not `versions` array identity, which can change
  // reference on every refetch without the actual latest version changing).
  useEffect(() => {
    const key = latest?.id ?? null;
    if (loadedVersionId.current === key) return;
    loadedVersionId.current = key;
    const flow = graphToFlow(latest?.graph ?? EMPTY_GRAPH);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    setDirty(false);
  }, [latest, setNodes, setEdges]);

  const [selected, setSelected] = useState<string | null>(null);

  const onConnect = useCallback(
    (conn: Connection) => {
      setEdges((eds) => addEdge({ ...conn, type: "smoothstep", label: conn.sourceHandle ?? undefined }, eds));
      setDirty(true);
    },
    [setEdges],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData("application/vicero-node-type") as WorkflowNodeType;
      const meta = NODE_TYPE_BY_ID[type];
      if (!meta) return;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const id = newNodeId(type);
      setNodes((nds) => [
        ...nds,
        {
          id,
          type: "workflowNode",
          position,
          data: { nodeType: meta.type, config: { ...meta.defaultConfig } } satisfies WorkflowNodeData,
        },
      ]);
      setDirty(true);
    },
    [screenToFlowPosition, setNodes],
  );

  const selectedNode = nodes.find((n) => n.id === selected);
  const selectedData = selectedNode?.data as WorkflowNodeData | undefined;

  const updateSelectedConfig = (config: Record<string, unknown>) => {
    if (!selected) return;
    setNodes((nds) => nds.map((n) => (n.id === selected ? { ...n, data: { ...n.data, config } } : n)));
    setDirty(true);
  };
  const updateSelectedLabel = (label: string) => {
    if (!selected) return;
    setNodes((nds) => nds.map((n) => (n.id === selected ? { ...n, data: { ...n.data, label } } : n)));
    setDirty(true);
  };
  const deleteSelected = () => {
    if (!selected) return;
    setNodes((nds) => nds.filter((n) => n.id !== selected));
    setEdges((eds) => eds.filter((e) => e.source !== selected && e.target !== selected));
    setSelected(null);
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => createWorkflowVersion(workflow.id, flowToGraph(nodes, edges)),
    onSuccess: async (v) => {
      loadedVersionId.current = v.id;
      setDirty(false);
      setSavedLabel(`Saved as draft v${v.version}`);
      await qc.invalidateQueries({ queryKey: ["workflow-versions", workflow.id, activeOrgId] });
    },
  });

  const publishMutation = useMutation({
    mutationFn: () => {
      if (!latest) throw new Error("Save a draft before publishing.");
      return publishWorkflowVersion(workflow.id, latest.version);
    },
    onSuccess: async () => {
      // Clears the stale "Saved as draft vN" label so the status line falls back to deriving
      // from `latest.status` — otherwise a save immediately followed by a publish would keep
      // showing "draft" even though the version is now published.
      setSavedLabel(null);
      await qc.invalidateQueries({ queryKey: ["workflow-versions", workflow.id, activeOrgId] });
      await qc.invalidateQueries({ queryKey: ["workflows", agentId, activeOrgId] });
    },
  });

  // ── Test run (docs/17 Phase 2 item 6): enqueue, then poll the run's own status and its
  // step list, overlaying each step's outcome onto the matching canvas node.
  const [runId, setRunId] = useState<string | null>(null);
  const { data: run } = useQuery({
    queryKey: ["workflow-run", runId],
    queryFn: () => getWorkflowRun(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (q) => (q.state.data && RUN_TERMINAL.has(q.state.data.status) ? false : 1000),
  });
  const { data: steps } = useQuery({
    queryKey: ["workflow-run-steps", runId],
    queryFn: () => listWorkflowRunSteps(runId as string),
    enabled: Boolean(runId),
    refetchInterval: run && RUN_TERMINAL.has(run.status) ? false : 1000,
  });

  // ── Run history (docs/17 Phase 2 gap-closure item 3): reopening a PAST run reuses the exact
  // same `runId`/`run`/`steps` state a live test run already drives — a terminal run's own
  // `refetchInterval` above already stops polling on the very first fetch, so no separate
  // "historical" code path is needed for the overlay itself.
  const { data: runHistory } = useQuery({
    queryKey: ["workflow-runs", workflow.id, activeOrgId],
    queryFn: () => listWorkflowRuns(workflow.id),
    enabled: Boolean(activeOrgId),
  });

  const testRunMutation = useMutation({
    mutationFn: () => testRunWorkflow(workflow.id, {}),
    onSuccess: async (r) => {
      setRunId(r.id);
      await qc.invalidateQueries({ queryKey: ["workflow-runs", workflow.id, activeOrgId] });
    },
  });
  const decideMutation = useMutation({
    mutationFn: (decision: "approved" | "rejected") => resumeWorkflowRun(runId as string, decision),
  });

  const stepByNode = useMemo(() => {
    const map = new Map<string, ApiWorkflowStep>();
    for (const s of steps ?? []) map.set(s.node_id, s); // last write wins — latest visit
    return map;
  }, [steps]);

  const overlaidNodes = useMemo(
    () =>
      nodes.map((n) => {
        const step = stepByNode.get(n.id);
        const isCurrent =
          (run?.status === "paused_approval" || run?.status === "paused_delay") &&
          run.current_node_id === n.id;
        const data = n.data as WorkflowNodeData;
        const runStatus: WorkflowNodeData["runStatus"] = isCurrent
          ? "awaiting_approval"
          : step?.status === "completed"
            ? "completed"
            : step?.status === "failed"
              ? "failed"
              : null;
        return { ...n, data: { ...data, runStatus } satisfies WorkflowNodeData };
      }),
    [nodes, stepByNode, run],
  );

  return (
    <div className="flex h-[75vh] min-h-[560px] overflow-hidden rounded-lg border border-border">
      {canWrite && <NodePalette />}

      <div className="relative flex-1" ref={wrapperRef}>
        <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded-lg border border-border bg-surface/95 p-1.5 shadow-pop backdrop-blur">
          {canWrite && (
            <Button
              size="sm"
              variant="outline"
              disabled={!dirty || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              {saveMutation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Save className="size-3.5" />
              )}
              Save draft
            </Button>
          )}
          {canWrite && (
            <Button
              size="sm"
              variant="outline"
              disabled={testRunMutation.isPending || dirty}
              title={dirty ? "Save your changes before testing" : undefined}
              onClick={() => testRunMutation.mutate()}
            >
              {testRunMutation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Play className="size-3.5" />
              )}
              Test run
            </Button>
          )}
          {canPublish && (
            <Button
              size="sm"
              variant="primary"
              disabled={!latest || latest.status === "published" || publishMutation.isPending || dirty}
              onClick={() => publishMutation.mutate()}
            >
              {publishMutation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Rocket className="size-3.5" />
              )}
              Publish
            </Button>
          )}
          <span className="px-1 text-xs text-muted">
            {dirty
              ? "Unsaved changes"
              : (savedLabel ??
                (latest ? `v${latest.version} · ${latest.status}` : "No versions yet — drop a node to start"))}
          </span>
          {runHistory && runHistory.length > 0 && (
            <Select value={runId ?? undefined} onValueChange={(v) => setRunId(v)}>
              <SelectTrigger className="h-8 w-[190px] text-xs">
                <SelectValue placeholder="View a past run…" />
              </SelectTrigger>
              <SelectContent>
                {runHistory.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {r.status} · {relativeTime(r.started_at)}
                    {r.is_test ? " · test" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        {run && (
          <div className="absolute right-3 top-3 z-10 flex items-center gap-2 rounded-lg border border-border bg-surface/95 p-2.5 text-xs shadow-pop backdrop-blur">
            <span className="font-medium text-text">
              Run: {run.status}
              {run.is_test ? " (test)" : ""}
            </span>
            {run.error && <span className="max-w-[220px] truncate text-error-text">{run.error}</span>}
            {run.status === "paused_approval" && (
              <div className="flex gap-1.5">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={decideMutation.isPending}
                  onClick={() => decideMutation.mutate("approved")}
                >
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={decideMutation.isPending}
                  onClick={() => decideMutation.mutate("rejected")}
                >
                  Reject
                </Button>
              </div>
            )}
          </div>
        )}

        <ReactFlow
          nodes={overlaidNodes}
          edges={edges}
          onNodesChange={(changes) => {
            onNodesChange(changes);
            if (changes.some((c) => c.type !== "select")) setDirty(true);
          }}
          onEdgesChange={(changes) => {
            onEdgesChange(changes);
            setDirty(true);
          }}
          onConnect={onConnect}
          onDrop={onDrop}
          onDragOver={(e) => e.preventDefault()}
          onNodeClick={(_e, node) => setSelected(node.id)}
          onPaneClick={() => setSelected(null)}
          nodeTypes={nodeTypes}
          nodesDraggable={canWrite}
          nodesConnectable={canWrite}
          elementsSelectable={canWrite}
          fitView
        >
          <Background gap={16} color="rgb(var(--chart-grid))" />
          <Controls />
          <MiniMap pannable zoomable className="!bg-surface" />
        </ReactFlow>
      </div>

      {selectedNode && selectedData && canWrite && (
        <PropertiesPanel
          node={{
            id: selectedNode.id,
            nodeType: selectedData.nodeType,
            label: selectedData.label ?? "",
            config: selectedData.config,
          }}
          agentId={agentId}
          currentWorkflowId={workflow.id}
          onChange={updateSelectedConfig}
          onLabelChange={updateSelectedLabel}
          onClose={() => setSelected(null)}
          onDelete={deleteSelected}
        />
      )}
    </div>
  );
}

export function WorkflowCanvas(props: { workflow: ApiWorkflow; agentId: string }) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
