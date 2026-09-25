"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OAuthButtons, OrDivider } from "@/components/auth/oauth-buttons";
import { FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
import { requestMagicLink, signup } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

export default function SignupPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await signup(email, password, name || undefined);
      // A brand-new workspace has no agent yet, so onboarding is next.
      router.replace("/onboarding");
    } catch (err) {
      if (err instanceof ApiError && err.code === "auth.email_taken") {
        setError("An account with this email already exists. Sign in instead, or reset your password.");
      } else {
        setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      }
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
      setNotice("Check your email for a link to finish creating your account. It expires in 15 minutes.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6 shadow-pop">
      <h1 className="font-display text-xl font-semibold text-text">Start your free trial</h1>
      <p className="mt-1 text-sm text-muted">10 days, 500 messages, no card needed.</p>

      <div className="mt-6">
        <OAuthButtons onError={setError} />
      </div>
      <OrDivider label="or with email" />

      <form onSubmit={onSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="name">Full name</Label>
          <Input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p id="password-hint" className="text-xs text-faint">
            At least 8 characters, and nothing easy to guess.
          </p>
        </div>
        <FormError message={error} />
        {notice && <FormNotice>{notice}</FormNotice>}
        <SubmitButton busy={busy}>Create account</SubmitButton>
        <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={onMagicLink}>
          Sign up with an email link instead
        </Button>
      </form>

      <p className="mt-5 text-center text-sm text-muted">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-accent-soft hover:text-accent">
          Sign in
        </Link>
      </p>
    </div>
  );
}
