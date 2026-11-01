import { afterEach, describe, expect, it, vi } from "vitest";
import { apiStream } from "./client";

function response(chunks: string[]) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiStream", () => {
  it("parses events split across chunks and flushes the final event at EOF", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response([
          'data: {"type":"token","delta":"Hel',
          'lo"}\n\n',
          'data: {"type":"done","finish_reason":"stop"}',
        ]),
      ),
    );

    const events: Record<string, unknown>[] = [];
    for await (const event of apiStream("/stream", {})) events.push(event);
    expect(events).toEqual([
      { type: "token", delta: "Hello" },
      { type: "done", finish_reason: "stop" },
    ]);
  });

  it("turns malformed SSE JSON into a visible stream error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response(['data: {"type":\n\n'])),
    );

    const events: Record<string, unknown>[] = [];
    for await (const event of apiStream("/stream", {})) events.push(event);
    expect(events).toEqual([
      { type: "error", error: "The agent returned an invalid stream event." },
    ]);
  });
});
