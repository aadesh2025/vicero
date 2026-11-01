import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Playground } from "./playground";

const { playgroundStream } = vi.hoisted(() => ({
  playgroundStream: vi.fn(),
}));

vi.mock("@/lib/api/agents", () => ({
  playgroundStream,
}));
vi.mock("@/lib/api/credentials", () => ({
  listProviders: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/store/builder", () => ({
  useBuilder: (selector: (state: unknown) => unknown) =>
    selector({
      agentId: "agent-1",
      draft: {
        persona: { welcomeMessage: "Welcome" },
        model: { provider: "groq", model: "llama", temperature: 0.7 },
      },
    }),
}));
vi.mock("@/lib/store/session", () => ({
  useSession: (selector: (state: unknown) => unknown) =>
    selector({ activeOrgId: "org-1" }),
}));

function renderPlayground() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Playground />
    </QueryClientProvider>,
  );
}

describe("Playground stream lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("stops typing as soon as the done event arrives", async () => {
    let release!: () => void;
    const persistence = new Promise<void>((resolve) => {
      release = resolve;
    });
    async function* events() {
      yield { type: "token", delta: "Hello" };
      yield { type: "done", finish_reason: "stop" };
      await persistence;
    }
    playgroundStream.mockReturnValue(events());

    const { container } = renderPlayground();
    await act(async () => {
      fireEvent.change(screen.getByPlaceholderText(/message the draft agent/i), {
        target: { value: "hi" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    });

    await waitFor(() => expect(screen.getByText("Hello")).toBeTruthy());
    await waitFor(() =>
      expect(container.querySelector(".animate-caret-blink")).toBeNull(),
    );
    await act(async () => {
      release();
      await Promise.resolve();
    });
  });

  it("surfaces stream errors and clears typing", async () => {
    async function* events() {
      yield { type: "error", error: "provider unavailable" };
    }
    playgroundStream.mockReturnValue(events());

    const { container } = renderPlayground();
    await act(async () => {
      fireEvent.change(screen.getByPlaceholderText(/message the draft agent/i), {
        target: { value: "hi" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    });

    expect(await screen.findByText("⚠ provider unavailable")).toBeTruthy();
    expect(container.querySelector(".animate-caret-blink")).toBeNull();
  });
});
