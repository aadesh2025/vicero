"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AuthHeading, FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
import { PasswordInput } from "@/components/auth/password-input";
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
    <div>
      <AuthHeading title="Choose a new password" />
      {done ? (
        <div className="space-y-4">
          <FormNotice>Your password is updated. Sign in with it now.</FormNotice>
          <Link href="/login" className="block text-center text-sm font-semibold text-accent-2 hover:text-text dark:text-accent">
            Go to sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="flex flex-col gap-3.5">
          <div>
            <PasswordInput
              id="password"
              label="New password"
              autoComplete="new-password"
              required
              minLength={8}
              aria-describedby="password-hint"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p id="password-hint" className="mt-1.5 px-1 text-xs text-faint">
              At least 8 characters, and nothing easy to guess.
            </p>
          </div>
          <FormError message={error} />
          <SubmitButton busy={busy}>Update password</SubmitButton>
        </form>
      )}
    </div>
  );
}
