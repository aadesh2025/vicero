"use client";

import { RedeemToken } from "@/components/auth/redeem-token";
import { verifyMagicLink } from "@/lib/api/auth";

export default function MagicLinkPage() {
  return (
    <RedeemToken
      title="Signing you in"
      working="Checking your sign-in link…"
      redeem={verifyMagicLink}
      successMessage="Signed in."
      signIn
    />
  );
}
