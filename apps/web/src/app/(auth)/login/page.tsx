"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OAuthButtons, OrDivider } from "@/components/auth/oauth-buttons";
import { FormError, FormNotice, SubmitButton, oauthErrorMessage } from "@/components/auth/form-bits";
import { login, requestMagicLink } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(oauthErrorMessage(params.get("error")));
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await login(email, password);
      router.replace(params.get("next") || "/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  async function onMagicLink() {
    if (!email) {
      setError("Enter your email above, then we'll send you a sign-in link.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await requestMagicLink(email);
      // Same wording whether or not the address has an account — the API never says.
      setNotice("If that address can sign in, a link is on its way. It expires in 15 minutes.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6 shadow-pop">
      <h1 className="font-display text-xl font-semibold text-text">Welcome back</h1>
      <p className="mt-1 text-sm text-muted">Sign in to your BotForge workspace.</p>

      <div className="mt-6">
        <OAuthButtons onError={setError} />
      </div>
      <OrDivider label="or with email" />

      <form onSubmit={onSubmit} className="space-y-4" noValidate={false}>
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="password">Password</Label>
            <Link href="/forgot-password" className="text-xs text-accent-soft hover:text-accent">
              Forgot password?
            </Link>
          </div>
          <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <FormError message={error} />
        {notice && <FormNotice>{notice}</FormNotice>}
        <SubmitButton busy={busy}>Sign in</SubmitButton>
        <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={onMagicLink}>
          Email me a sign-in link instead
        </Button>
      </form>

      <p className="mt-5 text-center text-sm text-muted">
        New here?{" "}
        <Link href="/signup" className="font-medium text-accent-soft hover:text-accent">
          Start your free trial
        </Link>
      </p>
    </div>
  );
}
