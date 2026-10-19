"use client";

import { useId, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { applySuggestion, suggest, templateToken } from "./templates";

/** A text field that offers `{{parameter}}` and `{{vars.name}}` after you type `{{` (docs/28 T5.3).
 *
 * It is a plain input underneath: the suggestions only ever edit the text, so a person who ignores them,
 * or whose browser blocks the popup, loses nothing. */
export function TemplateInput({
  value,
  onChange,
  candidates,
  multiline = false,
  rows = 4,
  placeholder,
  label,
  mono = true,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  /** Names to offer: declared parameters first, then `vars.` names. */
  candidates: string[];
  multiline?: boolean;
  rows?: number;
  placeholder?: string;
  /** Accessible name; the field has no visible label of its own. */
  label: string;
  mono?: boolean;
  className?: string;
}) {
  const listId = useId();
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);

  const token = templateToken(value, caret);
  const options = token && !dismissed ? suggest(candidates, token.query) : [];
  const open = options.length > 0;

  const track = () => {
    const el = ref.current;
    if (el) setCaret(el.selectionStart ?? value.length);
  };

  const accept = (name: string) => {
    if (!token) return;
    const next = applySuggestion(value, caret, token, name);
    onChange(next.text);
    setDismissed(false);
    setActive(0);
    requestAnimationFrame(() => {
      const el = ref.current;
      if (el) {
        el.focus();
        el.setSelectionRange(next.caret, next.caret);
        setCaret(next.caret);
      }
    });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + options.length) % options.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      accept(options[Math.min(active, options.length - 1)]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setDismissed(true);
    }
  };

  const shared = {
    ref,
    value,
    placeholder,
    "aria-label": label,
    "aria-autocomplete": "list" as const,
    "aria-expanded": open,
    "aria-controls": open ? listId : undefined,
    onChange: (
      e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => {
      onChange(e.target.value);
      setCaret(e.target.selectionStart ?? e.target.value.length);
      setDismissed(false);
      setActive(0);
    },
    onKeyDown,
    onKeyUp: track,
    onClick: track,
    onSelect: track,
    className: cn(mono && "font-mono text-[13px]", className),
  };

  return (
    <div className="relative">
      {multiline ? <Textarea {...shared} rows={rows} /> : <Input {...shared} />}
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={`${label} suggestions`}
          className="absolute left-0 top-full z-30 mt-1 max-h-48 w-64 max-w-full overflow-y-auto rounded-lg border border-border-strong bg-surface p-1 shadow-pop dark:bg-surface-2"
        >
          {options.map((name, i) => (
            <li
              key={name}
              role="option"
              aria-selected={i === active}
              // mouse down, not click: a click would blur the field first and close the list before it registers
              onMouseDown={(e) => {
                e.preventDefault();
                accept(name);
              }}
              onMouseEnter={() => setActive(i)}
              className={cn(
                "cursor-pointer rounded-md px-2 py-1.5 font-mono text-xs text-text",
                i === active ? "bg-surface-3" : "hover:bg-surface-3",
              )}
            >
              {`{{${name}}}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
