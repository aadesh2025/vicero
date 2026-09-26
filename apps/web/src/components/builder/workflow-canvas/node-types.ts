import {
  Bot,
  Clock,
  GitBranch,
  MessageSquare,
  Play,
  Repeat,
  SplitSquareHorizontal,
  Square,
  UserCheck,
  Variable,
  Wand2,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { WorkflowNodeType } from "@/lib/api/types";

export interface NodeTypeMeta {
  type: WorkflowNodeType;
  label: string;
  description: string;
  icon: LucideIcon;
  /** Tailwind color token shared by the palette entry and the canvas node's accent. */
  color: string;
  /** Config seeded onto a freshly-dropped node — enough to be valid-looking, not necessarily
   * enough to run (an author still has to fill in a tool name, an agent id, etc.). */
  defaultConfig: Record<string, unknown>;
  /** false for start/end, which the canvas seeds once and the palette hides — a graph with
   * two start nodes fails `validate_graph` server-side, so the UI shouldn't offer a second. */
  addable: boolean;
}

export const NODE_TYPES: NodeTypeMeta[] = [
  {
    type: "start",
    label: "Start",
    description: "Where every run begins.",
    icon: Play,
    color: "text-accent border-accent/40 bg-accent/10",
    defaultConfig: {},
    addable: false,
  },
  {
    type: "end",
    label: "End",
    description: "A run finishes here.",
    icon: Square,
    color: "text-muted border-border bg-surface-2",
    defaultConfig: {},
    addable: true,
  },
  {
    type: "message",
    label: "Message",
    description: "Render text, {{variables}} included.",
    icon: MessageSquare,
    color: "text-accent border-accent/40 bg-accent/10",
    defaultConfig: { content: "" },
    addable: true,
  },
  {
    type: "condition",
    label: "Condition",
    description: "Two-way branch: var OP literal.",
    icon: GitBranch,
    color: "text-warn-text border-warn/40 bg-warn/10",
    defaultConfig: { expression: "" },
    addable: true,
  },
  {
    type: "switch",
    label: "Switch",
    description: "N-way branch on a variable's value.",
    icon: SplitSquareHorizontal,
    color: "text-warn-text border-warn/40 bg-warn/10",
    defaultConfig: { variable: "", cases: {}, default_branch: "default" },
    addable: true,
  },
  {
    type: "set_variable",
    label: "Set variable",
    description: "Write a value into the run's variables.",
    icon: Variable,
    color: "text-success-text border-success/40 bg-success/10",
    defaultConfig: { key: "", value: "" },
    addable: true,
  },
  {
    type: "transform",
    label: "Transform",
    description: "A whitelisted string/number operation.",
    icon: Wand2,
    color: "text-success-text border-success/40 bg-success/10",
    defaultConfig: { operation: "trim", variable: "" },
    addable: true,
  },
  {
    type: "approval",
    label: "Approval",
    description: "Pause for a human — surfaces in the Inbox.",
    icon: UserCheck,
    color: "text-error-text border-error/40 bg-error/10",
    defaultConfig: { message: "" },
    addable: true,
  },
  {
    type: "tool",
    label: "Tool",
    description: "Call one of this agent's configured tools.",
    icon: Wrench,
    color: "text-accent border-accent/40 bg-accent/10",
    defaultConfig: { tool_name: "", arguments: {}, result_variable: "" },
    addable: true,
  },
  {
    type: "agent",
    label: "Agent",
    description: "Run an existing published agent as a step.",
    icon: Bot,
    color: "text-accent border-accent/40 bg-accent/10",
    defaultConfig: { agent_id: "", message: "", result_variable: "" },
    addable: true,
  },
  {
    type: "sub_agent",
    label: "Sub-workflow",
    description: "Invoke another published workflow.",
    icon: Workflow,
    color: "text-accent border-accent/40 bg-accent/10",
    defaultConfig: { workflow_id: "", result_variable: "" },
    addable: true,
  },
  {
    type: "loop",
    label: "Loop",
    description: "Iterate a list variable via a graph cycle.",
    icon: Repeat,
    color: "text-warn-text border-warn/40 bg-warn/10",
    defaultConfig: { list_variable: "", item_variable: "item" },
    addable: true,
  },
  {
    type: "delay",
    label: "Delay",
    description: "Wait — shape only; not executable yet.",
    icon: Clock,
    color: "text-muted border-border bg-surface-2",
    defaultConfig: { duration_seconds: 60 },
    addable: true,
  },
];

export const NODE_TYPE_BY_ID: Record<WorkflowNodeType, NodeTypeMeta> = Object.fromEntries(
  NODE_TYPES.map((n) => [n.type, n]),
) as Record<WorkflowNodeType, NodeTypeMeta>;

/** Branch-style nodes get labeled output handles instead of one plain outgoing edge. */
export function branchLabelsFor(type: WorkflowNodeType, config: Record<string, unknown>): string[] {
  if (type === "condition") return ["true", "false"];
  if (type === "approval") return ["approved", "rejected"];
  if (type === "loop") return ["body", "done"];
  if (type === "switch") {
    const cases = (config.cases as Record<string, string> | undefined) ?? {};
    const labels = Array.from(new Set(Object.values(cases)));
    labels.push((config.default_branch as string) || "default");
    return Array.from(new Set(labels));
  }
  return [];
}
