"use client";

import { AlertTriangle, ShieldCheck } from "lucide-react";
import type { ApiDocument } from "@/lib/api/types";

/** Ingest-time PII findings (docs/11 Phase B).
 *
 * Counts only — the API never returns the detected values, because a PII report that echoes
 * the PII is the same leak somewhere else. The operator opens the document to see context.
 *
 * `null` flags (never scanned) and `{}` (scanned, clean) are rendered differently on purpose:
 * telling someone a document is clean when nobody has ever looked at it is worse than saying
 * nothing. Every document ingested before migration 0015 is in the first state.
 */

const LABELS: Record<string, [singular: string, plural: string]> = {
  email: ["email address", "email addresses"],
  phone: ["phone number", "phone numbers"],
  address: ["postal address", "postal addresses"],
  secret: ["API key or secret", "API keys or secrets"],
};

export function describeFlags(flags: Record<string, number>): string {
  return Object.entries(flags)
    .filter(([, n]) => n > 0)
    .map(([kind, n]) => {
      const [one, many] = LABELS[kind] ?? [kind, kind];
      return `${n} ${n === 1 ? one : many}`;
    })
    .join(", ");
}

export function PiiBadge({ flags }: { flags: Record<string, number> | null }) {
  // Never scanned: say nothing rather than imply a clean bill of health.
  if (flags === null || flags === undefined) return null;
  const total = Object.values(flags).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  const detail = describeFlags(flags);
  return (
    <span
      title={`Contains ${detail}. Retrievable by this agent — remove it from the document if it should not be.`}
      className="inline-flex items-center gap-1 rounded-md border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[11px] font-medium text-warn-text"
    >
      <AlertTriangle className="size-3" />
      {detail}
    </span>
  );
}

/** Knowledge-base level summary. An affirmative "nothing found" matters as much as a warning:
 * a silent absence reads as "not implemented", which is exactly what an operator must not
 * conclude about a safety check. */
export function PiiSummary({ documents }: { documents: ApiDocument[] }) {
  const scanned = documents.filter((d) => d.pii_flags !== null && d.pii_flags !== undefined);
  if (scanned.length === 0) return null;

  const flagged = scanned.filter(
    (d) => Object.values(d.pii_flags ?? {}).reduce((a, b) => a + b, 0) > 0,
  );
  const unscanned = documents.length - scanned.length;

  if (flagged.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2/40 px-4 py-2.5 text-sm text-muted">
        <ShieldCheck className="size-4 text-success-text" />
        <span>
          No contact details or secrets found in {scanned.length}{" "}
          {scanned.length === 1 ? "document" : "documents"}.
          {unscanned > 0 && (
            <span className="text-faint">
              {" "}
              {unscanned} added before scanning existed — re-ingest to check.
            </span>
          )}
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/[0.06] px-4 py-2.5 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn-text" />
      <div>
        <p className="font-medium text-text">
          {flagged.length} {flagged.length === 1 ? "document contains" : "documents contain"}{" "}
          contact details or secrets
        </p>
        <p className="mt-0.5 text-muted">
          Anything in a knowledge base can be retrieved and quoted by this agent. Replies are
          filtered as a backstop, but the reliable fix is removing it from the source document.
          {unscanned > 0 && ` ${unscanned} more added before scanning existed — re-ingest to check.`}
        </p>
      </div>
    </div>
  );
}
