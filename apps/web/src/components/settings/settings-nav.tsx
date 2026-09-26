"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Building2, KeyRound, MessageSquareQuote, PlugZap, ScrollText, UserCircle, Webhook, Zap } from "lucide-react";
import { cn } from "@/lib/utils";

const items = [
  { label: "Organization", href: "/settings/org", icon: Building2 },
  { label: "Canned responses", href: "/settings/canned-responses", icon: MessageSquareQuote },
  { label: "Macros", href: "/settings/macros", icon: Zap },
  { label: "Provider keys", href: "/settings/credentials", icon: PlugZap },
  { label: "API keys", href: "/settings/api-keys", icon: KeyRound },
  { label: "Webhooks", href: "/settings/webhooks", icon: Webhook },
  { label: "Audit log", href: "/settings/audit", icon: ScrollText },
  { label: "Profile", href: "/settings/profile", icon: UserCircle },
];

export function SettingsNav() {
  const pathname = usePathname();
  return (
    <nav className="flex gap-1 overflow-x-auto no-scrollbar lg:flex-col lg:gap-0.5">
      {items.map((item) => {
        const active = pathname === item.href;
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "flex items-center gap-2.5 whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-surface-2 text-text" : "text-muted hover:bg-surface-2/60 hover:text-text",
            )}
          >
            <Icon className={cn("size-4", active ? "text-accent" : "text-faint")} />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
