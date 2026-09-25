import Link from "next/link";
import type { ComponentPropsWithoutRef } from "react";
import { cn } from "@/lib/utils";
import { Callout } from "./callout";

/**
 * How MDX elements render inside the docs.
 *
 * Most of the styling is `prose-botforge` in the Tailwind config; the overrides here are
 * for the handful of elements the typography plugin gets wrong for this content:
 *
 * - **links** go through `next/link` when internal, so docs navigation is client-side,
 *   and carry `rel="noreferrer"` when not.
 * - **`pre`** is the Shiki container. The prose plugin's own `pre` styling is cleared in
 *   the Tailwind config, so the border and scroll behaviour are applied here instead.
 * - **tables** get a wrapper that scrolls, because an endpoint table is wider than a
 *   phone and the page must not scroll sideways as a whole.
 */
export const docsComponents = {
  a: ({ href = "", className, ...props }: ComponentPropsWithoutRef<"a">) => {
    // `rehype-autolink-headings` wraps every heading in an anchor. It arrives here like any
    // other link, and styling it as one turned every heading on the site into underlined
    // blue text. It is navigation furniture, not prose — so it keeps the class rehype gave
    // it (styled in globals.css) and nothing else.
    if (className?.includes("heading-anchor")) {
      return <a {...props} href={href} className={className} />;
    }

    const link = "font-medium underline decoration-border-strong underline-offset-2 hover:decoration-accent";
    if (/^https?:\/\//.test(href)) {
      return <a {...props} href={href} target="_blank" rel="noreferrer noopener" className={cn(link, className)} />;
    }
    return <Link {...props} href={href} className={cn(link, className)} />;
  },

  pre: ({ className, ...props }: ComponentPropsWithoutRef<"pre">) => (
    <pre
      {...props}
      className={cn(
        "overflow-x-auto rounded-lg border border-border bg-surface-2 p-4 text-[13px] leading-relaxed",
        className,
      )}
    />
  ),

  code: ({ className, ...props }: ComponentPropsWithoutRef<"code">) => (
    <code
      {...props}
      // Shiki adds its own class to block code; only bare inline code gets the chip.
      className={cn(
        className ?? "rounded border border-border bg-surface-2 px-1 py-0.5 font-mono text-[0.875em]",
      )}
    />
  ),

  table: (props: ComponentPropsWithoutRef<"table">) => (
    <div className="my-6 overflow-x-auto rounded-lg border border-border">
      <table {...props} className="w-full border-collapse text-sm" />
    </div>
  ),
  th: ({ className, ...props }: ComponentPropsWithoutRef<"th">) => (
    <th
      {...props}
      className={cn(
        "border-b border-border bg-surface-2 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-faint",
        className,
      )}
    />
  ),
  td: ({ className, ...props }: ComponentPropsWithoutRef<"td">) => (
    <td {...props} className={cn("border-b border-border px-4 py-2.5 align-top", className)} />
  ),

  hr: () => <hr className="my-10 border-border" />,

  // Available to any page as <Callout type="warn">…</Callout>.
  Callout,
};
