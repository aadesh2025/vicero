"use client";

import { RedeemToken } from "@/components/auth/redeem-token";
import { verifyEmail } from "@/lib/api/auth";

export default function VerifyEmailPage() {
  return (
    <RedeemToken
      title="Verify your email"
      working="Confirming your address…"
      redeem={verifyEmail}
      successMessage="Your email is verified. You can now publish your agent to live channels."
    />
  );
}
