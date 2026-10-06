"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { oauthAuthorizeUrl, type OAuthProvider } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

const PROVIDERS: { id: OAuthProvider; label: string; mark: React.ReactNode }[] = [
  {
    id: "google",
    label: "Google",
    mark: (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="size-[18px]">
        <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8Z" />
        <path fill="#34A853" d="M12 24c3.2 0 6-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9H1.4v3.1A12 12 0 0 0 12 24Z" />
        <path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.8V6.5H1.4a12 12 0 0 0 0 11l4-3.1Z" />
        <path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.4 6.5l4 3.1C6.3 6.9 8.9 4.8 12 4.8Z" />
      </svg>
    ),
  },
  {
    id: "facebook",
    label: "Facebook",
    mark: (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="size-[18px]">
        <path fill="#1877F2" d="M24 12a12 12 0 1 0-13.9 11.9v-8.4H7.1V12h3V9.4c0-3 1.8-4.7 4.5-4.7 1.3 0 2.7.2 2.7.2v3h-1.5c-1.5 0-2 .9-2 1.9V12h3.4l-.5 3.5h-2.9v8.4A12 12 0 0 0 24 12Z" />
      </svg>
    ),
  },
];

/** "Continue with Google / Facebook". Sends the browser to the provider; the API's callback
 * brings it back to `/oauth/callback` with a one-time code. */
export function OAuthButtons({ onError }: { onError: (message: string) => void }) {
  const [busy, setBusy] = useState<OAuthProvider | null>(null);

  async function start(provider: OAuthProvider) {
    setBusy(provider);
    try {
      window.location.assign(await oauthAuthorizeUrl(provider));
    } catch (err) {
      setBusy(null);
      onError(
        err instanceof ApiError && err.status === 501
          ? `${provider === "google" ? "Google" : "Facebook"} sign-in isn't set up on this server yet.`
          : "Couldn't reach the sign-in provider. Try again.",
      );
    }
  }

  return (
    <div className="grid grid-cols-2 gap-3">
      {PROVIDERS.map((p) => (
        <Button
          key={p.id}
          type="button"
          variant="outline"
          className="h-11 gap-2 rounded-xl border-border-strong bg-surface text-sm font-semibold hover:bg-surface-3 [&_svg]:size-4"
          disabled={busy !== null}
          onClick={() => start(p.id)}
          aria-label={`Continue with ${p.label}`}
        >
          {busy === p.id ? <Loader2 className="size-4 animate-spin" /> : p.mark}
          {p.label}
        </Button>
      ))}
    </div>
  );
}

export function OrDivider({ label = "or" }: { label?: string }) {
  return (
    <div className="my-4 flex items-center gap-3 text-xs font-semibold uppercase tracking-[0.08em] text-faint" role="separator">
      <span className="h-px flex-1 bg-border" />
      {label}
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
