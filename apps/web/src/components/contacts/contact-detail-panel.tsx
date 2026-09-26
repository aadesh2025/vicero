"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ContactAvatar } from "@/components/inbox/contact-avatar";
import { channelMeta } from "@/lib/channel-meta";
import {
  addContactNote,
  getContact,
  setContactLabels,
  updateContact,
  LEAD_STAGES,
} from "@/lib/api/contacts";
import { relativeTime } from "@/lib/utils";

/**
 * The right-hand contact card, mirroring the Inbox's — About, lead stage, order status,
 * labels, a notes timeline, and the conversations this person has had.
 */
export function ContactDetailPanel({ contactId, onClose }: { contactId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [label, setLabel] = useState("");
  const [orderStatus, setOrderStatus] = useState<string | null>(null);

  const { data: contact, isLoading } = useQuery({
    queryKey: ["contact", contactId],
    queryFn: () => getContact(contactId),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["contact", contactId] });
    qc.invalidateQueries({ queryKey: ["contacts"] });
  };
  const patch = useMutation({
    mutationFn: (body: Parameters<typeof updateContact>[1]) => updateContact(contactId, body),
    onSuccess: invalidate,
  });
  const labels = useMutation({
    mutationFn: (next: string[]) => setContactLabels(contactId, next),
    onSuccess: invalidate,
  });
  const addNote = useMutation({
    mutationFn: (text: string) => addContactNote(contactId, text),
    onSuccess: () => {
      setNote("");
      invalidate();
    },
  });

  if (isLoading || !contact) {
    return (
      <aside
        aria-label="Contact details"
        className="flex w-[360px] shrink-0 items-center justify-center border-l border-border"
      >
        <Loader2 className="size-5 animate-spin text-accent" />
      </aside>
    );
  }

  const name = contact.display_name || contact.email || contact.phone || "Unnamed contact";
  const primary = contact.channels[0];
  const orderValue = orderStatus ?? contact.order_status ?? "";

  return (
    <aside
      aria-label="Contact details"
      className="flex w-[360px] shrink-0 flex-col overflow-y-auto border-l border-border scroll-thin"
    >
      <div className="flex items-start gap-3 border-b border-border p-4">
        <ContactAvatar
          channel={primary?.channel ?? "manual"}
          name={name}
          avatarUrl={primary?.avatar_url ?? null}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-text">{name}</div>
          <div className="text-xs text-faint">
            {contact.channels.map((c) => channelMeta(c.channel).label).join(" · ") || "No channels yet"}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close contact details"
          className="rounded-md p-1 text-faint hover:bg-surface-2 hover:text-text"
        >
          <X className="size-4" />
        </button>
      </div>

      <section className="space-y-3 border-b border-border p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-faint">About</h3>
        <dl className="space-y-1.5 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-muted">Email</dt>
            <dd className="truncate text-text">{contact.email || "—"}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-muted">Phone</dt>
            <dd className="truncate text-text">{contact.phone || "—"}</dd>
          </div>
          {/* Every handle this person has been recognised on. */}
          {contact.channels.map((c) => (
            <div key={c.id} className="flex justify-between gap-3">
              <dt className="text-muted">{channelMeta(c.channel).label}</dt>
              <dd className="truncate font-mono text-xs text-text">{c.external_id}</dd>
            </div>
          ))}
          <div className="flex justify-between gap-3">
            <dt className="text-muted">Conversations</dt>
            <dd className="text-text">{contact.conversation_count}</dd>
          </div>
        </dl>
      </section>

      <section className="space-y-3 border-b border-border p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-faint">Activity</h3>
        <div>
          <label htmlFor="lead-stage" className="mb-1 block text-xs text-muted">
            Lead stage
          </label>
          <select
            id="lead-stage"
            value={contact.lead_stage ?? ""}
            onChange={(e) => patch.mutate({ lead_stage: e.target.value || null })}
            className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 text-sm capitalize text-text"
          >
            <option value="">Not set</option>
            {LEAD_STAGES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="order-status" className="mb-1 block text-xs text-muted">
            Order status
          </label>
          <div className="flex gap-2">
            <Input
              id="order-status"
              value={orderValue}
              placeholder="e.g. Awaiting stock"
              onChange={(e) => setOrderStatus(e.target.value)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={orderValue === (contact.order_status ?? "")}
              onClick={() => patch.mutate({ order_status: orderValue || null })}
            >
              Save
            </Button>
          </div>
        </div>
      </section>

      <section className="space-y-3 border-b border-border p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-faint">Labels</h3>
        <div className="flex flex-wrap gap-1.5">
          {contact.labels.map((l) => (
            <span
              key={l}
              className="flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs text-text"
            >
              {l}
              <button
                onClick={() => labels.mutate(contact.labels.filter((x) => x !== l))}
                aria-label={`Remove label ${l}`}
                className="text-faint hover:text-error-text"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
          {contact.labels.length === 0 && <span className="text-xs text-faint">No labels yet.</span>}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (label.trim()) {
              labels.mutate([...contact.labels, label.trim()]);
              setLabel("");
            }
          }}
          className="flex gap-2"
        >
          <Input
            aria-label="New label"
            placeholder="Add a label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <Button type="submit" variant="outline" size="sm" aria-label="Add label" disabled={!label.trim()}>
            <Plus className="size-4" />
          </Button>
        </form>
      </section>

      <section className="space-y-3 border-b border-border p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-faint">Notes</h3>
        <ol className="space-y-2">
          {contact.notes.map((n, i) => (
            <li key={i} className="rounded-md border border-border bg-surface-2/50 p-2.5">
              <p className="whitespace-pre-wrap text-sm text-text">{n.text}</p>
              <p className="mt-1 text-[11px] text-faint">{relativeTime(n.at)}</p>
            </li>
          ))}
          {contact.notes.length === 0 && <li className="text-xs text-faint">No notes yet.</li>}
        </ol>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (note.trim()) addNote.mutate(note.trim());
          }}
          className="space-y-2"
        >
          <textarea
            aria-label="New note"
            placeholder="Add a note…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            className="w-full rounded-md border border-border bg-surface-2 p-2 text-sm text-text placeholder:text-faint focus-visible:border-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
          />
          <Button type="submit" variant="outline" size="sm" disabled={!note.trim() || addNote.isPending}>
            {addNote.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Add note
          </Button>
        </form>
      </section>

      <section className="space-y-2 p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-faint">Conversations</h3>
        {contact.conversations.length === 0 && <p className="text-xs text-faint">None yet.</p>}
        {contact.conversations.map((c) => (
          <Link
            key={c.id}
            href={`/inbox/${c.id}`}
            className="block rounded-md border border-border bg-surface-2/50 p-2.5 transition-colors hover:border-border-strong"
          >
            <div className="flex items-center gap-2">
              <span className="truncate text-sm text-text">{c.title || "Conversation"}</span>
              <Badge variant="default" className="ml-auto shrink-0">
                {c.status}
              </Badge>
            </div>
            <div className="text-[11px] text-faint">
              {relativeTime(c.last_message_at ?? c.created_at)}
            </div>
          </Link>
        ))}
      </section>
    </aside>
  );
}
