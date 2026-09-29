"use client";

import { NODE_TYPES } from "./node-types";

/** Drag a node type onto the canvas to add it. `dataTransfer` carries just the type string;
 * the canvas's `onDrop` looks up the rest (icon, default config) from `NODE_TYPE_BY_ID`. */
export function NodePalette() {
  const addable = NODE_TYPES.filter((n) => n.addable);
  return (
    <aside className="w-56 shrink-0 overflow-y-auto border-r border-border bg-surface p-3">
      <p className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-faint">Nodes</p>
      <div className="space-y-1.5">
        {addable.map((n) => (
          <div
            key={n.type}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData("application/vicero-node-type", n.type);
              e.dataTransfer.effectAllowed = "move";
            }}
            className="flex cursor-grab items-start gap-2 rounded-md border border-border bg-surface-2 p-2 text-xs transition-colors hover:border-accent/40 hover:bg-surface-3 active:cursor-grabbing"
            title={n.description}
          >
            <n.icon className="mt-0.5 size-3.5 shrink-0 text-muted" />
            <div className="min-w-0">
              <p className="font-medium text-text">{n.label}</p>
              <p className="mt-0.5 line-clamp-2 text-[11px] text-faint">{n.description}</p>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
