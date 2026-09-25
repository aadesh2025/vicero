"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * The private area's sign-in.
 *
 * Posts straight to `/api/vault/login` — deliberately not through `lib/api/client`, which
 * attaches BotForge tokens and retries through the BotForge refresh flow. Nothing here
 * touches a BotForge session, and a BotForge session is worth nothing here.
 */
export function VaultLoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/vault/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (res.ok) {
        // A full navigation, not a client transition: the gated page is a server component
        // that has to be rendered fresh now that the cookie exists.
        router.replace("/vault");
        router.refresh();
        return;
      }
      const data = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
      setError(data?.error?.message ?? "Sign-in failed.");
    } catch {
      setError("Could not reach the server. Try again.");
    }
    setPassword("");
    setBusy(false);
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div className="space-y-1.5">
        <Label htmlFor="vault-email">Admin email</Label>
        <Input
          id="vault-email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="vault-password">Password</Label>
        <Input
          id="vault-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {error && (
        <p role="alert" className="rounded-md border border-error/30 bg-error/10 px-3 py-2 text-sm text-error">
          {error}
        </p>
      )}
      <Button type="submit" variant="primary" className="w-full" disabled={busy || !email || !password}>
        {busy ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
