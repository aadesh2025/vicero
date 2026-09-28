"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BarChart3, Plus } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { DashboardStats } from "@/components/dashboard/dashboard-stats";
import { AgentsPanel } from "@/components/dashboard/agents-panel";
import { ConversationsPanel } from "@/components/dashboard/conversations-panel";
import { NewAgentDialog, type NewAgentSubmit } from "@/components/agents/new-agent-dialog";
import { Button } from "@/components/ui/button";
import { createAgent } from "@/lib/api/agents";

export default function DashboardPage() {
  const router = useRouter();
  const [creating, setCreating] = useState(false);

  // Same flow as /agents: create, then land on the Persona tab so whatever the template
  // filled in is the first thing to review. Both buttons used to be inert.
  async function onCreate({ name, templateId }: NewAgentSubmit) {
    const agent = await createAgent(name, { templateId: templateId ?? undefined });
    router.push(`/agents/${agent.id}?tab=persona`);
  }

  return (
    <div className="space-y-3">
      {/* Header with a single restrained accent glow. Tightened padding (2026-09-28 feedback:
          Today + KPIs + Activity should fit without scrolling through the chart). */}
      <div className="relative -mx-4 -mt-6 overflow-hidden px-4 pt-3 md:-mx-6 md:px-6 lg:-mx-8 lg:px-8">
        <div className="glow-accent pointer-events-none absolute inset-0 -z-10" />
        <PageHeader
          title="Dashboard"
          description="Everything your agents did in the last 30 days, at a glance."
        >
          <Button variant="secondary" size="default" asChild>
            <Link href="/analytics">
              <BarChart3 /> View reports
            </Link>
          </Button>
          <Button variant="primary" size="default" onClick={() => setCreating(true)}>
            <Plus /> New agent
          </Button>
        </PageHeader>
      </div>

      {/* Stat row, activity chart, per-channel and per-agent breakdowns — all live */}
      <DashboardStats />

      {/* Two-column body */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <AgentsPanel />
        </div>
        <div className="xl:col-span-1">
          <ConversationsPanel />
        </div>
      </div>

      <NewAgentDialog open={creating} onOpenChange={setCreating} onSubmit={onCreate} />
    </div>
  );
}
