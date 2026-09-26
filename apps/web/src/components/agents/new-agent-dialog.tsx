"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Bot,
  CalendarClock,
  ClipboardList,
  LifeBuoy,
  Loader2,
  PenLine,
  Target,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { listAgentTemplates } from "@/lib/api/agents";
import type { ApiAgentTemplate } from "@/lib/api/types";
import { useSession } from "@/lib/store/session";

/** Lucide names the backend catalog may reference. A template naming an icon we don't ship
 *  falls back to the generic bot rather than crashing the picker. */
const ICONS: Record<string, LucideIcon> = {
  LifeBuoy,
  Target,
  CalendarClock,
  ClipboardList,
};

export interface NewAgentSubmit {
  name: string;
  /** null = start from scratch (today's blank agent). */
  templateId: string | null;
}

export function NewAgentDialog({
  open,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (values: NewAgentSubmit) => Promise<void>;
}) {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [step, setStep] = useState<"template" | "name">("template");
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: templates, isLoading } = useQuery({
    queryKey: ["agent-templates"],
    queryFn: listAgentTemplates,
    enabled: Boolean(activeOrgId) && open,
    staleTime: Infinity, // static catalog
  });

  function reset() {
    setStep("template");
    setTemplateId(null);
    setName("");
    setError(null);
  }

  function handleOpenChange(next: boolean) {
    // Reopening should start at the picker, not wherever the last attempt was abandoned.
    if (!next) reset();
    onOpenChange(next);
  }

  function pick(id: string | null, suggestedName: string) {
    setTemplateId(id);
    // Pre-fill with the role's label so the common case is one click and Enter, still editable.
    setName(suggestedName);
    setStep("name");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ name: name.trim(), templateId });
    } catch (err) {
      // Leave the dialog open with the values intact — retyping a name after a failed
      // create is pure friction.
      setError(err instanceof Error ? err.message : "Couldn't create the agent.");
    } finally {
      setBusy(false);
    }
  }

  const chosen = templates?.find((t) => t.id === templateId);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className={step === "template" ? "max-w-2xl" : "max-w-md"}>
        {step === "template" ? (
          <>
            <DialogHeader>
              <DialogTitle>What should this agent do?</DialogTitle>
              <DialogDescription>
                Pick a starting point. Everything it sets up stays editable afterwards.
              </DialogDescription>
            </DialogHeader>

            {isLoading ? (
              <div className="grid gap-3 sm:grid-cols-2">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-[104px] rounded-lg" />
                ))}
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                {(templates ?? []).map((t) => (
                  <TemplateCard key={t.id} template={t} onSelect={() => pick(t.id, t.label)} />
                ))}
              </div>
            )}

            <button
              type="button"
              onClick={() => pick(null, "")}
              className="mt-4 flex w-full items-center gap-3 rounded-lg border border-dashed border-border-strong bg-surface/40 px-4 py-3 text-left transition-colors hover:border-accent/40 hover:bg-accent/[0.03]"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-border bg-surface-2 text-muted">
                <PenLine className="size-4" />
              </span>
              <span>
                <span className="block text-sm font-medium text-text">Start from scratch</span>
                <span className="block text-xs text-muted">
                  A blank agent with the default support prompt.
                </span>
              </span>
            </button>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Name your agent</DialogTitle>
              <DialogDescription>
                {chosen
                  ? `Starting from the ${chosen.label} template.`
                  : "Starting from a blank agent."}
              </DialogDescription>
            </DialogHeader>

            <form onSubmit={submit} className="space-y-4">
              <Input
                autoFocus
                placeholder="Agent name"
                aria-label="Agent name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              {error && <p className="text-sm text-error-text">{error}</p>}
              <div className="flex gap-2">
                <Button type="button" variant="ghost" onClick={() => setStep("template")} disabled={busy}>
                  <ArrowLeft className="size-4" /> Back
                </Button>
                <Button type="submit" variant="primary" className="flex-1" disabled={busy || !name.trim()}>
                  {busy && <Loader2 className="size-4 animate-spin" />} Create &amp; configure
                </Button>
              </div>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function TemplateCard({
  template,
  onSelect,
}: {
  template: ApiAgentTemplate;
  onSelect: () => void;
}) {
  const Icon = ICONS[template.icon] ?? Bot;
  return (
    <button
      type="button"
      onClick={onSelect}
      className="group flex h-full flex-col gap-2 rounded-lg border border-border bg-surface-2/40 p-4 text-left transition-colors hover:border-accent/40 hover:bg-accent/[0.03]"
    >
      <span className="grid size-9 place-items-center rounded-lg border border-border bg-surface-2 text-muted transition-colors group-hover:text-accent">
        <Icon className="size-4" />
      </span>
      <span className="text-sm font-medium text-text">{template.label}</span>
      <span className="text-xs leading-relaxed text-muted">{template.description}</span>
    </button>
  );
}
