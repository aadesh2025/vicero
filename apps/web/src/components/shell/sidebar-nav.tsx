"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { nav, type NavGroup } from "@/lib/nav";
import { listInbox } from "@/lib/api/inbox";
import { useSession } from "@/lib/store/session";
import { cn } from "@/lib/utils";

const STAFF_GROUP: NavGroup = {
  heading: "Platform",
  items: [{ label: "Admin", href: "/admin", icon: ShieldAlert }],
};

export function SidebarNav({ collapsed }: { collapsed: boolean }) {
  const pathname = usePathname();
  const isStaff = useSession((s) => Boolean(s.user?.is_staff));
  const activeOrgId = useSession((s) => s.activeOrgId);
  const groups = isStaff ? [...nav, STAFF_GROUP] : nav;

  // Conversations waiting on a human. Was a hardcoded "3" — it never reflected anything.
  const { data: waiting } = useQuery({
    queryKey: ["inbox-waiting", activeOrgId],
    queryFn: () => listInbox("handoff"),
    enabled: Boolean(activeOrgId),
    refetchInterval: 30_000,
    select: (rows) => rows.length,
  });

  return (
    <nav className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-3 py-4 no-scrollbar">
      {groups.map((group, gi) => (
        <div key={gi} className="flex flex-col gap-1">
          {group.heading && !collapsed && (
            <p className="px-3 pb-1 text-[11px] font-medium uppercase tracking-wider text-faint">
              {group.heading}
            </p>
          )}
          {group.items.map((item) => {
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                title={collapsed ? item.label : undefined}
                className={cn(
                  "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  collapsed && "justify-center px-0",
                  active
                    ? "bg-surface-2 text-text"
                    : "text-muted hover:bg-surface-2/60 hover:text-text",
                )}
              >
                {active && (
                  <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-accent shadow-[0_0_8px_0_rgb(255_106_61_/_0.6)]" />
                )}
                <Icon
                  className={cn(
                    "size-[18px] shrink-0 transition-colors",
                    active ? "text-accent-soft" : "text-faint group-hover:text-muted",
                  )}
                />
                {!collapsed && <span className="flex-1">{item.label}</span>}
                {!collapsed && item.href === "/inbox" && waiting ? (
                  <span className="rounded-full bg-accent/15 px-1.5 py-0.5 text-[11px] font-semibold text-accent-soft">
                    {waiting}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
