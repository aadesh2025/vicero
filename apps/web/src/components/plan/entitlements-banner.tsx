"use client";

import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useEntitlements } from "@/lib/entitlements";
import { ContactUsButtons } from "@/components/plan/contact-us";
import { cn } from "@/lib/utils";

/** ADR-103: an org counts as "near limit" once usage reaches 90% of its *effective* cap (the
 *  plan limit plus any packs bought this period) — the same threshold the admin panel's
 *  `near_limit` filter uses (`app/modules/admin/service.py::_NEAR_LIMIT_RATIO`), not the 80%
 *  in docs/22 §9.1's UI sketch, which never pinned a number. Kept in sync deliberately: two
 *  different thresholds for the same word would confuse whoever reads both screens. */
const NEAR_LIMIT_RATIO = 0.9;

/** trial / trial_expired / legacy already have their own UI (`TrialBanner`, `TrialMeter`,
 *  docs/18). This module only covers the gap A0-A5 built the engine for but nothing
 *  customer-facing read yet: starter / pro / business / plan_expired. */
const HANDLED_HERE = new Set(["starter", "pro", "business", "plan_expired"]);

/** Compact plan + usage display for the sidebar (docs/22 §10.2's "usage indicator"), the paid-
 *  plan sibling of `TrialMeter`. No expiry countdown here even when `plan_expires_at` is set —
 *  docs/22 §16.3: a paid plan's admin-managed expiry must never read like a countdown to a
 *  customer who may have already paid and is just waiting on staff to mark it renewed. */
export function EntitlementsMeter() {
  const { data } = useEntitlements();
  if (!data || !HANDLED_HERE.has(data.status)) return null;

  const planLabel = data.plan.charAt(0).toUpperCase() + data.plan.slice(1);

  if (data.status === "plan_expired") {
    return (
      <div
        data-testid="entitlements-meter"
        className="flex items-center justify-between gap-2 rounded-card border border-error/30 bg-error-soft p-3.5"
      >
        <span className="text-[13px] font-extrabold text-text">{planLabel}</span>
        <Badge variant="error">Expired</Badge>
      </div>
    );
  }

  const used = data.messages.used;
  const limit = data.messages.effective_limit;
  const pct = limit !== null && limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;

  return (
    <div data-testid="entitlements-meter" className="flex flex-col gap-2.5 rounded-card border border-border bg-surface-2 p-3.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-extrabold text-text">{planLabel}</span>
        {limit !== null && pct >= NEAR_LIMIT_RATIO * 100 && (
          <span className="rounded-lg bg-warn-soft px-2 py-0.5 text-[11px] font-extrabold text-warn-text">
            {pct >= 100 ? "At limit" : "Near limit"}
          </span>
        )}
      </div>
      <p className="text-xs font-semibold text-muted">
        {used.toLocaleString()} / {limit === null ? "Unlimited" : limit.toLocaleString()} messages
      </p>
      {limit !== null && (
        <div
          role="progressbar"
          aria-label="Messages used this period"
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={used}
          className="h-[7px] overflow-hidden rounded bg-surface-3"
        >
          <div
            className={cn("h-full rounded", pct >= 100 ? "bg-error" : pct >= 80 ? "bg-warn" : "bg-accent")}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

/** Global banner: the plan_expired state, and the near/at-limit warning — visible but never
 *  blocking, since A4 already enforces the real limits server-side (docs/22 §11). No self-serve
 *  upgrade exists, so every CTA here is "Contact us" (§1, §16), same as the pricing page. */
export function EntitlementsBanner() {
  const { data } = useEntitlements();
  if (!data || !HANDLED_HERE.has(data.status)) return null;

  if (data.status === "plan_expired") {
    const missed = data.messages.unanswered;
    return (
      <div
        role="alert"
        data-testid="plan-expired-banner"
        className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-error/30 bg-error-soft px-4 py-3 text-sm md:px-6 lg:px-8"
      >
        <AlertTriangle className="size-4 shrink-0 text-error-text" aria-hidden />
        <div className="flex-1">
          <p className="font-extrabold text-text">Your plan has expired. Contact us to continue.</p>
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
        <ContactUsButtons subject="Renew my Vicero workspace" />
      </div>
    );
  }

  const used = data.messages.used;
  const limit = data.messages.effective_limit;
  if (limit === null) return null; // unlimited — nothing to warn about

  const atLimit = used >= limit;
  const nearLimit = !atLimit && used >= NEAR_LIMIT_RATIO * limit;
  if (!atLimit && !nearLimit) return null;

  return (
    <div
      role="alert"
      data-testid={atLimit ? "at-limit-banner" : "near-limit-banner"}
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2 text-sm md:px-6 lg:px-8",
        atLimit ? "border-error/30 bg-error-soft" : "border-warn/30 bg-warn-soft",
      )}
    >
      <AlertTriangle className={cn("size-4 shrink-0", atLimit ? "text-error-text" : "text-warn-text")} aria-hidden />
      <span className="font-extrabold text-text">
        {atLimit ? "You've reached your message limit" : "You're close to your message limit"}
      </span>
      <span className="font-semibold text-muted">
        {used.toLocaleString()} / {limit.toLocaleString()} messages this period
      </span>
      <div className="ml-auto">
        <ContactUsButtons subject="Add messages to my Vicero workspace" />
      </div>
    </div>
  );
}
