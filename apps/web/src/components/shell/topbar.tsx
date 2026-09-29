"use client";

import { Bell, Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./theme-toggle";
import { useUI } from "@/lib/store/ui";

// Page titles stay in each page's own header (PageHeader), so the topbar carries only global
// controls. That keeps every page's `h1` where it always was for tests and screen readers.
export function Topbar() {
  const setMobileOpen = useUI((s) => s.setMobileOpen);

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-bg/85 px-4 backdrop-blur-md md:px-6">
      <button
        className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text lg:hidden"
        onClick={() => setMobileOpen(true)}
        aria-label="Open navigation"
      >
        <Menu className="size-5" />
      </button>

      <div className="ml-auto flex items-center gap-2">
        <Button variant="secondary" size="icon" aria-label="Notifications" className="relative">
          <Bell className="size-[18px]" />
          <span aria-hidden className="absolute right-2.5 top-2.5 size-2 rounded-full bg-error ring-2 ring-surface" />
        </Button>
        <ThemeToggle />
      </div>
    </header>
  );
}
