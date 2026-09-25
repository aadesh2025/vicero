"use client";

import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Inline error banner used by every auth form. `role="alert"` so it is announced. */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-md border border-error/30 bg-error/10 px-3 py-2 text-sm text-error">
      {message}
    </p>
  );
}

export function FormNotice({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="rounded-md border border-success/30 bg-success/10 px-3 py-2 text-sm text-text">
      {children}
    </p>
  );
}

export function SubmitButton({ busy, children }: { busy: boolean; children: React.ReactNode }) {
  return (
    <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy}>
      {busy && <Loader2 className="size-4 animate-spin" />} {children}
    </Button>
  );
}

/** Human wording for the sign-in error codes the API redirects back to `/login?error=`. */
export function oauthErrorMessage(code: string | null): string | null {
  switch (code) {
    case null:
    case "":
      return null;
    case "auth.oauth_email_unverified":
      return "An account with this email exists but hasn't been verified. Verify it from the email we sent when you signed up, or sign in with your password.";
    case "auth.oauth_cancelled":
      return "Sign-in was cancelled.";
    case "auth.email_not_allowed":
      return "Please use a permanent email address.";
    case "auth.signup_rate_limited":
      return "Too many accounts have been created from this network today. Try again tomorrow.";
    case "auth.account_inactive":
      return "This account is disabled.";
    default:
      return "We couldn't complete that sign-in. Try again, or use email instead.";
  }
}
