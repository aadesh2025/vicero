import Link from "next/link";
import { LogoMark } from "@/components/brand/logo";
import { PRODUCT_NAME } from "@/lib/brand";
import { PricingCards } from "@/components/plan/pricing-cards";

/** Public pricing page (docs/22 §10.1) — no auth, no checkout: plans are admin-granted right
 *  now (contact_only, §1/§16), so the only action here is "Contact us". docs/23 (a real payment
 *  processor) is deferred and explicitly out of scope. */
export const metadata = { title: `Pricing — ${PRODUCT_NAME}` };

export default function PricingPage() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <Link href="/" className="mb-10 inline-flex items-center gap-2.5" aria-label={`${PRODUCT_NAME} home`}>
        <LogoMark size={32} />
        <span className="font-display text-lg font-extrabold tracking-tight text-text">{PRODUCT_NAME}</span>
      </Link>

      <h1 className="font-display text-3xl font-semibold tracking-tight text-text sm:text-4xl">Pricing</h1>
      <p className="mt-2 max-w-2xl text-muted">
        One plan per workspace, billed monthly. Every limit below is per workspace — a client running two workspaces
        pays for two.
      </p>

      <div className="mt-10">
        <PricingCards />
      </div>

      {/* docs/22 §3 rules 1, 4, 6 — required plain-language disclosures, not just numbers. */}
      <div className="mt-10 space-y-2 text-xs text-faint">
        <p>A visitor message and the bot&apos;s reply count as two messages toward your monthly limit.</p>
        <p>
          Messaging-provider costs (WhatsApp, Meta, and similar) are billed to you directly by that provider through
          your own connected account — BotForge charges only for the platform.
        </p>
        <p>
          Using your own model API key (BYOK) is available on every plan and does not change your message limit.
        </p>
      </div>
    </main>
  );
}
