import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, LegalSection } from "@/components/legal/legal-page";
import { API_BASE } from "@/lib/api/config";
import { LEGAL_EMAIL } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Data Deletion Instructions — Vicero",
  description: "How to have your data deleted from Vicero, and how to check the status of a deletion request.",
};

interface DeletionStatus {
  confirmation_code: string;
  status: "pending" | "completed" | "failed";
  requested_at: string;
  completed_at: string | null;
}

const CODE = /^[A-Za-z0-9_-]{8,64}$/;

/** `undefined` = the lookup itself failed; `null` = the code is unknown. */
async function fetchStatus(code: string): Promise<DeletionStatus | null | undefined> {
  if (!CODE.test(code)) return null;
  const base = (process.env.API_INTERNAL_URL || API_BASE).replace(/\/$/, "");
  try {
    const res = await fetch(`${base}/api/meta/data-deletion/${encodeURIComponent(code)}`, { cache: "no-store" });
    if (res.status === 404) return null;
    if (!res.ok) return undefined;
    return (await res.json()) as DeletionStatus;
  } catch {
    return undefined;
  }
}

const STATUS_TEXT: Record<DeletionStatus["status"], string> = {
  pending: "Received. Deletion is in progress.",
  completed: "Completed. The data tied to this request has been deleted.",
  failed: "We could not finish this automatically. We will complete it manually and email you if you contacted us.",
};

function StatusPanel({ code, status }: { code: string; status: DeletionStatus | null | undefined }) {
  return (
    <section aria-labelledby="status-heading" className="rounded-lg border border-border bg-surface p-5">
      <h2 id="status-heading" className="font-display text-xl font-semibold tracking-tight text-text">
        Deletion request status
      </h2>
      <p className="mt-2 break-all text-sm text-muted">
        Confirmation code: <code className="font-mono text-text">{code}</code>
      </p>
      <p role="status" className="mt-3 text-text">
        {status === undefined && "We could not check the status right now. Please try again shortly."}
        {status === null && "No request was found for this code. Check that the link is complete."}
        {status && STATUS_TEXT[status.status]}
      </p>
      {status && (
        <p className="mt-2 text-sm text-muted">
          Requested {new Date(status.requested_at).toUTCString()}
          {status.completed_at && <>; completed {new Date(status.completed_at).toUTCString()}</>}.
        </p>
      )}
    </section>
  );
}

export default async function DataDeletionPage({
  searchParams,
}: {
  searchParams: Promise<{ code?: string | string[] }>;
}) {
  const { code } = await searchParams;
  const confirmation = Array.isArray(code) ? code[0] : code;
  const status = confirmation ? await fetchStatus(confirmation) : null;

  return (
    <LegalPage
      title="Data Deletion Instructions"
      intro="You can have your data deleted from Vicero at any time. Choose the path that matches who you are."
    >
      {confirmation && <StatusPanel code={confirmation} status={status} />}

      <LegalSection title="If you run a chatbot on Vicero (account owner)">
        <ol>
          <li>Sign in to your Vicero dashboard.</li>
          <li>
            To disconnect a channel (WhatsApp, Messenger, Instagram): open your agent, go to <strong>Channels</strong> and
            remove the connection. This deletes the stored access token.
          </li>
          <li>
            To delete everything: go to <strong>Settings → Organization</strong> and choose{" "}
            <strong>Delete organization</strong>. This removes your agents, knowledge base, contacts, conversations and
            connected channels.
          </li>
          <li>
            You can also delete individual conversations and contacts from the inbox and contacts pages. For anything you
            cannot do yourself, email <a href={`mailto:${LEGAL_EMAIL}`}>{LEGAL_EMAIL}</a>.
          </li>
        </ol>
      </LegalSection>

      <LegalSection title="If you messaged a chatbot (end user)">
        <ol>
          <li>
            Email <a href={`mailto:${LEGAL_EMAIL}`}>{LEGAL_EMAIL}</a> with the subject{" "}
            <strong>Data deletion request</strong>.
          </li>
          <li>
            Say which platform you used (WhatsApp, Facebook Messenger or Instagram) and include your ID or username on it
            (your phone number for WhatsApp), and the name of the business you messaged if you know it.
          </li>
          <li>We confirm your request within 7 days.</li>
          <li>We delete your data within 30 days of the request.</li>
        </ol>
        <p>
          If you remove Vicero&apos;s app from your Facebook or Instagram settings and ask Meta to delete your data, Meta
          sends us the request automatically. We then delete the data linked to your account and give you a confirmation code
          so you can check progress on this page.
        </p>
      </LegalSection>

      <LegalSection title="What is deleted, and what may be kept">
        <p>
          <strong>Deleted:</strong> your conversations and messages, your contact profile (name, profile picture and platform
          ID) and, for account owners, stored channel access tokens, knowledge-base documents and chatbot configuration.
        </p>
        <p>
          <strong>May be kept:</strong> records we must retain by law or for legitimate security needs, such as invoices and
          billing records and security or abuse logs, and encrypted backups until they are overwritten on their normal cycle.
          Messages already delivered to a platform such as WhatsApp are governed by that platform&apos;s own controls.
        </p>
        <p>
          More detail is in our <Link href="/privacy">Privacy Policy</Link>.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
