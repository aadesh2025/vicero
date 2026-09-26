"use client";

import Link from "next/link";
import { Mail, MessageCircle } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui/button";
import { usePlan } from "@/components/plan/use-plan";

// Where a person can reach us until paid plans exist. Set per deployment; nothing is hard-coded
// so a fork or a white-label install points at its own operator.
const EMAIL = process.env.NEXT_PUBLIC_UPGRADE_EMAIL;
const WHATSAPP = process.env.NEXT_PUBLIC_UPGRADE_WHATSAPP;

/** Placeholder for plans and pricing, which are not built yet (docs/18 — out of scope). */
export default function UpgradePage() {
  const { data: plan } = usePlan();
  const ended = plan?.status === "trial_expired";
  const whatsappDigits = WHATSAPP?.replace(/\D/g, "");

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        title="Upgrade"
        description={ended ? "Your free trial has ended." : "Keep your agent running after the trial."}
      />
      <div className="rounded-xl border border-border bg-surface p-6">
        <h2 className="font-display text-lg font-semibold text-text">Plans coming soon</h2>
        <p className="mt-2 text-sm text-muted">
          Paid plans aren&apos;t available to buy online yet. Get in touch and we&apos;ll switch your workspace over
          by hand — your agents, knowledge and conversations stay exactly as they are.
        </p>
        {ended && plan && plan.unanswered_messages > 0 && (
          <p className="mt-3 text-sm text-text">
            <strong>
              {plan.unanswered_messages} visitor {plan.unanswered_messages === 1 ? "message is" : "messages are"}
            </strong>{" "}
            waiting for an answer.
          </p>
        )}
        <div className="mt-5 flex flex-wrap gap-3">
          {EMAIL && (
            <Button asChild variant="primary">
              <a href={`mailto:${EMAIL}?subject=${encodeURIComponent("Upgrade my BotForge workspace")}`}>
                <Mail /> Email us
              </a>
            </Button>
          )}
          {whatsappDigits && (
            <Button asChild variant="outline">
              <a href={`https://wa.me/${whatsappDigits}`} target="_blank" rel="noreferrer">
                <MessageCircle /> WhatsApp
              </a>
            </Button>
          )}
          {!EMAIL && !whatsappDigits && (
            <p className="text-sm text-muted">Contact your BotForge representative to upgrade.</p>
          )}
        </div>
      </div>
      <Link href="/dashboard" className="inline-block text-sm text-accent hover:text-accent">
        Back to dashboard
      </Link>
    </div>
  );
}
