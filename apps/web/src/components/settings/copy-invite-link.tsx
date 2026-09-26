"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, Copy, Link2, Loader2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { createInvitationLink } from "@/lib/api/orgs";

/** Hand an invited client their acceptance link directly.
 *
 * The invitation email is queued automatically, but it can fail to arrive — a spam filter, a
 * mistyped address, or (the common case in a fresh deployment) `EMAIL_BACKEND=console`, which
 * delivers to nobody. Without this an admin can invite someone and have no way to let them in.
 *
 * It is behind a confirmation because generating a link is **not** read-only: tokens are stored
 * hashed and cannot be read back, so issuing one mints a new token and stops the previously
 * sent link from working. Doing that silently would break a link the client may already hold.
 */
export function CopyInviteLink({
  orgId,
  invitationId,
  email,
}: {
  orgId: string;
  invitationId: string;
  email: string;
}) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () => createInvitationLink(orgId, invitationId),
    onSuccess: async (data) => {
      setUrl(data.accept_url);
      await copy(data.accept_url);
    },
    onError: (e: Error) => setError(e.message),
  });

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      // Clipboard access needs a secure context and can be denied outright. The link is shown
      // in a selectable field regardless, so a refusal costs a manual copy, not the link.
      setCopied(false);
    }
  }

  function close(next: boolean) {
    setOpen(next);
    if (!next) {
      setUrl(null);
      setCopied(false);
      setError(null);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text"
        title="Copy invite link"
        aria-label={`Copy invite link for ${email}`}
      >
        <Link2 className="size-4" />
      </button>

      <Dialog open={open} onOpenChange={close}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite link for {email}</DialogTitle>
          </DialogHeader>

          {url === null ? (
            <div className="space-y-4">
              <p className="flex items-start gap-2 text-sm text-muted">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn-text" />
                <span>
                  Generating a link <strong className="text-text">replaces any link already
                  sent</strong> to this address — an earlier email will stop working. The new link
                  is valid for 7 days.
                </span>
              </p>
              {error && (
                <p role="alert" className="text-xs text-error-text">
                  {error}
                </p>
              )}
              <Button
                variant="primary"
                className="w-full"
                disabled={generate.isPending}
                onClick={() => {
                  setError(null);
                  generate.mutate();
                }}
              >
                {generate.isPending ? <Loader2 className="size-4 animate-spin" /> : <Link2 className="size-4" />}
                Generate &amp; copy link
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted">
                Send this to {email}. They&apos;ll set their own password and join.
              </p>
              <div className="flex items-center gap-2">
                <Input readOnly value={url} onFocus={(e) => e.currentTarget.select()} className="font-mono text-xs" />
                <Button variant="outline" onClick={() => copy(url)} aria-label="Copy link">
                  {copied ? <Check className="size-4 text-success-text" /> : <Copy className="size-4" />}
                </Button>
              </div>
              <p className="text-xs text-faint">
                {copied ? "Copied to clipboard." : "Select the link above to copy it."}
              </p>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
