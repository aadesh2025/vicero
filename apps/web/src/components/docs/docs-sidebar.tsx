"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

export interface SidebarGroup {
  name: string;
  pages: { title: string; href: string }[];
}

/**
 * Grouped navigation for the docs.
 *
 * Takes plain serializable data rather than reading content itself: the same component
 * renders the public tree (passed down from a server component) and the staff-only tree
 * (fetched through the gated route handler), and neither can leak into the other's bundle.
 *
 * Small text sits directly on the grey page background, where `--faint` is 4.39:1 — just under
 * the AA line that it clears inside a white card (see globals.css). So this uses `muted`.
 *
 * Styling matches `SettingsNav` — active `bg-surface-2 text-text`, idle `text-muted` — so
 * the docs read as part of the product rather than a bolted-on site.
 */
export function DocsSidebar({ groups, basePath }: { groups: SidebarGroup[]; basePath: string }) {
  const pathname = usePathname();

  return (
    <nav aria-label="Documentation" className="flex flex-col gap-6 text-sm">
      {groups.map((group) => (
        <div key={group.name}>
          <h2 className="mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted">
            {group.name}
          </h2>
          <ul className="flex flex-col gap-0.5">
            {group.pages.map((page) => {
              const href = page.href ? `${basePath}/${page.href}` : basePath;
              const active = pathname === href;
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "block rounded-md px-3 py-1.5 transition-colors",
                      active
                        ? "bg-surface-2 font-medium text-text"
                        : "text-muted hover:bg-surface-2/60 hover:text-text",
                    )}
                  >
                    {page.title}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
