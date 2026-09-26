"use client";

import { createContext, useContext, useRef } from "react";
import type { ComponentPropsWithoutRef } from "react";
import { cn } from "@/lib/utils";
import { CopyButton } from "./copy-button";

/** Set by `CodeTabs`. A block inside a tab group uses the group's single copy button and the
 *  group's own frame, so it must not draw a second of either. */
export const InsideTabsContext = createContext(false);

/**
 * A code block with a copy button.
 *
 * Wraps the `<pre>` that Shiki has already highlighted on the server; nothing is highlighted
 * or computed here. The copy button reads the block's text from the DOM at click time.
 */
export function CodeBlock({ className, children, ...props }: ComponentPropsWithoutRef<"pre">) {
  const ref = useRef<HTMLPreElement>(null);
  const inTabs = useContext(InsideTabsContext);

  return (
    <div className={cn("relative", !inTabs && "my-6")}>
      <pre
        {...props}
        ref={ref}
        className={cn(
          "overflow-x-auto p-4 text-[13px] leading-relaxed",
          // Inside a tab group the frame belongs to the group.
          inTabs ? "!my-0 bg-transparent" : "rounded-lg border border-border bg-surface-2 pr-14",
          className,
        )}
      >
        {children}
      </pre>
      {!inTabs && (
        <CopyButton
          className="absolute right-2 top-2"
          getText={() => ref.current?.textContent ?? ""}
        />
      )}
    </div>
  );
}
