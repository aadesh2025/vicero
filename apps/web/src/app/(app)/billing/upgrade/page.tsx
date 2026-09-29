"use client";

import Link from "next/link";
import { PageHeader } from "@/components/dashboard/page-header";
import { usePlan } from "@/components/plan/use-plan";
import { PricingCards } from "@/components/plan/pricing-cards";

/** The three plan cards + the 402 landing target (docs/22 §10.1). Plans are admin-granted, not
 *  bought online — `PricingCards` renders the same "Contact us" CTA as the public /pricing
 *  page, so this route's own job is just the context (trial ended? messages waiting?) around
 *  it, not a second copy of the cards or the contact mechanism. */
export default function UpgradePage() {
  const { data: plan } = usePlan();
  const ended = plan?.status === "trial_expired";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        title="Upgrade"
        description={ended ? "Your free trial has ended." : "Keep your agent running after the trial."}
      />
      {ended && plan && plan.unanswered_messages > 0 && (
        <div className="rounded-xl border border-warn/30 bg-warn/[0.06] p-4 text-sm text-text">
          <strong className="font-bold">
            {plan.unanswered_messages} visitor {plan.unanswered_messages === 1 ? "message is" : "messages are"}
          </strong>{" "}
          waiting for an answer. Your agents, knowledge and conversations are all still here — reaching out below
          switches your workspace over by hand.
        </div>
      )}
      <PricingCards />
      <Link href="/dashboard" className="inline-block text-sm text-accent hover:text-accent">
        Back to dashboard
      </Link>
    </div>
  );
}
