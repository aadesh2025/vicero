"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
import { resetPassword } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetForm />
    </Suspense>
  );
}

function ResetForm() {
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(token ? null : "This link is missing its token.");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    setBusy(true);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6 shadow-pop">
      <h1 className="font-display text-xl font-semibold text-text">Choose a new password</h1>
      {done ? (
        <div className="mt-6 space-y-4">
          <FormNotice>Your password is updated. Sign in with it now.</FormNotice>
          <Link href="/login" className="block text-center text-sm font-medium text-accent-soft hover:text-accent">
            Go to sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="password">New password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="text-xs text-faint">At least 8 characters, and nothing easy to guess.</p>
          </div>
          <FormError message={error} />
          <SubmitButton busy={busy}>Update password</SubmitButton>
        </form>
      )}
    </div>
  );
}
