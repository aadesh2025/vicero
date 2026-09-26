"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
import { exchangeOAuthCode, requestOAuthEmail } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { landingPath } from "@/lib/auth-landing";

/** Where the API sends the browser after a provider sign-in.
 *
 * Two shapes: `?code=` (signed in — trade it for a session) or `#pending=` (the provider gave no
 * verified email, so ask for one and mail a link to it). The pending token rides in the URL
 * fragment, which browsers never send to servers or in Referer headers.
 */
export default function OAuthCallbackPage() {
  return (
    <Suspense>
      <Callback />
    </Suspense>
  );
}

function Callback() {
  const router = useRouter();
  const code = useSearchParams().get("code");
  const started = useRef(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("pending");
    if (fragment) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPending(fragment);
      history.replaceState(null, "", window.location.pathname);
      return;
    }
    if (!code) {
      setError("This sign-in link is incomplete. Try signing in again.");
      return;
    }
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        await exchangeOAuthCode(code);
        router.replace(await landingPath());
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Something went wrong. Try signing in again.");
      }
    })();
  }, [code, router]);

  if (pending) return <NeedEmail pendingToken={pending} />;

  return (
    <div className="rounded-xl border border-border bg-surface p-6 text-center shadow-pop">
      <h1 className="font-display text-xl font-semibold text-text">Signing you in</h1>
      {error ? (
        <>
          <p role="alert" className="mt-3 text-sm text-error-text">
            {error}
          </p>
          <Link href="/login" className="mt-4 inline-block text-sm font-medium text-accent hover:text-accent">
            Back to sign in
          </Link>
        </>
      ) : (
        <p role="status" className="mt-3 flex items-center justify-center gap-2 text-sm text-muted">
          <Loader2 className="size-4 animate-spin" /> One moment…
        </p>
      )}
    </div>
  );
}

function NeedEmail({ pendingToken }: { pendingToken: string }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await requestOAuthEmail(pendingToken, email);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6 shadow-pop">
      <h1 className="font-display text-xl font-semibold text-text">One more step</h1>
      <p className="mt-1 text-sm text-muted">
        The provider didn&apos;t share a verified email address. Enter the one you want to use and we&apos;ll email
        you a link to confirm it.
      </p>
      {sent ? (
        <div className="mt-6">
          <FormNotice>Check your inbox for the confirmation link. It expires in 15 minutes.</FormNotice>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <FormError message={error} />
          <SubmitButton busy={busy}>Email me the link</SubmitButton>
        </form>
      )}
    </div>
  );
}
