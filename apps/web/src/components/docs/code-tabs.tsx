"use client";

import { Children, useId, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { CopyButton } from "./copy-button";
import { InsideTabsContext } from "./code-block";

/**
 * The same example in several languages: `<CodeTabs labels="cURL,JavaScript,Python">` around
 * one fenced block per label.
 *
 * `labels` is a plain string, not `labels={["a","b"]}`: next-mdx-remote strips JavaScript
 * expressions from MDX by default (`blockJS`), which would silently empty an array attribute.
 *
 * Every panel is in the page from the start and the inactive ones are only `hidden`, so the
 * examples are present for search, print and no-JavaScript readers. Which tab is selected is
 * held in component state alone — deliberately not remembered in `localStorage`; a docs page
 * has no business writing to the reader's browser.
 */
export function CodeTabs({ labels, children }: { labels: string; children: ReactNode }) {
  const panels = Children.toArray(children);
  const names = labels
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);
  // A count mismatch is an authoring slip; label the extras rather than dropping a panel.
  const tabs = panels.map((_, i) => names[i] ?? `Example ${i + 1}`);

  const [active, setActive] = useState(0);
  const base = useId();
  const panelRefs = useRef<(HTMLDivElement | null)[]>([]);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function select(i: number) {
    setActive(i);
    tabRefs.current[i]?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const last = tabs.length - 1;
    if (e.key === "ArrowRight") select(active === last ? 0 : active + 1);
    else if (e.key === "ArrowLeft") select(active === 0 ? last : active - 1);
    else if (e.key === "Home") select(0);
    else if (e.key === "End") select(last);
    else return;
    e.preventDefault();
  }

  return (
    <InsideTabsContext.Provider value>
      <div className="not-prose my-6 overflow-hidden rounded-lg border border-border bg-surface-2">
        <div className="flex items-center gap-2 border-b border-border bg-surface px-2 py-1.5">
          <div role="tablist" aria-label="Language" onKeyDown={onKeyDown} className="flex flex-1 gap-1 overflow-x-auto">
            {tabs.map((name, i) => (
              <button
                key={name}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`${base}-tab-${i}`}
                aria-selected={i === active}
                aria-controls={`${base}-panel-${i}`}
                tabIndex={i === active ? 0 : -1}
                onClick={() => setActive(i)}
                className={cn(
                  "whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50",
                  i === active ? "bg-surface-2 text-text" : "text-muted hover:text-text",
                )}
              >
                {name}
              </button>
            ))}
          </div>
          {/* One button for the group, copying whichever example is showing. */}
          <CopyButton
            label={`Copy ${tabs[active]} example`}
            resetKey={active}
            getText={() => panelRefs.current[active]?.querySelector("pre")?.textContent ?? ""}
          />
        </div>

        {panels.map((panel, i) => (
          <div
            key={tabs[i]}
            ref={(el) => {
              panelRefs.current[i] = el;
            }}
            role="tabpanel"
            id={`${base}-panel-${i}`}
            aria-labelledby={`${base}-tab-${i}`}
            hidden={i !== active}
          >
            {panel}
          </div>
        ))}
      </div>
    </InsideTabsContext.Provider>
  );
}
