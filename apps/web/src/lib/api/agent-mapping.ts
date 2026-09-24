import type { AgentDraft } from "@/lib/mock/builder";
import type { ApiAgent, ApiVersion } from "./types";
import type { Provider } from "@/lib/mock/types";

function num(v: unknown, fallback: number): number {
  return typeof v === "number" ? v : fallback;
}

/** Map a backend agent + version into the builder's draft shape. */
export function versionToDraft(
  agent: ApiAgent,
  v: ApiVersion,
  /** From the unversioned widget-config endpoint. Falls back to the legacy
   *  `persona.widget` for a draft loaded before that migration ran. */
  widgetConfig?: Record<string, unknown>,
): AgentDraft {
  const mc = v.model_config ?? {};
  const rag = v.rag_config ?? {};
  const feat = v.features ?? {};
  const persona = v.persona ?? {};
  const w = (widgetConfig ?? (persona.widget as Record<string, unknown>) ?? {}) as Record<string, unknown>;

  return {
    id: agent.id,
    name: agent.name,
    status: agent.status === "published" ? "live" : agent.status === "archived" ? "paused" : "draft",
    persona: {
      displayName: (persona.displayName as string) ?? agent.name,
      systemPrompt: v.system_prompt ?? "",
      tone: (persona.tone as string) ?? "Friendly",
      welcomeMessage: v.welcome_message ?? "",
      fallbackMessage: v.fallback_message ?? "",
      suggestedPrompts: v.suggested_prompts ?? [],
      blockedTopics: (persona.blockedTopics as string[]) ?? [],
      templateId: (persona.template_id as string) ?? null,
    },
    model: {
      provider: (mc.provider as Provider) ?? "groq",
      model: (mc.model as string) ?? "openai/gpt-oss-120b",
      temperature: num(mc.temperature, 0.7),
      topP: num(mc.top_p, 1),
      maxTokens: num(mc.max_tokens, 1024),
      frequencyPenalty: num(mc.frequency_penalty, 0),
      presencePenalty: num(mc.presence_penalty, 0),
      fallbacks: Array.isArray(mc.fallbacks)
        ? (mc.fallbacks as Record<string, unknown>[])
            .filter((f) => f && typeof f.provider === "string")
            .map((f) => ({ provider: f.provider as Provider, model: String(f.model ?? "") }))
        : [],
      stop: Array.isArray(mc.stop) ? (mc.stop as unknown[]).map(String) : [],
    },
    knowledge: {
      attachedKbIds: (rag.knowledge_base_ids as string[]) ?? [],
      topK: num(rag.top_k, 5),
      scoreThreshold: num(rag.score_threshold, 0.35),
      hybrid: (rag.hybrid as boolean) ?? true,
    },
    features: {
      rag: (rag.enabled as boolean) ?? false,
      tools: (feat.tools_enabled as boolean) ?? false,
      memory: (feat.memory_enabled as boolean) ?? true,
      handoff: (feat.handoff_enabled as boolean) ?? false,
    },
    widget: {
      primaryColor: (w.primaryColor as string) ?? "#1F2937",
      position: (w.position as "bottom-right" | "bottom-left") ?? "bottom-right",
      launcherText: (w.launcherText as string) ?? "Chat with us",
      branding: (w.branding as boolean) ?? true,
      mode: (w.mode as "dark" | "light") ?? "light",
      widgetStyle: (w.widgetStyle as "solid" | "transparent") ?? "solid",
      backgroundColor: (w.backgroundColor as string) ?? null,
      textColor: (w.textColor as string) ?? null,
      bubbleColor: (w.bubbleColor as string) ?? null,
      typingAreaColor: (w.typingAreaColor as string) ?? null,
      fontFamily: (w.fontFamily as AgentDraft["widget"]["fontFamily"]) ?? "system",
      logoUrl: (w.logoUrl as string) ?? null,
      floatingButtonStyle: (w.floatingButtonStyle as AgentDraft["widget"]["floatingButtonStyle"]) ?? null,
      floatingButtonColor: (w.floatingButtonColor as string) ?? null,
      inputBarButtons: (w.inputBarButtons as AgentDraft["widget"]["inputBarButtons"]) ?? ["attachment"],
    },
  };
}

/** Map the builder draft back into a version PATCH body (JSON key model_config). */
export function draftToPatch(draft: AgentDraft): Record<string, unknown> {
  const p = draft.persona;
  const m = draft.model;
  const k = draft.knowledge;
  const f = draft.features;
  return {
    system_prompt: p.systemPrompt,
    welcome_message: p.welcomeMessage,
    fallback_message: p.fallbackMessage,
    suggested_prompts: p.suggestedPrompts,
    persona: {
      displayName: p.displayName,
      tone: p.tone,
      blockedTopics: p.blockedTopics,
      // Echoed back so provenance survives even though the backend merges persona on write —
      // an autosave that omitted it would otherwise depend on that merge to not lose it.
      ...(p.templateId ? { template_id: p.templateId } : {}),
    },
    model_config: {
      provider: m.provider,
      model: m.model,
      temperature: m.temperature,
      top_p: m.topP,
      max_tokens: m.maxTokens,
      frequency_penalty: m.frequencyPenalty,
      presence_penalty: m.presencePenalty,
      stop: m.stop,
      fallbacks: m.fallbacks.map((f) => ({ provider: f.provider, model: f.model })),
    },
    rag_config: {
      enabled: f.rag,
      knowledge_base_ids: k.attachedKbIds,
      top_k: k.topK,
      score_threshold: k.scoreThreshold,
      hybrid: k.hybrid,
    },
    features: { tools_enabled: f.tools, memory_enabled: f.memory, handoff_enabled: f.handoff },
  };
}

/** The widget's appearance, for the unversioned `PATCH /agents/{id}/widget-config`.
 *
 * Deliberately *not* part of `draftToPatch`: appearance is live on save, while everything
 * else in the draft waits for a publish. */
export function draftToWidgetConfig(draft: AgentDraft): Record<string, unknown> {
  const w = draft.widget;
  return {
    primaryColor: w.primaryColor,
    position: w.position,
    launcherText: w.launcherText,
    branding: w.branding,
    mode: w.mode,
    widgetStyle: w.widgetStyle,
    backgroundColor: w.backgroundColor,
    textColor: w.textColor,
    bubbleColor: w.bubbleColor,
    typingAreaColor: w.typingAreaColor,
    fontFamily: w.fontFamily,
    logoUrl: w.logoUrl,
    floatingButtonStyle: w.floatingButtonStyle,
    floatingButtonColor: w.floatingButtonColor,
    inputBarButtons: w.inputBarButtons,
  };
}
