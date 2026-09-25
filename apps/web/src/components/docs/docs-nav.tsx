import Link from "next/link";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { Logo } from "@/components/brand/logo";

/**
 * Top bar for the public docs.
 *
 * This is the product's first public-facing chrome — nothing existed above the `(auth)`
 * layer before it — so it deliberately stays minimal: wordmark, the two destinations a
 * reader actually wants, and the theme toggle the rest of the app already has.
 */
export function DocsNav() {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4 md:px-6">
        <Link href="/docs" className="flex items-center gap-2" aria-label="BotForge documentation">
          <Logo />
          <span className="hidden text-sm text-faint sm:inline">docs</span>
        </Link>

        <div className="ml-auto flex items-center gap-1">
          <Link
            href="/docs/api/reference"
            className="hidden rounded-md px-3 py-1.5 text-sm text-muted transition-colors hover:bg-surface-2 hover:text-text sm:block"
          >
            API reference
          </Link>
          <Link
            href="/login"
            className="rounded-md px-3 py-1.5 text-sm text-muted transition-colors hover:bg-surface-2 hover:text-text"
          >
            Sign in
          </Link>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
