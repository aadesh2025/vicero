"use client";

import { useState } from "react";
import Link from "next/link";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
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
    <div className="rounded-xl border border-border bg-surface p-6 shadow-pop">
      <h1 className="font-display text-xl font-semibold text-text">Reset your password</h1>
      <p className="mt-1 text-sm text-muted">We&apos;ll email you a link to choose a new one.</p>
      {sent ? (
        <div className="mt-6">
          {/* Identical for every address: the API never says whether an account exists. */}
          <FormNotice>If that address has an account, a reset link is on its way.</FormNotice>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <FormError message={error} />
          <SubmitButton busy={busy}>Send reset link</SubmitButton>
        </form>
      )}
      <p className="mt-5 text-center text-sm text-muted">
        <Link href="/login" className="font-medium text-accent-soft hover:text-accent">
          Back to sign in
        </Link>
      </p>
    </div>
  );
}
