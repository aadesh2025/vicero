"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Lightbulb, X } from "lucide-react";
import { Field, SectionCard } from "@/components/builder/field";
import { ChipInput } from "@/components/builder/chip-input";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { listAgentTemplates } from "@/lib/api/agents";
import { useBuilder } from "@/lib/store/builder";
import { toneOptions } from "@/lib/mock/builder";

// ~4 chars per token is a fine heuristic for a live counter.
const estTokens = (s: string) => Math.ceil(s.length / 4);

/** What a template-created agent still needs wired up by hand.
 *
 * A pointer, never an action: the template deliberately doesn't auto-attach a calendar or CRM
 * tool, because an agent that looks configured but has no credentials fails on its first real
 * visitor. Dismissal is local to the browser — it's a nudge, not state worth a write. */
function TemplateNextStep({ templateId }: { templateId: string }) {
  const [dismissed, setDismissed] = useState(false);
  const { data: templates } = useQuery({
    queryKey: ["agent-templates"],
    queryFn: listAgentTemplates,
    staleTime: Infinity,
  });
  const step = templates?.find((t) => t.id === templateId)?.suggested_next_step;
  if (!step || dismissed) return null;

  return (
    <div className="flex items-start gap-3 rounded-lg border border-accent/25 bg-accent/[0.04] px-4 py-3">
      <Lightbulb className="mt-0.5 size-4 shrink-0 text-accent" />
      <p className="flex-1 text-sm leading-relaxed text-muted">
        <span className="font-medium text-text">Suggested next step. </span>
        {step}
      </p>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss suggestion"
        className="rounded-md p-1 text-faint transition-colors hover:text-text"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}

export function PersonaTab() {
  const draft = useBuilder((s) => s.draft);
  const update = useBuilder((s) => s.update);
  if (!draft) return null;
  const p = draft.persona;

  return (
    <div className="space-y-6">
      {p.templateId && <TemplateNextStep templateId={p.templateId} />}
      <SectionCard title="Identity" description="How your agent introduces itself to people.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Display name" htmlFor="displayName" description="Shown in the chat header.">
            <Input
              id="displayName"
              value={p.displayName}
              onChange={(e) => update((d) => void (d.persona.displayName = e.target.value))}
            />
          </Field>
          <Field label="Tone" description="Steers the writing style.">
            <Select value={p.tone} onValueChange={(v) => update((d) => void (d.persona.tone = v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {toneOptions.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
      </SectionCard>

      <SectionCard title="System prompt" description="The core instructions that define behavior and guardrails.">
        <div className="space-y-2">
          <Textarea
            value={p.systemPrompt}
            onChange={(e) => update((d) => void (d.persona.systemPrompt = e.target.value))}
            className="min-h-[200px] font-mono text-[13px] leading-relaxed"
          />
          <div className="flex items-center justify-between text-xs text-faint">
            <span>{p.systemPrompt.length} characters</span>
            <span className="font-mono">~{estTokens(p.systemPrompt)} tokens</span>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Messages" description="First impression and graceful failure.">
        <Field label="Welcome message" description="Sent when a conversation opens.">
          <Textarea
            value={p.welcomeMessage}
            onChange={(e) => update((d) => void (d.persona.welcomeMessage = e.target.value))}
            className="min-h-16"
          />
        </Field>
        <Field label="Fallback message" description="Used when the agent can't answer.">
          <Textarea
            value={p.fallbackMessage}
            onChange={(e) => update((d) => void (d.persona.fallbackMessage = e.target.value))}
            className="min-h-16"
          />
        </Field>
        <Field label="Suggested prompts" description="Quick-start chips shown in the widget.">
          <ChipInput
            values={p.suggestedPrompts}
            onChange={(next) => update((d) => void (d.persona.suggestedPrompts = next))}
            placeholder="Add a suggested prompt…"
          />
        </Field>
      </SectionCard>

      <SectionCard title="Guardrails" description="Topics the agent must refuse or deflect.">
        <Field label="Blocked topics">
          <ChipInput
            values={p.blockedTopics}
            onChange={(next) => update((d) => void (d.persona.blockedTopics = next))}
            placeholder="Add a blocked topic…"
            variant="accent"
          />
        </Field>
      </SectionCard>
    </div>
  );
}
