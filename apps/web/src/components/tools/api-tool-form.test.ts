import { describe, expect, it } from "vitest";
import type { ApiTool } from "@/lib/api/tools";
import {
  emptyForm,
  formProblems,
  formToBody,
  newKv,
  toolToForm,
  type ApiToolForm,
} from "./api-tool-form";
import { newRow } from "./params";

function form(over: Partial<ApiToolForm> = {}): ApiToolForm {
  return {
    ...emptyForm(),
    name: "get_order",
    connectionId: "c1",
    target: "/orders/{{order_id}}",
    params: [newRow({ name: "order_id" })],
    ...over,
  };
}

describe("api tool form -> request body", () => {
  it("builds a connection-based GET with a path, schema and nothing else", () => {
    const body = formToBody(form(), "agent-1");
    expect(body).toMatchObject({
      name: "get_order",
      type: "api",
      agent_id: "agent-1",
      connection_id: "c1",
      config: { method: "GET", path: "/orders/{{order_id}}" },
      approval_required: false,
      rate_limit: null,
      messages: null,
      mode: "sync",
    });
    expect(body.input_schema).toMatchObject({ required: ["order_id"] });
    expect((body.config as Record<string, unknown>).body).toBeUndefined();
  });

  it("uses url, not path, without a connection", () => {
    const body = formToBody(
      form({ connectionId: "", target: "https://api.example.com/x" }),
      null,
    );
    expect(body.connection_id).toBeNull();
    expect(body.config).toMatchObject({ url: "https://api.example.com/x" });
    expect((body.config as Record<string, unknown>).path).toBeUndefined();
  });

  it("carries query, headers, a JSON body, pick, capture, retry and timeout", () => {
    const body = formToBody(
      form({
        method: "POST",
        query: [newKv("page", "{{order_id}}")],
        headers: [newKv("Idempotency-Key", "{{order_id}}")],
        body: '{"qty": "{{order_id}}"}',
        pick: "$.data.price\n$.data.items[0].name\n",
        maxChars: "2000",
        capture: [newKv("customer_id", "$.customer.id")],
        retryMax: "2",
        timeoutS: "20",
      }),
      "a",
    );
    expect(body.config).toMatchObject({
      method: "POST",
      query: { page: "{{order_id}}" },
      headers: { "Idempotency-Key": "{{order_id}}" },
      body_type: "json",
      body: { qty: "{{order_id}}" },
      response: {
        pick: ["$.data.price", "$.data.items[0].name"],
        max_chars: 2000,
      },
      capture: { customer_id: "$.customer.id" },
      retry: { max: 2 },
      timeout_s: 20,
    });
  });

  it("does not send a body with GET", () => {
    expect(
      (
        formToBody(form({ method: "GET", body: '{"a":1}' }), "a")
          .config as Record<string, unknown>
      ).body,
    ).toBeUndefined();
  });

  it("encodes a form body from key=value lines", () => {
    const body = formToBody(
      form({ method: "POST", bodyType: "form", body: "a={{order_id}}\nb = 2" }),
      "a",
    );
    expect(body.config).toMatchObject({
      body_type: "form",
      body: { a: "{{order_id}}", b: "2" },
    });
  });

  it("carries messages, approval, background mode and limits", () => {
    const body = formToBody(
      form({
        onStart: "One moment.",
        onDelayText: "Still working...",
        onDelayAfter: "6",
        onError: "Sorry.",
        approvalRequired: true,
        background: true,
        perConversation: "2",
        perMinute: "10",
      }),
      "a",
    );
    expect(body.messages).toEqual({
      on_start: "One moment.",
      on_delay: { after_s: 6, text: "Still working..." },
      on_error: "Sorry.",
    });
    expect(body).toMatchObject({
      approval_required: true,
      mode: "async",
      rate_limit: { per_conversation: 2, per_minute: 10 },
    });
  });
});

describe("api tool form validation", () => {
  it("accepts a good form", () => {
    expect(formProblems(form())).toEqual([]);
  });

  it.each([
    [{ name: "bad name" }, "name starts with a letter"],
    [{ name: "" }, "name starts with a letter"],
    [{ target: "" }, "Enter the path"],
    [{ connectionId: "", target: "" }, "Enter the URL"],
    [{ connectionId: "", target: "ftp://x" }, "must start with http"],
    [{ connectionId: "", target: "https://{{order_id}}/x" }, "host part"],
    [{ method: "POST" as const, body: "{not json" }, "not valid JSON"],
    [{ pick: "data.x" }, "not a valid selector"],
    [{ capture: [newKv("Bad Name", "$.a")] }, "not a valid variable name"],
    [{ capture: [newKv("ok", "nope")] }, "needs a selector"],
    [{ retryMax: "9" }, "Retries must be 0 to 3"],
    [{ perMinute: "0" }, "whole number above 0"],
    [{ timeoutS: "abc" }, "whole number above 0"],
    [{ onDelayText: "wait", onDelayAfter: "99" }, "between 1 and 30"],
    [{ target: "/orders/{{typo}}" }, "Not a parameter: {{typo}}"],
  ])("flags %j", (over, text) => {
    expect(
      formProblems(form(over as Partial<ApiToolForm>)).join(" | "),
    ).toContain(text);
  });

  it("lets {{vars.x}} through, since variables are checked at run time", () => {
    expect(
      formProblems(form({ target: "/c/{{vars.customer_id}}/{{order_id}}" })),
    ).toEqual([]);
  });

  it("checks the body for unknown references too", () => {
    const problems = formProblems(
      form({ method: "POST", body: '{"a": "{{nope}}"}' }),
    );
    expect(problems.join(" ")).toContain("{{nope}}");
  });
});

describe("tool -> form (editing a saved tool)", () => {
  const saved: ApiTool = {
    id: "t1",
    agent_id: "a1",
    connection_id: "c1",
    name: "get_order",
    type: "api",
    description: "Look up an order.",
    enabled: true,
    config: {
      method: "POST",
      path: "/orders/{{order_id}}",
      query: { page: "1" },
      headers: { "X-Ref": "{{order_id}}" },
      body_type: "json",
      body: { qty: 2 },
      response: { pick: ["$.status"], max_chars: 1500 },
      capture: { st: "$.status" },
      retry: { max: 1 },
      timeout_s: 10,
    },
    input_schema: {
      type: "object",
      properties: { order_id: { type: "string" } },
      required: ["order_id"],
    },
    created_at: "2026-01-01T00:00:00Z",
    messages: {
      on_start: "Hi",
      on_delay: { after_s: 7, text: "Still" },
      on_error: "Oops",
    },
    approval_required: true,
    rate_limit: { per_minute: 5 },
    mode: "async",
  };

  it("round-trips through the form unchanged", () => {
    const back = formToBody(toolToForm(saved), "a1");
    expect(back.config).toEqual(saved.config);
    expect(back.messages).toEqual(saved.messages);
    expect(back).toMatchObject({
      name: "get_order",
      connection_id: "c1",
      approval_required: true,
      rate_limit: { per_minute: 5 },
      mode: "async",
      description: "Look up an order.",
    });
    expect(back.input_schema).toEqual(saved.input_schema);
  });

  it("reads a legacy http tool (url, no connection)", () => {
    const f = toolToForm({
      ...saved,
      type: "http",
      connection_id: null,
      config: { method: "GET", url: "https://api.example.com/x" },
      messages: null,
      rate_limit: null,
      approval_required: false,
      mode: "sync",
    });
    expect(f.target).toBe("https://api.example.com/x");
    expect(f.connectionId).toBe("");
  });
});
