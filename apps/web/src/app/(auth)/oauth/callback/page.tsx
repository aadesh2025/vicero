"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { AuthField, AuthHeading, FormError, FormNotice, SubmitButton } from "@/components/auth/form-bits";
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
    <div>
      <AuthHeading title="Signing you in" />
      {error ? (
        <>
          <p role="alert" className="text-sm text-error-text">
            {error}
          </p>
          <Link href="/login" className="mt-4 inline-block text-sm font-semibold text-accent-2 hover:text-text dark:text-accent">
            Back to sign in
          </Link>
        </>
      ) : (
        <p role="status" className="flex items-center gap-2 text-sm text-muted">
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
    <div>
      <AuthHeading
        title="One more step"
        sub="The provider didn't share a verified email address. Enter the one you want to use and we'll email you a link to confirm it."
      />
      {sent ? (
        <div>
          <FormNotice>Check your inbox for the confirmation link. It expires in 15 minutes.</FormNotice>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="flex flex-col gap-3.5">
          <AuthField id="email" label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <FormError message={error} />
          <SubmitButton busy={busy}>Email me the link</SubmitButton>
        </form>
      )}
    </div>
  );
}
