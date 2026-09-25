import { Sidebar } from "@/components/shell/sidebar";
import { Topbar } from "@/components/shell/topbar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/components/auth/auth-gate";
import { TrialBanner } from "@/components/plan/trial-banner";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <TooltipProvider delayDuration={200}>
        <div className="flex min-h-screen bg-bg">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <Topbar />
            <TrialBanner />
            <main className="flex-1 px-4 py-6 md:px-6 lg:px-8">{children}</main>
          </div>
        </div>
      </TooltipProvider>
    </AuthGate>
  );
}
