"use client";

import { RedeemToken } from "@/components/auth/redeem-token";
import { verifyOAuthEmail } from "@/lib/api/auth";

export default function OAuthVerifyPage() {
  return (
    <RedeemToken
      title="Finishing sign-in"
      working="Confirming your email…"
      redeem={verifyOAuthEmail}
      successMessage="Signed in."
      signIn
    />
  );
}
