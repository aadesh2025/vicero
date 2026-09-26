"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Copies whatever text `getText` returns at the moment of the click.
 *
 * Reads the text from the code block as it is rendered, so what lands on the clipboard is
 * exactly what the reader sees — in the docs, always a placeholder. It holds nothing between
 * clicks: no state beyond the "Copied" flash, nothing written to `localStorage` or
 * `sessionStorage`, nothing sent anywhere. The docs are a page that explains keys, never one
 * that handles them.
 */
export function CopyButton({
  getText,
  label = "Copy code",
  className,
  resetKey,
}: {
  getText: () => string;
  label?: string;
  className?: string;
  /** When this changes, the "Copied" confirmation clears. A tab group passes the selected tab,
   *  so switching language does not leave the button claiming the *new* example was copied. */
  resetKey?: unknown;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  const [seenKey, setSeenKey] = useState(resetKey);
  if (seenKey !== resetKey) {
    // Adjusting state during render, the documented pattern for "reset when a prop changes".
    setSeenKey(resetKey);
    setCopied(false);
  }

  async function copy() {
    const text = getText();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard access can be refused (insecure origin, permissions policy). Say nothing
      // rather than claim it worked — and never fall back to logging the text.
      return;
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1600);
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? "Copied" : label}
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-md border border-border bg-surface text-muted",
        "transition-colors hover:bg-surface-2 hover:text-text focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50",
        className,
      )}
    >
      {copied ? <Check className="size-4 text-success-text" aria-hidden /> : <Copy className="size-4" aria-hidden />}
      <span role="status" className="sr-only">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </button>
  );
}
