"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { NewAgentDialog, type NewAgentSubmit } from "@/components/agents/new-agent-dialog";
import { createAgent } from "@/lib/api/agents";
import { useSession } from "@/lib/store/session";

/** First stop after signing up: a workspace exists, an agent doesn't. One clear action. */
export default function OnboardingPage() {
  const router = useRouter();
  const user = useSession((s) => s.user);
  const [open, setOpen] = useState(false);

  async function onCreate({ name, templateId }: NewAgentSubmit) {
    const agent = await createAgent(name, { templateId: templateId ?? undefined });
    router.replace(`/agents/${agent.id}?tab=persona`);
  }

  const first = user?.full_name?.split(" ")[0];

  return (
    <div className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center text-center">
      <span className="grid size-14 place-items-center rounded-xl border border-border bg-surface-2 text-accent-soft">
        <Bot className="size-7" />
      </span>
      <h1 className="mt-6 font-display text-3xl font-semibold tracking-tight text-text">
        {first ? `Welcome, ${first}.` : "Welcome to BotForge."}
      </h1>
      <p className="mt-2 text-muted">
        Your workspace is ready and your 10-day free trial has started. Create your first agent — pick a role
        to start from, or begin with a blank one.
      </p>
      <Button variant="primary" size="lg" className="mt-8" onClick={() => setOpen(true)}>
        <Sparkles /> Create your first agent
      </Button>
      <NewAgentDialog open={open} onOpenChange={setOpen} onSubmit={onCreate} />
    </div>
  );
}
