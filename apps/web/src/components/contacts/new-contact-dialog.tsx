"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { createContact, LEAD_STAGES } from "@/lib/api/contacts";

/**
 * Add a contact by hand.
 *
 * Every other contact appears because someone messaged an agent; this is the one path an
 * operator drives. Channel is fixed to `manual` server-side — there's no platform account
 * behind it, so there's nothing to pick.
 */
export function NewContactDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (id: string) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [stage, setStage] = useState("");
  const [orderStatus, setOrderStatus] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName("");
    setEmail("");
    setPhone("");
    setStage("");
    setOrderStatus("");
    setError(null);
  };

  const create = useMutation({
    mutationFn: () =>
      createContact({
        display_name: name.trim(),
        email: email.trim() || null,
        phone: phone.trim() || null,
        lead_stage: stage || null,
        order_status: orderStatus.trim() || null,
      }),
    onSuccess: (contact) => {
      qc.invalidateQueries({ queryKey: ["contacts"] });
      reset();
      onOpenChange(false);
      onCreated(contact.id);
    },
    onError: (e) => setError((e as Error).message),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New contact</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label htmlFor="contact-name" className="mb-1 block text-xs text-muted">
              Name
            </label>
            <Input
              id="contact-name"
              autoFocus
              placeholder="Aadesh Kumar"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="contact-email" className="mb-1 block text-xs text-muted">
                Email
              </label>
              <Input
                id="contact-email"
                type="email"
                placeholder="aadesh@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="contact-phone" className="mb-1 block text-xs text-muted">
                Phone
              </label>
              <Input
                id="contact-phone"
                placeholder="+91 98765 43210"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="contact-stage" className="mb-1 block text-xs text-muted">
                Lead stage
              </label>
              <select
                id="contact-stage"
                value={stage}
                onChange={(e) => setStage(e.target.value)}
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
              <label htmlFor="contact-order" className="mb-1 block text-xs text-muted">
                Order status
              </label>
              <Input
                id="contact-order"
                placeholder="e.g. Awaiting stock"
                value={orderStatus}
                onChange={(e) => setOrderStatus(e.target.value)}
              />
            </div>
          </div>
          {error && <p className="text-sm text-error-text">{error}</p>}
          <Button type="submit" variant="primary" className="w-full" disabled={create.isPending || !name.trim()}>
            {create.isPending && <Loader2 className="size-4 animate-spin" />} Add contact
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
