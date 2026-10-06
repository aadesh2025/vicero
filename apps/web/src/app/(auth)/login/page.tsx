"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { Mail } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { OAuthButtons, OrDivider } from "@/components/auth/oauth-buttons";
import { AuthField, AuthHeading, FormError, FormNotice, SubmitButton, oauthErrorMessage } from "@/components/auth/form-bits";
import { PasswordInput } from "@/components/auth/password-input";
import { AnimatedLogoTile } from "@/components/brand/animated-logo-mark";
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
    <div>
      <AnimatedLogoTile className="mb-4 hidden lg:block" />
      <AuthHeading
        title="Welcome back."
        sub="Your assistants kept every conversation going while you were away. Sign in to pick them up."
      />

      <OAuthButtons onError={setError} />
      <OrDivider label="or with email" />

      <form onSubmit={onSubmit} className="flex flex-col gap-2.5" noValidate={false}>
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
        <PasswordInput
          id="password"
          label="Password"
          autoComplete="current-password"
          placeholder="••••••••••"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <div className="-mt-1 flex justify-end">
          <Link href="/forgot-password" className="text-xs font-semibold text-accent-2 hover:text-text dark:text-accent">
            Forgot password?
          </Link>
        </div>
        <FormError message={error} />
        {notice && <FormNotice>{notice}</FormNotice>}
        <SubmitButton busy={busy}>Sign in</SubmitButton>
        <button
          type="button"
          disabled={busy}
          onClick={onMagicLink}
          className="flex h-11 items-center justify-center gap-2 rounded-xl border border-dashed border-border-strong text-[13px] font-semibold text-text transition-colors hover:bg-surface-3 disabled:pointer-events-none disabled:opacity-60"
        >
          <Mail className="size-[18px]" strokeWidth={1.8} />
          Email me a sign-in link instead
        </button>
      </form>

      <p className="mt-5 text-center text-[13px] text-muted">
        New to Vicero?{" "}
        <Link href="/signup" className="font-semibold text-accent-2 hover:text-text dark:text-accent">
          Start your free trial
        </Link>
      </p>
    </div>
  );
}
