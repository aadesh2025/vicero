"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";

export function ChipInput({
  values,
  onChange,
  placeholder,
  variant = "default",
}: {
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  variant?: "default" | "accent";
}) {
  const [text, setText] = useState("");

  const add = () => {
    const v = text.trim();
    if (!v || values.includes(v)) return;
    onChange([...values, v]);
    setText("");
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {values.map((v) => (
          <span
            key={v}
            className={cn(
              "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs",
              variant === "accent"
                ? "border-accent/30 bg-accent/10 text-accent"
                : "border-border bg-surface-2 text-muted",
            )}
          >
            {v}
            <button
              type="button"
              onClick={() => onChange(values.filter((x) => x !== v))}
              className="text-faint transition-colors hover:text-error-text"
              aria-label={`Remove ${v}`}
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
        {values.length === 0 && <span className="text-xs text-faint">None yet</span>}
      </div>
      <div className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder={placeholder}
          className="h-8 flex-1 rounded-md border border-border bg-surface-2 px-2.5 text-sm text-text placeholder:text-faint focus-visible:border-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
        />
        <button
          type="button"
          onClick={add}
          className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-muted transition-colors hover:border-border-strong hover:text-text"
          aria-label="Add"
        >
          <Plus className="size-4" />
        </button>
      </div>
    </div>
  );
}
