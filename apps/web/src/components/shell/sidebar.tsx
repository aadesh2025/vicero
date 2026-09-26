"use client";

import Link from "next/link";
import { PanelLeftClose, PanelLeft, X } from "lucide-react";
import { LogoMark } from "@/components/brand/logo";
import { SidebarNav } from "./sidebar-nav";
import { OrgSwitcher } from "./org-switcher";
import { UserMenu } from "./user-menu";
import { TrialMeter } from "@/components/plan/trial-banner";
import { usePlan } from "@/components/plan/use-plan";
import { Button } from "@/components/ui/button";
import { PRODUCT_NAME, PRODUCT_TAGLINE } from "@/lib/brand";
import { useUI } from "@/lib/store/ui";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const { collapsed, toggleCollapsed, mobileOpen, setMobileOpen } = useUI();
  const { data: plan } = usePlan();

  return (
    <>
      {/* Mobile scrim */}
      {mobileOpen && (
        <div
          aria-hidden
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <aside
        aria-label="Sidebar"
        className={cn(
          // h-screen + sticky bounds the sidebar to the viewport so its <nav> (min-h-0) scrolls
          // internally and the header + footer always stay in view.
          "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border bg-sidebar transition-[width,transform] duration-200 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
          collapsed ? "w-[72px]" : "w-[240px]",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className={cn("flex items-center gap-2 px-4 pb-3 pt-4", collapsed && "flex-col px-0")}>
          <Link href="/dashboard" className="flex min-w-0 items-center gap-2.5" aria-label={`${PRODUCT_NAME} home`}>
            {collapsed ? (
              <LogoMark />
            ) : (
              <>
                <LogoMark size={32} />
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className="font-display text-[17px] font-extrabold tracking-tight text-text">{PRODUCT_NAME}</span>
                  <span className="truncate text-[11px] font-semibold text-faint">{PRODUCT_TAGLINE}</span>
                </span>
              </>
            )}
          </Link>
          {/* Primary collapse toggle — always beside the brand mark, never scroll-dependent.
              Shown on desktop only (mobile uses the ✕ below to close the drawer). */}
          <button
            className={cn(
              "rounded-lg p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text",
              collapsed ? "hidden lg:block" : "ml-auto hidden lg:block",
            )}
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeft className="size-[18px]" /> : <PanelLeftClose className="size-[18px]" />}
          </button>
          <button
            className="ml-auto rounded-lg p-1.5 text-faint hover:text-text lg:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className={cn("px-3", collapsed && "px-2")}>
          <OrgSwitcher collapsed={collapsed} />
        </div>

        <SidebarNav collapsed={collapsed} />

        <div className={cn("flex flex-col gap-2 border-t border-border p-3", collapsed && "px-2")}>
          {!collapsed && plan && plan.status === "trial" && <TrialMeter plan={plan} variant="card" />}
          <UserMenu collapsed={collapsed} />
          <Button
            variant="ghost"
            size={collapsed ? "icon" : "sm"}
            onClick={toggleCollapsed}
            className={cn("hidden w-full lg:flex", !collapsed && "justify-start")}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeft className="size-[18px]" /> : <PanelLeftClose className="size-[18px]" />}
            {!collapsed && <span>Collapse</span>}
          </Button>
        </div>
      </aside>
    </>
  );
}
