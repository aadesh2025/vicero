"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Bot, ChevronsLeft, ChevronsRight, Inbox, Lock, Plug, Sparkles, Workflow } from "lucide-react";
import { ChannelText } from "@/components/shared/channel";
import { usePlan } from "@/components/plan/use-plan";
import { getOverview } from "@/lib/api/analytics";
import { useSession } from "@/lib/store/session";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "ai-rail-collapsed";

interface Action {
  title: string;
  sub: string;
  href: string;
  icon: typeof Bot;
  /** One of the fixed pastel gradients (docs/20 §9.3), matching the reference's coloured cards. */
  gradient: "bg-lavender" | "bg-mint" | "bg-gradient-blue" | "bg-peach";
  /** Set when this action is behind a plan limit; swaps the sub-label and the destination. */
  locked?: string;
}

/**
 * Dashboard-only quick-links rail (docs/20 §9.3). Quick links to existing routes only — no chat
 * input yet; the conversational AI builder is a separate, unbuilt feature.
 */
export function AiBuilderRail({ className }: { className?: string }) {
  const [collapsed, setCollapsed] = useState(false);
  const [ready, setReady] = useState(false);
  const activeOrgId = useSession((s) => s.activeOrgId);
  const { data: plan } = usePlan();
  const { data: overview } = useQuery({
    queryKey: ["dash-overview", activeOrgId],
    queryFn: () => getOverview(),
    enabled: Boolean(activeOrgId),
  });

  // Hydration guard, same pattern as the theme toggle: localStorage only exists client-side.
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCollapsed(localStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      /* private mode — falls back to expanded */
    }
    setReady(true);
  }, []);

  function toggle() {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      } catch {
        /* nothing to persist to */
      }
      return next;
    });
  }

  const actions: Action[] = [
    {
      title: "Build a new agent",
      sub: "Give it a persona and a knowledge base",
      href: "/agents",
      icon: Bot,
      gradient: "bg-lavender",
      locked: plan && !plan.can_create_agent ? "Your plan's agent limit is reached" : undefined,
    },
    {
      title: "Connect a channel",
      sub: "WhatsApp, Instagram, Telegram and more",
      href: "/settings/credentials",
      icon: Plug,
      gradient: "bg-mint",
    },
    {
      title: "Create an automation",
      sub: "Wire an agent to n8n",
      href: "/automations",
      icon: Workflow,
      gradient: "bg-gradient-blue",
      locked: plan && !plan.features.n8n ? "Automations are part of a paid plan" : undefined,
    },
    {
      title: "Review waiting chats",
      sub: "Conversations a human needs to answer",
      href: "/inbox",
      icon: Inbox,
      gradient: "bg-peach",
    },
  ];

  // The insight is only ever a real comparison the data supports: at least two channels with
  // traffic, and a genuine gap between the best and the overall rate. Nothing here is invented
  // copy — an org with one channel, or with no meaningful gap, simply gets no insight card.
  const withTraffic = (overview?.by_channel ?? []).filter((b) => b.conversations > 0);
  const best = withTraffic.reduce<(typeof withTraffic)[number] | null>(
    (top, b) => (!top || b.resolution_rate > top.resolution_rate ? b : top),
    null,
  );
  const overallPct = Math.round((overview?.resolution_rate ?? 0) * 100);
  const bestPct = best ? Math.round(best.resolution_rate * 100) : 0;
  const insight = withTraffic.length >= 2 && best && bestPct - overallPct >= 5 ? { best, gap: bestPct - overallPct } : null;

  if (!ready) return <div className={cn("w-[300px] shrink-0", className)} aria-hidden />;

  return (
    <aside
      aria-label="AI builder"
      className={cn(
        "sticky top-[88px] flex shrink-0 flex-col gap-4 self-start rounded-card border border-border bg-surface p-4",
        collapsed ? "w-[60px] items-center" : "w-[300px]",
        className,
      )}
    >
      <div className={cn("flex items-center gap-2.5", collapsed && "flex-col")}>
        <span className="grid size-8 shrink-0 place-items-center rounded-[10px] bg-gradient-to-br from-ai to-accent-strong text-white">
          <Sparkles className="size-4" aria-hidden />
        </span>
        {!collapsed && (
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-extrabold text-ai-text">AI Builder</span>
            <span className="block text-[11px] font-semibold text-faint">Builds and manages for you</span>
          </span>
        )}
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? "Expand AI Builder" : "Collapse AI Builder"}
          className="rounded-lg p-1 text-faint hover:bg-surface-2 hover:text-text"
        >
          {collapsed ? <ChevronsLeft className="size-4" /> : <ChevronsRight className="size-4" />}
        </button>
      </div>

      {!collapsed && (
        <>
          <div className="rounded-[14px] bg-ai-soft p-3.5 text-[13px] font-semibold leading-relaxed text-text">
            Tell me what you need and I’ll point you at the right place to build it.
          </div>

          <div className="flex flex-col gap-2">
            {actions.map((a) => (
              <Link
                key={a.title}
                href={a.locked ? "/billing/upgrade" : a.href}
                // Fixed pastel gradient + fixed dark text in BOTH themes (docs/20 §9.3) — these
                // cards deliberately don't follow the surrounding theme, same as the reference.
                className={cn("flex items-center gap-3 rounded-[13px] bg-[length:100%_100%] p-2.5 shadow-card transition-transform hover:-translate-y-px", a.gradient)}
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-[10px] bg-white/60 text-on-pastel">
                  <a.icon className="size-4" aria-hidden />
                </span>
                <span className="flex min-w-0 flex-col text-left text-on-pastel">
                  <span className="text-[13px] font-bold">{a.title}</span>
                  <span className="flex items-center gap-1 truncate text-xs font-semibold opacity-80">
                    {a.locked && <Lock className="size-3 shrink-0" aria-hidden />}
                    {a.locked ?? a.sub}
                  </span>
                </span>
              </Link>
            ))}
          </div>

          {insight && (
            <div className="flex flex-col gap-1.5 rounded-[14px] bg-success-soft p-3.5">
              <span className="text-[11px] font-extrabold tracking-[0.06em] text-success-text">SMART INSIGHT</span>
              <span className="text-[13px] font-semibold leading-snug text-text">
                <ChannelText channel={best!.channel} /> resolves {insight.gap} points more often than your average this
                period.
              </span>
            </div>
          )}
        </>
      )}
    </aside>
  );
}
