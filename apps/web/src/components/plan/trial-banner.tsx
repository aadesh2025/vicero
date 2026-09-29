"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, MailWarning } from "lucide-react";
import { Button } from "@/components/ui/button";
import { resendVerification } from "@/lib/api/auth";
import { UPGRADE_PATH, type PlanStatus } from "@/lib/api/plan";
import { usePlan } from "@/components/plan/use-plan";
import { useSession } from "@/lib/store/session";
import { cn } from "@/lib/utils";

/** Trial state for every page of the app. On desktop the running-trial meter lives in the
 * sidebar (`TrialMeter variant="card"`); this strip shows the same meter where there is no
 * sidebar (below `lg`), plus the persistent "ended" banner and the verify-your-email nudge. */
export function TrialBanner() {
  const { data: plan } = usePlan();
  const user = useSession((s) => s.user);
  // `PlanStatus["status"]` is typed to these two plus "legacy", but the backend's actual
  // `get_entitlements()` status can also be starter/pro/business/plan_expired (docs/22) — an
  // untyped string slipping through would otherwise render this trial-only strip's "Free trial"
  // copy on a paying customer. Those statuses have their own banner (`EntitlementsBanner`).
  if (!plan || (plan.status !== "trial" && plan.status !== "trial_expired")) return null;
  const unverified = user && !user.email_verified;
  return (
    <div className="space-y-px">
      {plan.status === "trial_expired" ? (
        <EndedBanner plan={plan} />
      ) : (
        <TrialMeter plan={plan} variant="strip" className="lg:hidden" />
      )}
      {unverified && plan.status === "trial" && <VerifyNudge email={user.email} />}
    </div>
  );
}

/** Usage meter for a running trial. `card` is the sidebar version, `strip` the full-width one. */
export function TrialMeter({
  plan,
  variant = "strip",
  className,
}: {
  plan: PlanStatus;
  variant?: "card" | "strip";
  className?: string;
}) {
  const used = plan.messages_used;
  const limit = plan.messages_limit ?? 0;
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const days = plan.days_left ?? 0;
  const ending = days <= 3 || pct >= 80;
  const summary = `${used} / ${limit} messages · ${days} ${days === 1 ? "day" : "days"} left`;

  const bar = (
    <div
      role="progressbar"
      aria-label="Trial messages used"
      aria-valuemin={0}
      aria-valuemax={limit}
      aria-valuenow={used}
      className={cn("h-[7px] overflow-hidden rounded bg-surface-3", variant === "strip" && "w-32")}
    >
      <div
        className={cn("h-full rounded", pct >= 80 ? "bg-warn" : "bg-gradient-to-r from-ai to-accent-strong")}
        style={{ width: `${pct}%` }}
      />
    </div>
  );

  if (variant === "card") {
    return (
      <div
        data-testid="trial-meter"
        className={cn("flex flex-col gap-2.5 rounded-card border border-border bg-ai-soft p-3.5", className)}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] font-extrabold text-text">Free trial</span>
          {ending && (
            <span className="rounded-lg bg-warn-soft px-2 py-0.5 text-[11px] font-extrabold text-warn-text">Ending soon</span>
          )}
        </div>
        <p className="text-xs font-semibold text-muted">{summary}</p>
        {bar}
        <Button asChild variant="secondary" size="sm" className="w-full text-ai-text">
          <Link href={UPGRADE_PATH}>Upgrade</Link>
        </Button>
      </div>
    );
  }

  return (
    <div
      data-testid="trial-meter-compact"
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-ai-soft px-4 py-2 text-sm md:px-6 lg:px-8",
        className,
      )}
    >
      <span className="font-extrabold text-text">Free trial</span>
      <span className="font-semibold text-muted">{summary}</span>
      {bar}
      <Button asChild variant="secondary" size="sm" className="ml-auto text-ai-text">
        <Link href={UPGRADE_PATH}>Upgrade</Link>
      </Button>
    </div>
  );
}

export function EndedBanner({ plan }: { plan: PlanStatus }) {
  const missed = plan.unanswered_messages;
  return (
    <div
      role="alert"
      data-testid="trial-ended"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-error/30 bg-error-soft px-4 py-3 text-sm md:px-6 lg:px-8"
    >
      <AlertTriangle className="size-4 shrink-0 text-error-text" aria-hidden />
      <div className="flex-1">
        <p className="font-extrabold text-text">Your free trial has ended. Upgrade to continue.</p>
        <p className="font-medium text-muted">
          Your agent has stopped replying to visitors. Nothing was deleted.
          {missed > 0 && (
            <>
              {" "}
              <strong className="font-extrabold text-text">
                {missed} visitor {missed === 1 ? "message was" : "messages were"} not answered.
              </strong>
            </>
          )}
        </p>
      </div>
      <Button asChild variant="primary" size="sm">
        <Link href={UPGRADE_PATH}>Upgrade</Link>
      </Button>
    </div>
  );
}

function VerifyNudge({ email }: { email: string }) {
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-warn/30 bg-warn-soft px-4 py-2 text-sm md:px-6 lg:px-8">
      <MailWarning className="size-4 shrink-0 text-warn-text" aria-hidden />
      <span className="font-medium text-muted">
        Verify <span className="font-mono text-text">{email}</span> to publish your agent to live channels.
      </span>
      <button
        type="button"
        disabled={state !== "idle"}
        onClick={async () => {
          setState("sending");
          try {
            await resendVerification(email);
          } finally {
            setState("sent");
          }
        }}
        className="font-extrabold text-accent hover:underline disabled:text-faint disabled:no-underline"
      >
        {state === "sent" ? "Email sent" : "Resend email"}
      </button>
    </div>
  );
}
