"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Wrench, Brain, Plus, UserRound, X } from "lucide-react";
import { Field, SectionCard, SliderField } from "@/components/builder/field";
import { ChipInput } from "@/components/builder/chip-input";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useBuilder } from "@/lib/store/builder";
import { useSession } from "@/lib/store/session";
import { listProviderModels, listProviders } from "@/lib/api/credentials";
import type { ApiProviderInfo } from "@/lib/api/types";
import type { Provider } from "@/lib/mock/types";
import type { FeatureToggles } from "@/lib/mock/builder";

/** Providers this org holds a key for, plus whichever one the agent is already on.
 *
 * The pinned entry matters: an agent live on a provider whose key was since removed must not
 * have its model silently rewritten by the picker just because the option vanished. It stays
 * selectable and flagged, and only an operator can move it. */
function useSelectableProviders(current: string) {
  const orgId = useSession((s) => s.activeOrgId);
  const { data, isLoading } = useQuery({
    queryKey: ["providers", orgId],
    queryFn: listProviders,
    enabled: Boolean(orgId),
  });
  const all = data ?? [];
  const configured = all.filter((p) => p.configured);
  const currentSpec = all.find((p) => p.name === current);
  const options =
    currentSpec && !currentSpec.configured ? [...configured, currentSpec] : configured;
  return { options, all, configured, isLoading, byName: new Map(all.map((p) => [p.name, p])) };
}

export function ModelTab() {
  const draft = useBuilder((s) => s.draft);
  const update = useBuilder((s) => s.update);
  const orgId = useSession((s) => s.activeOrgId);
  const providerName = draft?.model.provider ?? "";
  const { options, configured, byName, isLoading } = useSelectableProviders(providerName);
  // Ask the provider what it can actually run; the catalogue is only the fallback.
  const models = useQuery({
    queryKey: ["provider-models", orgId, providerName],
    queryFn: () => listProviderModels(providerName),
    enabled: Boolean(orgId && providerName),
    staleTime: 5 * 60 * 1000,
  });

  if (!draft) return null;
  const m = draft.model;
  const provider = byName.get(m.provider);
  // First *configured* provider not already used as the primary or an existing fallback.
  const nextFallback = configured.find(
    (p) => p.name !== m.provider && !m.fallbacks.some((f) => f.provider === p.name),
  )?.name as Provider | undefined;

  const modelOptions = models.data?.models ?? provider?.available_models ?? [];
  // Same reasoning as the pinned provider: a model that has been retired upstream stays
  // selected until someone changes it deliberately.
  const modelIds = modelOptions.map((o) => o.id);
  const shownModels = modelIds.includes(m.model)
    ? modelOptions
    : [...modelOptions, { id: m.model, label: `${m.model} (not offered)` }];

  if (!isLoading && configured.length === 0) {
    return (
      <div className="space-y-6">
        <SectionCard title="Provider & model" description="No LLM provider is connected yet.">
          <div className="rounded-md border border-border bg-surface-2/50 p-5 text-center">
            <p className="text-sm text-text">This agent has no model it can run.</p>
            <p className="mx-auto mt-1 max-w-md text-xs text-muted">
              Add an API key for a provider and its models become selectable here.
            </p>
            <Link
              href="/settings/credentials"
              className="mt-3 inline-block text-sm text-accent underline underline-offset-2"
            >
              Add a provider key
            </Link>
          </div>
        </SectionCard>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SectionCard title="Provider & model" description="Only providers you hold a key for are listed.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Provider">
            <Select
              value={m.provider}
              onValueChange={(v) =>
                update((d) => {
                  d.model.provider = v as Provider;
                  // Seed with the new provider's first model so the pair is never mismatched;
                  // the live list loads a moment later and the operator can refine it.
                  d.model.model = byName.get(v)?.available_models[0]?.id ?? "";
                })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((p) => (
                  <SelectItem key={p.name} value={p.name}>
                    {p.label}
                    {p.free ? "  · free" : ""}
                    {p.configured ? "" : "  · no key"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Model">
            <Select value={m.model} onValueChange={(v) => update((d) => void (d.model.model = v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {shownModels.map((model) => (
                  <SelectItem key={model.id} value={model.id}>
                    {model.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {provider ? (
            <Badge variant={provider.free ? "success" : "warn"}>
              {provider.free ? "Free tier" : "Paid provider"}
            </Badge>
          ) : null}
          {provider && !provider.configured ? (
            <Badge variant="error">No key — this agent cannot reply</Badge>
          ) : null}
          {m.fallbacks.length > 0 ? (
            <Badge variant="default">
              Fallback: {m.fallbacks.map((f) => byName.get(f.provider)?.label ?? f.provider).join(" → ")}
            </Badge>
          ) : null}
        </div>

        <Field
          label="Fallback providers"
          description="Tried in order if the primary fails before it starts replying. Not used once a reply has begun."
        >
          <div className="space-y-2">
            {m.fallbacks.map((f, i) => (
              <div key={`${f.provider}-${i}`} className="flex items-center gap-2">
                <Select
                  value={f.provider}
                  onValueChange={(v) =>
                    update((d) => {
                      d.model.fallbacks[i].provider = v as Provider;
                      d.model.fallbacks[i].model = byName.get(v)?.available_models[0]?.id ?? "";
                    })
                  }
                >
                  <SelectTrigger className="w-[190px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {options
                      .filter((p) => p.name !== m.provider)
                      .map((p) => (
                        <SelectItem key={p.name} value={p.name}>
                          {p.label}
                          {p.configured ? "" : "  · no key"}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                <Select
                  value={f.model}
                  onValueChange={(v) => update((d) => void (d.model.fallbacks[i].model = v))}
                >
                  <SelectTrigger className="flex-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(byName.get(f.provider)?.available_models ?? [{ id: f.model, label: f.model }]).map(
                      (model) => (
                        <SelectItem key={model.id} value={model.id}>
                          {model.label}
                        </SelectItem>
                      ),
                    )}
                  </SelectContent>
                </Select>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove ${byName.get(f.provider)?.label ?? f.provider} fallback`}
                  onClick={() => update((d) => void d.model.fallbacks.splice(i, 1))}
                >
                  <X className="size-4" />
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              disabled={nextFallback === undefined}
              onClick={() =>
                update((d) => {
                  if (nextFallback === undefined) return;
                  d.model.fallbacks.push({
                    provider: nextFallback,
                    model: byName.get(nextFallback)?.available_models[0]?.id ?? "",
                  });
                })
              }
            >
              <Plus className="size-4" /> Add fallback
            </Button>
            {m.fallbacks.length === 0 ? (
              <p className="text-xs text-faint">
                No fallback configured — if {provider?.label ?? m.provider} fails, the turn returns an
                error.
              </p>
            ) : null}
          </div>
        </Field>
        <Field label="Credentials" description="Which key this agent will use for the selected provider.">
          <CredentialStatus provider={provider} name={m.provider} />
        </Field>
      </SectionCard>

      <SectionCard title="Sampling" description="Control creativity and length of responses.">
        <div className="grid gap-6 sm:grid-cols-2">
          <SliderField label="Temperature" value={m.temperature} display={m.temperature.toFixed(2)}>
            <Slider
              value={[m.temperature]}
              min={0}
              max={2}
              step={0.05}
              onValueChange={([v]) => update((d) => void (d.model.temperature = v))}
            />
          </SliderField>
          <SliderField label="Top P" value={m.topP} display={m.topP.toFixed(2)}>
            <Slider
              value={[m.topP]}
              min={0}
              max={1}
              step={0.05}
              onValueChange={([v]) => update((d) => void (d.model.topP = v))}
            />
          </SliderField>
          <SliderField label="Max tokens" value={m.maxTokens} display={String(m.maxTokens)}>
            <Slider
              value={[m.maxTokens]}
              min={256}
              max={8192}
              step={128}
              onValueChange={([v]) => update((d) => void (d.model.maxTokens = v))}
            />
          </SliderField>
          <SliderField label="Presence penalty" value={m.presencePenalty} display={m.presencePenalty.toFixed(1)}>
            <Slider
              value={[m.presencePenalty]}
              min={-2}
              max={2}
              step={0.1}
              onValueChange={([v]) => update((d) => void (d.model.presencePenalty = v))}
            />
          </SliderField>
          <SliderField label="Frequency penalty" value={m.frequencyPenalty} display={m.frequencyPenalty.toFixed(1)}>
            <Slider
              value={[m.frequencyPenalty]}
              min={0}
              max={2}
              step={0.1}
              onValueChange={([v]) => update((d) => void (d.model.frequencyPenalty = v))}
            />
          </SliderField>
        </div>
        <Field
          label="Stop sequences"
          description="Generation halts as soon as the model emits one of these. Case-sensitive."
        >
          <ChipInput
            values={m.stop}
            onChange={(next) => update((d) => void (d.model.stop = next))}
            placeholder="Add a stop sequence…"
          />
        </Field>
      </SectionCard>

      <SectionCard title="Capabilities" description="Toggle what this agent is allowed to do.">
        <FeatureRow
          icon={<BookOpen className="size-4" />}
          label="Knowledge retrieval (RAG)"
          desc="Ground answers in attached knowledge bases."
          k="rag"
        />
        <FeatureRow
          icon={<Wrench className="size-4" />}
          label="Tool calling"
          desc="Let the agent call tools and automations."
          k="tools"
        />
        <FeatureRow
          icon={<Brain className="size-4" />}
          label="Memory"
          desc="Remember earlier turns with long-term summaries."
          k="memory"
        />
        <FeatureRow
          icon={<UserRound className="size-4" />}
          label="Human handoff"
          desc="Escalate to a human operator when stuck."
          k="handoff"
        />
      </SectionCard>
    </div>
  );
}

function FeatureRow({
  icon,
  label,
  desc,
  k,
}: {
  icon: React.ReactNode;
  label: string;
  desc: string;
  k: keyof FeatureToggles;
}) {
  const draft = useBuilder((s) => s.draft);
  const update = useBuilder((s) => s.update);
  if (!draft) return null;
  return (
    <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
      <span className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-accent">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-text">{label}</div>
        <div className="text-xs text-muted">{desc}</div>
      </div>
      <Switch
        checked={draft.features[k]}
        onCheckedChange={(v) => update((d) => void (d.features[k] = v))}
      />
    </div>
  );
}

/** Shows which key this agent will actually use.
 *
 * The backend resolves keys agent-scoped → org default → platform env key, so the old
 * "Organization default key / Bring your own / Custom base URL" select was doubly wrong: it
 * was never persisted, and the choice isn't the agent's to make. `key_source` reports which
 * of those three the server would land on, so this can state it rather than infer it. */
function CredentialStatus({ provider, name }: { provider: ApiProviderInfo | undefined; name: string }) {
  if (!provider) {
    return <p className="text-sm text-muted">Checking credentials…</p>;
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {provider.key_source === "org" && (
        <>
          <Badge variant="success">Key configured</Badge>
          <span className="text-muted">
            {provider.label} · <span className="font-mono text-xs">{provider.masked_key}</span>
          </span>
        </>
      )}
      {provider.key_source === "env" && (
        <>
          <Badge variant="success">Platform key</Badge>
          <span className="text-muted">
            Using this deployment&apos;s shared {provider.label} key. Add your own to override it.
          </span>
        </>
      )}
      {provider.key_source === "not_required" && (
        <>
          <Badge variant="default">No key needed</Badge>
          <span className="text-muted">{provider.label} runs without an API key.</span>
        </>
      )}
      {provider.key_source === "none" && (
        <>
          <Badge variant="error">No key</Badge>
          <span className="text-muted">
            Every reply on {name} will fail until a key is added.
          </span>
        </>
      )}
      <Link href="/settings/credentials" className="text-xs text-accent underline underline-offset-2">
        Manage
      </Link>
    </div>
  );
}
