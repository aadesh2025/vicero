import Link from "next/link";
import { Logo } from "@/components/brand/logo";
import { AuthShowcase } from "@/components/auth/auth-showcase";
import { MiniChatCard } from "@/components/auth/mini-chat-card";

const DOT_GRID = {
  backgroundImage: "radial-gradient(rgb(var(--border-strong) / 0.7) 1px, transparent 1px)",
  backgroundSize: "22px 22px",
} as const;

/** Split frame for every auth page: the form on the left, an always-dark chat preview on the
 *  right (hidden below `lg`, where a two-bubble card replaces it). */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen gap-5 bg-bg p-5" style={DOT_GRID}>
      <div className="flex min-w-0 flex-1 basis-[400px] flex-col px-1 py-3 sm:px-7 sm:py-5">
        <Link href="/" aria-label="Vicero home" className="self-start">
          <Logo />
        </Link>
        <div className="flex flex-1 items-center justify-center py-6">
          <div className="w-full max-w-[360px]">
            <MiniChatCard />
            {children}
          </div>
        </div>
        <nav aria-label="Help" className="flex gap-[18px] text-xs">
          <Link href="/docs" className="font-medium text-muted hover:text-text">
            Help
          </Link>
          <Link href="/pricing" className="font-medium text-muted hover:text-text">
            Pricing
          </Link>
        </nav>
      </div>
      <AuthShowcase />
    </div>
  );
}
