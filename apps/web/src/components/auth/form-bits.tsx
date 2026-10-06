"use client";

import { Loader2 } from "lucide-react";
import { BrandArrow } from "@/components/brand/animated-logo-mark";
import { cn } from "@/lib/utils";

/** Inline error banner used by every auth form. `role="alert"` so it is announced. */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-xl border border-error/30 bg-error/10 px-3 py-2 text-sm text-error-text">
      {message}
    </p>
  );
}

export function FormNotice({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="rounded-xl border border-success/30 bg-success/10 px-3 py-2 text-sm text-text">
      {children}
    </p>
  );
}

/** Primary action: inverse of the theme (near-black on light, white on dark) with the brand arrow. */
export function SubmitButton({ busy, children }: { busy: boolean; children: React.ReactNode }) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="flex h-[46px] w-full items-center justify-center gap-2.5 rounded-xl bg-text text-[15px] font-bold text-bg transition-colors hover:bg-text/90 disabled:pointer-events-none disabled:opacity-60"
    >
      {busy && <Loader2 className="size-4 animate-spin" />}
      {children}
      <BrandArrow />
    </button>
  );
}

/** A text field with its label inside the box. The label is a real `<label htmlFor>`; the box
 *  lights up when the input (or the optional `trailing` control) has focus. */
export function AuthField({
  id,
  label,
  trailing,
  className,
  ...input
}: React.InputHTMLAttributes<HTMLInputElement> & { id: string; label: string; trailing?: React.ReactNode }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-xl border border-border-strong bg-surface py-0.5 pl-3.5 pr-1 transition-shadow focus-within:border-accent-strong focus-within:ring-4 focus-within:ring-accent/15",
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col py-1.5">
        <label htmlFor={id} className="text-[11px] font-semibold text-muted">
          {label}
        </label>
        <input
          id={id}
          {...input}
          className="h-6 w-full border-0 bg-transparent p-0 text-[15px] text-text placeholder:text-faint focus-visible:outline-none read-only:text-muted"
        />
      </div>
      {trailing}
    </div>
  );
}

/** Heading + subline shared by every auth page. */
export function AuthHeading({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-5">
      <h1 className="font-display text-[26px] font-extrabold leading-[1.1] tracking-[-0.02em] text-text lg:text-[30px]">{title}</h1>
      {sub && <p className="mt-2 text-sm leading-[1.5] text-muted">{sub}</p>}
    </div>
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
