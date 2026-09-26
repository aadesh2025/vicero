"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, MailWarning } from "lucide-react";
import { Button } from "@/components/ui/button";
import { resendVerification } from "@/lib/api/auth";
import { UPGRADE_PATH, type PlanStatus } from "@/lib/api/plan";
import { usePlan } from "@/components/plan/use-plan";
import { useSession } from "@/lib/store/session";

/** Trial state for every page of the app: a usage meter while it runs, a persistent
 * "upgrade" banner once it has ended, and a nudge to verify the email that publishing needs. */
export function TrialBanner() {
  const { data: plan } = usePlan();
  const user = useSession((s) => s.user);
  if (!plan || plan.status === "legacy") return null;
  const unverified = user && !user.email_verified;
  return (
    <div className="space-y-px">
      {plan.status === "trial_expired" ? <EndedBanner plan={plan} /> : <TrialMeter plan={plan} />}
      {unverified && plan.status === "trial" && <VerifyNudge email={user.email} />}
    </div>
  );
}

export function TrialMeter({ plan }: { plan: PlanStatus }) {
  const used = plan.messages_used;
  const limit = plan.messages_limit ?? 0;
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const days = plan.days_left ?? 0;
  return (
    <div
      data-testid="trial-meter"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-surface-2/60 px-4 py-2 text-sm md:px-6 lg:px-8"
    >
      <span className="font-medium text-text">Free trial</span>
      <span className="text-muted">
        {used} / {limit} messages · {days} {days === 1 ? "day" : "days"} left
      </span>
      <div
        role="progressbar"
        aria-label="Trial messages used"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        className="h-1.5 w-32 overflow-hidden rounded-full bg-border"
      >
        <div className={pct >= 80 ? "h-full bg-warn" : "h-full bg-accent"} style={{ width: `${pct}%` }} />
      </div>
      <Button asChild variant="outline" size="sm" className="ml-auto">
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
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-error/30 bg-error/10 px-4 py-3 text-sm md:px-6 lg:px-8"
    >
      <AlertTriangle className="size-4 shrink-0 text-error-text" />
      <div className="flex-1">
        <p className="font-medium text-text">Your free trial has ended. Upgrade to continue.</p>
        <p className="text-muted">
          Your agent has stopped replying to visitors. Nothing was deleted.
          {missed > 0 && (
            <>
              {" "}
              <strong className="text-text">
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
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-warn/30 bg-warn/[0.07] px-4 py-2 text-sm md:px-6 lg:px-8">
      <MailWarning className="size-4 shrink-0 text-warn-text" />
      <span className="text-muted">
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
        className="font-medium text-accent hover:text-accent disabled:text-faint"
      >
        {state === "sent" ? "Email sent" : "Resend email"}
      </button>
    </div>
  );
}
