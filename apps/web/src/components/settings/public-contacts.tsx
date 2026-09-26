"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Plus, X } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { updateOrg } from "@/lib/api/orgs";
import { useSession, activeOrg } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";

/** The PII-redaction allowlist (docs/11 Phase B, ADR-053/056).
 *
 * This shipped read-only: the backend read `public_contacts` on every turn but nothing could
 * write to it, so the list was empty for every org and the reply filter stripped a client's
 * own support address out of its own answers. The empty state below says so explicitly rather
 * than looking like an unused optional field — an operator who leaves this blank has an agent
 * that cannot tell anyone how to reach them.
 *
 * Entries are validated server-side against the same matcher the redactor uses, so a URL or a
 * line of prose is rejected instead of sitting here looking configured and doing nothing.
 */
export function PublicContacts() {
  const org = useSession(activeOrg);
  const orgs = useSession((s) => s.orgs);
  const setOrgs = useSession((s) => s.setOrgs);
  const canManageOrg = useCan("org:manage");

  const [entry, setEntry] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (contacts: string[]) => updateOrg(org?.id ?? "", { public_contacts: contacts }),
    onSuccess: (updated) => {
      setOrgs(orgs.map((o) => (o.id === updated.id ? updated : o)));
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!org) return null;

  // Derived, not mirrored: the store is the source of truth and the mutation updates it on
  // success. While a save is in flight we render what was submitted, so the chip appears
  // immediately without a second copy of the list to keep in step.
  const contacts = save.isPending && save.variables ? save.variables : (org.public_contacts ?? []);

  function add() {
    const value = entry.trim();
    if (!value || contacts.includes(value)) {
      setEntry("");
      return;
    }
    setEntry("");
    save.mutate([...contacts, value]);
  }

  function removeAt(value: string) {
    save.mutate(contacts.filter((c) => c !== value));
  }

  return (
    <Section
      title="Public contacts"
      description="Contact details your agent is allowed to share with visitors. Anything not listed here gets redacted from replies automatically."
    >
      {contacts.length === 0 ? (
        <p className="rounded-md border border-warn/40 bg-warn/[0.06] px-3 py-2 text-sm text-muted">
          Nothing listed, so your agent currently shares{" "}
          <span className="text-text">no contact details at all</span> — a visitor asking how to
          reach you gets a redacted reply. Add your support email or phone number below.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {contacts.map((contact) => (
            <li
              key={contact}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2.5 py-1 text-sm text-text"
            >
              <span className="font-mono text-[13px]">{contact}</span>
              {canManageOrg && (
                <button
                  onClick={() => removeAt(contact)}
                  disabled={save.isPending}
                  aria-label={`Remove ${contact}`}
                  className="rounded p-0.5 text-faint transition-colors hover:text-error-text disabled:opacity-40"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canManageOrg && (
        <div className="mt-4 flex items-center gap-2">
          <Input
            value={entry}
            onChange={(e) => setEntry(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            placeholder="support@yourbusiness.com or +91 80 4000 1000"
            aria-label="Add a public contact"
            className="max-w-sm"
          />
          <Button variant="outline" size="sm" onClick={add} disabled={!entry.trim() || save.isPending}>
            {save.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Add
          </Button>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-error-text">{error}</p>}
      <p className="mt-2 text-xs text-faint">
        Email addresses and phone numbers only — those are what the reply filter recognises.
        Anything else would be accepted into the list and then quietly ignored.
      </p>
    </Section>
  );
}
