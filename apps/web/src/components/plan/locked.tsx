"use client";

import Link from "next/link";
import { Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { UPGRADE_PATH, type PlanFeature } from "@/lib/api/plan";
import { useFeature } from "@/components/plan/use-plan";

/** A locked feature is shown, not hidden — it's the upsell. The server refuses the action
 * regardless (HTTP 402 `plan_limit`); this only explains why and where to go. */
export function LockedRegion({
  feature,
  label,
  children,
}: {
  feature: PlanFeature;
  /** What is locked, in plain words: "Workflows", "Custom tools and MCP servers". */
  label: string;
  children: React.ReactNode;
}) {
  const { allowed } = useFeature(feature);
  if (allowed) return <>{children}</>;
  return (
    <div className="space-y-4">
      <UpgradeNotice title={`${label} are part of a paid plan`}>
        Your free trial lets you explore them, but not use them. Upgrade to unlock.
      </UpgradeNotice>
      {/* `inert` removes it from the tab order and the accessibility tree as well as blocking clicks. */}
      <div inert aria-hidden="true" className="pointer-events-none select-none opacity-50">
        {children}
      </div>
    </div>
  );
}

/** Replaces a whole page body when its feature is locked (Automations). */
export function LockedPanel({
  feature,
  title,
  children,
}: {
  feature: PlanFeature;
  title: string;
  children: React.ReactNode;
}) {
  const { allowed } = useFeature(feature);
  if (allowed) return null;
  return <UpgradeNotice title={title}>{children}</UpgradeNotice>;
}

export function UpgradeNotice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-accent/25 bg-accent/[0.05] px-4 py-4 sm:flex-row sm:items-center">
      <span className="grid size-9 shrink-0 place-items-center rounded-md border border-accent/30 bg-accent/10 text-accent">
        <Lock className="size-4" />
      </span>
      <div className="flex-1 text-sm">
        <p className="font-medium text-text">{title}</p>
        <p className="mt-0.5 text-muted">{children}</p>
      </div>
      <Button asChild variant="primary" size="sm">
        <Link href={UPGRADE_PATH}>Upgrade</Link>
      </Button>
    </div>
  );
}

/** A disabled button that says why, for actions the plan doesn't allow. */
export function LockedButton({
  reason,
  children,
}: {
  reason: string;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* A disabled <button> swallows pointer events, so the tooltip hangs off a wrapper. */}
        <span tabIndex={0} className="inline-flex" aria-label={reason}>
          <Button variant="primary" disabled aria-disabled>
            <Lock /> {children}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {reason}{" "}
        <Link href={UPGRADE_PATH} className="font-medium text-accent underline">
          Upgrade
        </Link>
      </TooltipContent>
    </Tooltip>
  );
}
