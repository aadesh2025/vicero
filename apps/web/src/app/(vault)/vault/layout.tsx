import type { Metadata } from "next";
import { Lock } from "lucide-react";
import { Logo } from "@/components/brand/logo";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { VaultLogoutButton } from "@/components/vault/logout-button";
import { getVaultSession } from "@/lib/vault/session";

export const metadata: Metadata = {
  title: { default: "Private", template: "%s — Vicero private" },
  // Not a control — a crawler that ignores it still finds nothing without a session — but
  // there is no reason for this URL to appear in a search index.
  robots: { index: false, follow: false, nocache: true },
};

/**
 * Chrome for the private area.
 *
 * Its own tree, outside `(app)`: no `AuthGate`, no Vicero sidebar, no org switcher, and no
 * call to the Vicero API. The two logins share a host and nothing else.
 */
export default async function VaultLayout({ children }: { children: React.ReactNode }) {
  const email = await getVaultSession();

  return (
    <div className="min-h-screen bg-bg">
      <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4 md:px-6">
          <Logo />
          <span className="inline-flex items-center gap-1 rounded border border-border bg-surface px-2 py-0.5 text-xs text-muted">
            <Lock className="size-3" aria-hidden /> Private
          </span>
          <div className="ml-auto flex items-center gap-1">
            {email && <span className="hidden text-sm text-muted sm:inline">{email}</span>}
            {email && <VaultLogoutButton />}
            <ThemeToggle />
          </div>
        </div>
      </header>
      {children}
    </div>
  );
}
