import type { Metadata } from "next";
import Link from "next/link";
import { DocsNav } from "@/components/docs/docs-nav";

export const metadata: Metadata = {
  title: { default: "Vicero docs", template: "%s — Vicero docs" },
  description:
    "Build AI agents, ground them in your own knowledge base, embed a chat widget and wire automations.",
};

/**
 * Public chrome for the documentation site.
 *
 * This route group sits outside `(app)`, so it gets no `AuthGate`, no sidebar and no org
 * context — these pages are readable signed out, which is the point of them.
 */
export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-bg">
      <DocsNav />
      {children}
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-2 px-4 py-8 text-sm text-muted sm:flex-row sm:items-center md:px-6">
          <p>Vicero — build, deploy and operate AI agents.</p>
          <nav className="flex gap-4 sm:ml-auto">
            <Link href="/docs" className="transition-colors hover:text-text">
              Docs
            </Link>
            <Link href="/docs/api/authentication" className="transition-colors hover:text-text">
              API
            </Link>
            <Link href="/login" className="transition-colors hover:text-text">
              Sign in
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
