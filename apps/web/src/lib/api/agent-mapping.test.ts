import { describe, expect, it } from "vitest";
import { draftToPatch, versionToDraft } from "./agent-mapping";
import type { ApiAgent, ApiVersion } from "./types";

const agent = {
  id: "a1",
  name: "Support Bot",
  slug: "support-bot",
  description: null,
  status: "draft",
  public_key: "bf_pub_x",
  is_public: false,
  current_version_id: null,
  draft_version: 1,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
} as unknown as ApiAgent;

function version(overrides: Record<string, unknown> = {}): ApiVersion {
  return {
    id: "v1",
    version: 1,
    is_published: false,
    system_prompt: "You are helpful.",
    persona: { displayName: "Ava", tone: "Friendly", blockedTopics: [] },
    welcome_message: "Hi!",
    fallback_message: "Not sure.",
    suggested_prompts: [],
    model_config: { provider: "groq", model: "openai/gpt-oss-20b" },
    rag_config: {},
    features: {},
    created_at: "2026-07-01T00:00:00Z",
    ...overrides,
  } as unknown as ApiVersion;
}

describe("versionToDraft", () => {
  it("defaults score_threshold to the backend's 0.35, not 0.7", () => {
    // 0.7 filtered out genuinely relevant chunks; the backend default was lowered to 0.35
    // and the frontend default drifted, so a draft with no stored value disagreed.
    expect(versionToDraft(agent, version()).knowledge.scoreThreshold).toBe(0.35);
  });

  it("keeps a stored score_threshold", () => {
    const d = versionToDraft(agent, version({ rag_config: { score_threshold: 0.8 } }));
    expect(d.knowledge.scoreThreshold).toBe(0.8);
  });

  it("reads fallbacks and stop sequences, tolerating absence", () => {
    expect(versionToDraft(agent, version()).model.fallbacks).toEqual([]);
    expect(versionToDraft(agent, version()).model.stop).toEqual([]);

    const configured = versionToDraft(
      agent,
      version({
        model_config: {
          provider: "groq",
          model: "openai/gpt-oss-20b",
          stop: ["END"],
          fallbacks: [{ provider: "gemini", model: "gemini-1.5-flash" }],
        },
      }),
    );
    expect(configured.model.stop).toEqual(["END"]);
    expect(configured.model.fallbacks).toEqual([{ provider: "gemini", model: "gemini-1.5-flash" }]);
  });

  it("ignores malformed fallback entries rather than throwing", () => {
    const d = versionToDraft(
      agent,
      version({ model_config: { provider: "groq", model: "m", fallbacks: [null, { model: "x" }] } }),
    );
    expect(d.model.fallbacks).toEqual([]);
  });
});

describe("draftToPatch", () => {
  it("round-trips the model config a version returned", () => {
    const draft = versionToDraft(
      agent,
      version({
        model_config: {
          provider: "groq",
          model: "openai/gpt-oss-20b",
          temperature: 0.3,
          presence_penalty: 0.5,
          stop: ["END"],
          fallbacks: [{ provider: "gemini", model: "gemini-1.5-flash" }],
        },
      }),
    );
    const patch = draftToPatch(draft).model_config as Record<string, unknown>;

    expect(patch.provider).toBe("groq");
    expect(patch.temperature).toBe(0.3);
    expect(patch.presence_penalty).toBe(0.5);
    expect(patch.stop).toEqual(["END"]);
    expect(patch.fallbacks).toEqual([{ provider: "gemini", model: "gemini-1.5-flash" }]);
  });

  it("sends persona.tone, which the backend folds into the system prompt", () => {
    const patch = draftToPatch(versionToDraft(agent, version()));
    expect((patch.persona as Record<string, unknown>).tone).toBe("Friendly");
  });

  it("preserves the creating template's id across an autosave", () => {
    // The builder's persona round-trip is lossy by construction (it rebuilds the object from
    // named fields), so provenance has to be echoed explicitly or the next autosave would
    // strip it and the next-step hint would vanish on first edit.
    const draft = versionToDraft(
      agent,
      version({ persona: { displayName: "Ava", tone: "Concise", template_id: "appointment_scheduler" } }),
    );
    expect(draft.persona.templateId).toBe("appointment_scheduler");
    expect((draftToPatch(draft).persona as Record<string, unknown>).template_id).toBe(
      "appointment_scheduler",
    );
  });

  it("omits template_id entirely for an agent started from scratch", () => {
    const draft = versionToDraft(agent, version());
    expect(draft.persona.templateId).toBeNull();
    expect(draftToPatch(draft).persona as Record<string, unknown>).not.toHaveProperty("template_id");
  });
});
