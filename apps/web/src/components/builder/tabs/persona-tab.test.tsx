import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { PersonaTab, PROMPT_TOKEN_WARN } from "./persona-tab";
import { useBuilder } from "@/lib/store/builder";

vi.mock("@/lib/api/agents", () => ({ listAgentTemplates: vi.fn().mockResolvedValue([]) }));

function seed(systemPrompt: string) {
  useBuilder.setState({
    agentId: "a1",
    draft: {
      id: "a1",
      name: "Agent",
      status: "draft",
      persona: {
        displayName: "Agent",
        systemPrompt,
        tone: "Friendly",
        welcomeMessage: "",
        fallbackMessage: "",
        suggestedPrompts: [],
        blockedTopics: [],
        templateId: null,
      },
    },
  } as never);
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PersonaTab />
    </QueryClientProvider>,
  );
}

describe("PersonaTab prompt-size warning", () => {
  it("stays quiet for a normal prompt", () => {
    seed("You are a friendly assistant for Acme. Be concise.");
    renderTab();
    expect(screen.queryByText(/This prompt is long/)).toBeNull();
  });

  it("warns when the prompt alone would strain a free-tier token budget", () => {
    // The production agent that triggered this: ~32,800 characters (~8,200 tokens).
    seed("x".repeat((PROMPT_TOKEN_WARN + 1) * 4));
    renderTab();
    expect(screen.getByRole("status")).toHaveTextContent(/move business details .* into a knowledge base/);
  });
});
