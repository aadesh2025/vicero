import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelTab } from "./model-tab";
import { useSession } from "@/lib/store/session";
import { useBuilder } from "@/lib/store/builder";
import type { ApiProviderInfo, ApiProviderModels } from "@/lib/api/types";

const listProviders = vi.fn();
const listProviderModels = vi.fn();
vi.mock("@/lib/api/credentials", () => ({
  listProviders: () => listProviders(),
  listProviderModels: (p: string) => listProviderModels(p),
}));

function provider(over: Partial<ApiProviderInfo> & { name: string }): ApiProviderInfo {
  return {
    label: over.name,
    free: false,
    requires_key: true,
    models: [],
    available_models: [{ id: `${over.name}-model-1`, label: `${over.name} model 1`, context: null, tools: true, note: null, pricing_known: true }],
    configured: false,
    key_source: "none",
    masked_key: null,
    credential_id: null,
    base_url: null,
    base_url_required: false,
    api_key_url: null,
    key_hint: null,
    description: null,
    ...over,
  };
}

function models(ids: string[], source: "live" | "catalog" = "live"): ApiProviderModels {
  return {
    provider: "groq",
    source,
    error: null,
    models: ids.map((id) => ({ id, label: id, context: null, tools: true, note: null, pricing_known: true })),
  };
}

function seedDraft(providerName: string, model: string) {
  useBuilder.setState({
    agentId: "a1",
    draft: {
      id: "a1",
      name: "Agent",
      status: "draft",
      persona: {
        displayName: "Agent",
        systemPrompt: "",
        tone: "Friendly",
        welcomeMessage: "",
        fallbackMessage: "",
        suggestedPrompts: [],
        blockedTopics: [],
        templateId: null,
      },
      model: {
        provider: providerName as never,
        model,
        fallbacks: [],
        temperature: 0.7,
        topP: 1,
        maxTokens: 1024,
        frequencyPenalty: 0,
        presencePenalty: 0,
        stop: [],
      },
      knowledge: { attachedKbIds: [], topK: 4, scoreThreshold: 0.35, hybrid: true },
      features: { rag: false, tools: false, memory: false, handoff: false },
      widget: {
        primaryColor: "#f97316",
        position: "bottom-right",
        launcherText: "Chat",
        branding: true,
        mode: "dark",
        widgetStyle: "solid",
        backgroundColor: null,
        textColor: null,
        bubbleColor: null,
        typingAreaColor: null,
        fontFamily: "system",
        logoUrl: null,
        floatingButtonStyle: null,
        floatingButtonColor: null,
        inputBarButtons: [],
      },
    },
  } as never);
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ModelTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useSession.setState({ activeOrgId: "org-1" });
  listProviderModels.mockResolvedValue(models(["groq-model-1"]));
});

describe("ModelTab provider selection", () => {
  it("offers only providers this org holds a key for", async () => {
    // Asserted through the fallback picker, whose options are the same filtered list: with
    // the only keyed provider already primary there is nothing left to add.
    listProviders.mockResolvedValue([
      provider({ name: "groq", configured: true, key_source: "org", free: true }),
      provider({ name: "openai", configured: false, key_source: "none" }),
    ]);
    seedDraft("groq", "groq-model-1");
    renderTab();

    const add = await screen.findByRole("button", { name: /add fallback/i });
    expect(add).toBeDisabled();
  });

  it("offers a second keyed provider as a fallback", async () => {
    listProviders.mockResolvedValue([
      provider({ name: "groq", configured: true, key_source: "org", free: true }),
      provider({ name: "gemini", configured: true, key_source: "org", free: true }),
      provider({ name: "openai", configured: false, key_source: "none" }),
    ]);
    seedDraft("groq", "groq-model-1");
    renderTab();

    const add = await screen.findByRole("button", { name: /add fallback/i });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);

    // The unkeyed provider is never what gets picked.
    expect(useBuilder.getState().draft?.model.fallbacks[0].provider).toBe("gemini");
  });

  it("counts a platform env key as usable", async () => {
    // The live agents run on the deployment's Groq key with no credential row at all — if
    // `configured` ignored that, the builder would hide the provider they already use.
    listProviders.mockResolvedValue([
      provider({ name: "groq", configured: true, key_source: "env", free: true }),
    ]);
    seedDraft("groq", "groq-model-1");
    renderTab();

    await screen.findByText(/platform key/i);
  });

  it("keeps an agent's current provider selectable after its key is removed", async () => {
    // Dropping it from the list would leave the select with no matching option, and the next
    // autosave would write a provider the operator never chose.
    listProviders.mockResolvedValue([
      provider({ name: "groq", configured: true, key_source: "org", free: true }),
      provider({ name: "openai", configured: false, key_source: "none" }),
    ]);
    seedDraft("openai", "gpt-4o");
    renderTab();

    await screen.findByText(/no key — this agent cannot reply/i);
  });

  it("tells the operator when nothing is connected instead of showing an empty picker", async () => {
    listProviders.mockResolvedValue([provider({ name: "openai" })]);
    seedDraft("openai", "gpt-4o");
    renderTab();

    await screen.findByText(/this agent has no model it can run/i);
    expect(screen.getByRole("link", { name: /add a provider key/i })).toHaveAttribute(
      "href",
      "/settings/credentials",
    );
  });
});

describe("ModelTab model list", () => {
  it("asks the provider for its models rather than using a bundled list", async () => {
    listProviders.mockResolvedValue([
      provider({ name: "groq", configured: true, key_source: "org", free: true }),
    ]);
    listProviderModels.mockResolvedValue(models(["openai/gpt-oss-120b"]));
    seedDraft("groq", "openai/gpt-oss-120b");
    renderTab();

    await waitFor(() => expect(listProviderModels).toHaveBeenCalledWith("groq"));
  });

  it("keeps a model the provider no longer offers", async () => {
    // e.g. Groq retired mixtral-8x7b-32768. The agent stays on it, visibly flagged, until
    // someone picks a replacement — the picker must not silently rewrite a live config.
    listProviders.mockResolvedValue([
      provider({ name: "groq", configured: true, key_source: "org", free: true }),
    ]);
    listProviderModels.mockResolvedValue(models(["openai/gpt-oss-120b"]));
    seedDraft("groq", "mixtral-8x7b-32768");
    renderTab();

    await screen.findByText(/mixtral-8x7b-32768 \(not offered\)/i);
    expect(useBuilder.getState().draft?.model.model).toBe("mixtral-8x7b-32768");
  });
});
