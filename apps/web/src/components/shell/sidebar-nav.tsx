"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { nav, type NavGroup, type NavItem } from "@/lib/nav";
import { listInbox } from "@/lib/api/inbox";
import { useSession } from "@/lib/store/session";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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
    <nav aria-label="Main" className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-3 py-2 no-scrollbar">
      {groups.map((group, gi) => (
        <div key={gi} className="flex flex-col gap-0.5">
          {group.heading && !collapsed && (
            <p className="px-3 pb-1 pt-4 text-[10.5px] font-extrabold uppercase tracking-[0.08em] text-faint">
              {group.heading}
            </p>
          )}
          {group.heading && collapsed && <div aria-hidden className="mx-2 my-2 h-px bg-border" />}
          {group.items.map((item) => (
            <NavLink
              key={item.href}
              item={item}
              collapsed={collapsed}
              active={pathname === item.href || pathname.startsWith(item.href + "/")}
              badge={item.href === "/inbox" && waiting ? waiting : undefined}
            />
          ))}
        </div>
      ))}
    </nav>
  );
}

function NavLink({
  item,
  collapsed,
  active,
  badge,
}: {
  item: NavItem;
  collapsed: boolean;
  active: boolean;
  badge?: number;
}) {
  const Icon = item.icon;
  const link = (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-3 rounded-[10px] px-3 py-2 text-[13.5px] transition-colors",
        collapsed && "justify-center px-0",
        active
          ? "bg-accent-soft font-extrabold text-accent"
          : "font-semibold text-muted hover:bg-surface-2 hover:text-text",
      )}
    >
      {active && <span aria-hidden className="absolute -left-3 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-accent" />}
      <Icon className={cn("size-[18px] shrink-0", active ? "text-accent" : "text-faint group-hover:text-muted")} />
      {!collapsed && <span className="flex-1">{item.label}</span>}
      {badge ? (
        <span
          className={cn(
            "grid h-5 min-w-5 place-items-center rounded-full bg-error-soft px-1.5 text-[11px] font-extrabold tabular-nums text-error-text",
            collapsed && "absolute right-1 top-0.5 h-4 min-w-4 px-1 text-[10px]",
          )}
        >
          {badge}
          <span className="sr-only"> waiting</span>
        </span>
      ) : null}
    </Link>
  );

  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}
