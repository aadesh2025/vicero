import Link from "next/link";
import { Logo } from "@/components/brand/logo";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { LEGAL_LINKS } from "@/lib/legal";
import { PRODUCT_NAME } from "@/lib/brand";

/**
 * Public chrome for the legal pages.
 *
 * Outside `(app)` and `(auth)`, so no `AuthGate`, no org context, and `proxy.ts` never matches
 * these paths: Meta's App Review (and anyone else) must be able to read them signed out.
 */
export default function LegalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-3 px-4 md:px-6">
          <Link href="/" aria-label={`${PRODUCT_NAME} home`}>
            <Logo />
          </Link>
          <div className="ml-auto">
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-12 md:px-6">{children}</main>
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-3xl flex-col gap-2 px-4 py-8 text-sm text-muted sm:flex-row sm:items-center md:px-6">
          <p>© {PRODUCT_NAME}</p>
          <nav aria-label="Legal" className="flex flex-wrap gap-x-4 gap-y-1 sm:ml-auto">
            {LEGAL_LINKS.map((l) => (
              <Link key={l.href} href={l.href} className="transition-colors hover:text-text">
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
      </footer>
    </div>
  );
}
