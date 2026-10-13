import type { AgentStatus, Provider } from "./types";

export interface PersonaConfig {
  displayName: string;
  systemPrompt: string;
  tone: string;
  welcomeMessage: string;
  fallbackMessage: string;
  suggestedPrompts: string[];
  blockedTopics: string[];
  /** The role template this agent was created from, if any. Read-only provenance — it drives
   *  the builder's next-step hint and nothing else. Null for agents started from scratch. */
  templateId: string | null;
}

/** One link in the agent's provider fallback chain (NFR-4). */
export interface FallbackEntry {
  provider: Provider;
  model: string;
}

export interface ModelConfig {
  provider: Provider;
  model: string;
  /** Tried in order when the primary provider fails before producing any output. */
  fallbacks: FallbackEntry[];
  temperature: number;
  topP: number;
  maxTokens: number;
  frequencyPenalty: number;
  presencePenalty: number;
  /** Generation halts if the model emits any of these. */
  stop: string[];
}

export interface KnowledgeConfig {
  attachedKbIds: string[];
  topK: number;
  scoreThreshold: number;
  hybrid: boolean;
}

export interface FeatureToggles {
  rag: boolean;
  tools: boolean;
  memory: boolean;
  handoff: boolean;
}

export type FloatingButtonStyle =
  | "circle-chat"
  | "circle-message"
  | "circle-dots"
  | "rounded-square"
  | "pill-text"
  | "pulse-ring";

export type WidgetFont = "system" | "inter" | "arial" | "georgia" | "courier";
export type InputBarButton = "attachment" | "emoji";

export interface WidgetConfig {
  primaryColor: string;
  position: "bottom-right" | "bottom-left";
  launcherText: string;
  branding: boolean;
  mode: "dark" | "light";
  // Extended customization — null/defaults keep the current look for untouched agents.
  widgetStyle: "solid" | "transparent";
  backgroundColor: string | null;
  textColor: string | null;
  bubbleColor: string | null;
  typingAreaColor: string | null;
  fontFamily: WidgetFont;
  logoUrl: string | null;
  floatingButtonStyle: FloatingButtonStyle | null;
  floatingButtonColor: string | null;
  inputBarButtons: InputBarButton[];
  /** Websites allowed to embed the widget (hostnames). Empty = any site. */
  allowedSites: string[];
}

export interface AgentDraft {
  id: string;
  name: string;
  status: AgentStatus;
  persona: PersonaConfig;
  model: ModelConfig;
  knowledge: KnowledgeConfig;
  features: FeatureToggles;
  widget: WidgetConfig;
}

export const toneOptions = [
  "Friendly",
  "Professional",
  "Concise",
  "Empathetic",
  "Playful",
  "Technical",
];

// The fake `knowledgeBases`, `tools`, `versions` and `makeDraft("Support Concierge")`
// fixtures that used to live here were removed on 2026-07-30: every screen that once
// rendered them now reads the real API (see docs/PROGRESS.md).
//
// `providerCatalog` followed on 2026-08-03 (the roadmap item ADR-041 left open). It listed
// each provider's models client-side, so the Model tab offered whatever was true when the
// list was typed — including `mixtral-8x7b-32768`, which Groq had already retired — and it
// could not know which providers this org actually holds a key for. Both now come from
// `GET /v1/credentials/providers`. What remains here is the builder's *type* vocabulary plus
// `toneOptions`, which is genuinely static copy rather than per-tenant data.
