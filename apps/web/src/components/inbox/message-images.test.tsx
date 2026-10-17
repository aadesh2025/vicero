import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MessageImages, visibleText } from "./message-images";
import type { ApiMessage } from "@/lib/api/conversations";
import { ApiError } from "@/lib/api/client";

const apiBlob = vi.fn();
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/client")>()),
  apiBlob: (...a: unknown[]) => apiBlob(...a),
}));

function renderImages(direction: "in" | "out" = "in", description: string | null = "A cracked screen") {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MessageImages attachments={[{ media_id: "m1", kind: "image", direction, description }]} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => "blob:photo");
  URL.revokeObjectURL = vi.fn();
});

describe("MessageImages", () => {
  it("shows the photo with the vision description as alt text and enlarges on click", async () => {
    apiBlob.mockResolvedValue(new Blob(["x"], { type: "image/jpeg" }));
    renderImages();
    const img = await screen.findByAltText("A cracked screen");
    expect(img).toHaveAttribute("src", "blob:photo");
    expect(apiBlob).toHaveBeenCalledWith("/v1/media/m1");
    fireEvent.click(screen.getByRole("button", { name: /Enlarge/ }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("says an expired customer photo was deleted", async () => {
    apiBlob.mockRejectedValue(new ApiError(404, "media.not_found", "Image not found."));
    renderImages("in");
    expect(await screen.findByText("Photo deleted after 30 days")).toBeInTheDocument();
  });

  it("does not claim a missing outgoing photo was auto-deleted", async () => {
    apiBlob.mockRejectedValue(new ApiError(404, "media.not_found", "Image not found."));
    renderImages("out");
    expect(await screen.findByText("Photo unavailable")).toBeInTheDocument();
  });
});

describe("visibleText", () => {
  const msg = (content: string | null, withPhoto: boolean): ApiMessage =>
    ({
      id: "1",
      role: "user",
      content,
      attachments: withPhoto ? [{ media_id: "m", kind: "image", direction: "in", description: null }] : [],
    }) as unknown as ApiMessage;

  it("hides the placeholder and the vision line but keeps the caption", () => {
    expect(visibleText(msg("[Customer sent a photo]", true))).toBeNull();
    expect(visibleText(msg("is this covered?\n[Customer sent a photo: A cracked screen]", true))).toBe(
      "is this covered?",
    );
    expect(visibleText(msg("[Photo]", true))).toBeNull();
  });

  it("leaves ordinary messages alone", () => {
    expect(visibleText(msg("hello", false))).toBe("hello");
    expect(visibleText(msg("[Customer sent a photo] literally", false))).toBe("[Customer sent a photo] literally");
  });
});
