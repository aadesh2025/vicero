"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { AuthHeading } from "@/components/auth/form-bits";
import { ApiError } from "@/lib/api/client";
import { landingPath } from "@/lib/auth-landing";

interface RedeemProps {
  title: string;
  working: string;
  /** Redeems the token. */
  redeem: (token: string) => Promise<unknown>;
  successMessage: string;
  /** The redemption signs the user in: continue to onboarding/dashboard instead of showing a link. */
  signIn?: boolean;
}

/** A page reached from an emailed link: redeem the `?token=` once, then say what happened.
 *
 * Tokens are single-use, and React runs effects twice in development, so the redemption is
 * guarded by a ref — without it the second call would fail and overwrite the success message.
 */
export function RedeemToken(props: RedeemProps) {
  return (
    <Suspense>
      <Inner {...props} />
    </Suspense>
  );
}

function Inner({ title, working, redeem, successMessage, signIn = false }: RedeemProps) {
  const router = useRouter();
  const token = useSearchParams().get("token");
  const started = useRef(false);
  const [state, setState] = useState<"working" | "done" | "error">(token ? "working" : "error");
  const [message, setMessage] = useState(token ? working : "This link is missing its token.");

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    (async () => {
      try {
        await redeem(token);
        if (signIn) {
          router.replace(await landingPath());
          return;
        }
        setState("done");
        setMessage(successMessage);
      } catch (err) {
        setState("error");
        setMessage(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      }
    })();
  }, [token, redeem, router, signIn, successMessage]);

  return (
    <div>
      <AuthHeading title={title} />
      <p
        role={state === "error" ? "alert" : "status"}
        className="flex items-center gap-2 text-sm text-muted"
      >
        {state === "working" && <Loader2 className="size-4 animate-spin" />}
        {message}
      </p>
      {state !== "working" && (
        <Link
          href={state === "done" ? "/dashboard" : "/login"}
          className="mt-5 flex h-[54px] items-center justify-center rounded-[14px] bg-text text-base font-bold text-bg transition-colors hover:bg-text/90"
        >
          {state === "done" ? "Continue" : "Back to sign in"}
        </Link>
      )}
    </div>
  );
}
