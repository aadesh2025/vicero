"use client";

import { useState } from "react";
import Link from "next/link";
import { Mail } from "lucide-react";
import { useRouter } from "next/navigation";
import { OAuthButtons, OrDivider } from "@/components/auth/oauth-buttons";
import { AuthField, AuthHeading, FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
import { PasswordInput } from "@/components/auth/password-input";
import { AnimatedLogoTile } from "@/components/brand/animated-logo-mark";
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
    <div>
      <AnimatedLogoTile className="mb-7 hidden lg:block" />
      <AuthHeading title="Start your free trial." sub="10 days, 500 messages, no card needed." />

      <OAuthButtons onError={setError} />
      <OrDivider label="or with email" />

      <form onSubmit={onSubmit} className="flex flex-col gap-3.5">
        <AuthField id="name" label="Full name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        <AuthField
          id="email"
          label="Work email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <div>
          <PasswordInput
            id="password"
            label="Password"
            autoComplete="new-password"
            placeholder="••••••••••"
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
        {notice && <FormNotice>{notice}</FormNotice>}
        <SubmitButton busy={busy}>Create account</SubmitButton>
        <button
          type="button"
          disabled={busy}
          onClick={onMagicLink}
          className="flex h-12 items-center justify-center gap-2 rounded-[14px] border border-dashed border-border-strong text-sm font-semibold text-text transition-colors hover:bg-surface-3 disabled:pointer-events-none disabled:opacity-60"
        >
          <Mail className="size-[18px]" strokeWidth={1.8} />
          Sign up with an email link instead
        </button>
      </form>

      <p className="mt-6 text-center text-sm text-muted">
        Already have an account?{" "}
        <Link href="/login" className="font-semibold text-accent-2 hover:text-text dark:text-accent">
          Sign in
        </Link>
      </p>
    </div>
  );
}
