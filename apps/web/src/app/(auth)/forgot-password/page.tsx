"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthField, AuthHeading, FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
import { forgotPassword } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <AuthHeading title="Reset your password" sub="We'll email you a link to choose a new one." />
      {sent ? (
        <div>
          {/* Identical for every address: the API never says whether an account exists. */}
          <FormNotice>If that address has an account, a reset link is on its way.</FormNotice>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="flex flex-col gap-3.5">
          <AuthField id="email" label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <FormError message={error} />
          <SubmitButton busy={busy}>Send reset link</SubmitButton>
        </form>
      )}
      <p className="mt-6 text-center text-sm text-muted">
        <Link href="/login" className="font-semibold text-accent-2 hover:text-text dark:text-accent">
          Back to sign in
        </Link>
      </p>
    </div>
  );
}
