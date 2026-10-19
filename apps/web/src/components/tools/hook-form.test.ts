import { describe, expect, it } from "vitest";
import { newKv } from "./api-tool-form";
import {
  emptyHookForm,
  hookProblems,
  hookToBody,
  sampleCurl,
  type HookForm,
} from "./hook-form";

function form(over: Partial<HookForm> = {}): HookForm {
  return {
    ...emptyHookForm(),
    name: "Order shipped",
    mapping: [
      newKv("phone", "$.customer.phone"),
      newKv("order_id", "$.order.id"),
    ],
    ...over,
  };
}

describe("inbound hook form", () => {
  it("accepts a good set_variables hook and builds its body", () => {
    const f = form();
    expect(hookProblems(f)).toEqual([]);
    expect(hookToBody(f)).toEqual({
      name: "Order shipped",
      action: "set_variables",
      action_config: { channel: "whatsapp", contact_field: "phone" },
      payload_mapping: { phone: "$.customer.phone", order_id: "$.order.id" },
      auth_mode: "hmac",
    });
  });

  it("builds a send_message hook from text, or from an approved template", () => {
    const text = form({
      action: "send_message",
      message: "Order {{order_id}} shipped",
    });
    expect(hookProblems(text)).toEqual([]);
    expect(hookToBody(text).action_config).toEqual({
      channel: "whatsapp",
      contact_field: "phone",
      message: "Order {{order_id}} shipped",
    });
    const tpl = form({
      action: "send_message",
      template: "order_update",
      templateParams: "order_id, phone",
    });
    expect(hookProblems(tpl)).toEqual([]);
    expect(hookToBody(tpl).action_config).toEqual({
      channel: "whatsapp",
      contact_field: "phone",
      template: "order_update",
      template_params: ["order_id", "phone"],
    });
  });

  it("builds run_workflow and agent_turn hooks", () => {
    expect(
      hookToBody(form({ action: "run_workflow", workflowId: "w1" }))
        .action_config,
    ).toEqual({ workflow_id: "w1" });
    const turn = form({
      action: "agent_turn",
      message: "Order {{order_id}} changed.",
    });
    expect(hookProblems(turn)).toEqual([]);
    expect(hookToBody(turn).action_config).toEqual({
      message: "Order {{order_id}} changed.",
    });
  });

  it.each([
    [{ name: " " }, "Give the hook a name"],
    [{ mapping: [newKv("bad name", "$.a")] }, "not a valid field name"],
    [{ mapping: [newKv("phone", "customer.phone")] }, "needs a selector"],
    [
      { mapping: [newKv("phone", "$.a"), newKv("phone", "$.b")] },
      "can be used once",
    ],
    [{ mapping: [newKv("order_id", "$.o")] }, 'Map the field "phone"'],
    [{ action: "send_message" as const, message: "" }, "Write the message"],
    [
      { action: "send_message" as const, message: "{{missing}}" },
      "Not mapped: missing",
    ],
    [
      {
        action: "send_message" as const,
        channel: "telegram" as const,
        template: "t",
      },
      "WhatsApp feature",
    ],
    [{ action: "agent_turn" as const, message: "" }, "Write the update"],
    [
      { action: "agent_turn" as const, message: "{{nope}}" },
      "Not mapped: nope",
    ],
    [
      { action: "run_workflow" as const, workflowId: "" },
      "Choose the workflow",
    ],
    [
      { mapping: [newKv("phone", "$.customer.phone")] },
      "at least one field to save",
    ],
  ])("flags %j", (over, text) => {
    expect(hookProblems(form(over as Partial<HookForm>)).join(" | ")).toContain(
      text,
    );
  });

  it("writes a sample call that reads the secret from the environment, never inline", () => {
    const hmac = sampleCurl(
      "https://api.example.com/",
      "/v1/hooks/in/abc",
      "hmac",
      form().mapping,
    );
    expect(hmac).toContain("https://api.example.com/v1/hooks/in/abc");
    expect(hmac).toContain("$HOOK_SECRET");
    expect(hmac).toContain("X-Vicero-Signature");
    expect(hmac).toContain('"customer":{"phone":"example"}');
    const bearer = sampleCurl(
      "https://api.example.com",
      "/v1/hooks/in/abc",
      "bearer",
      form().mapping,
    );
    expect(bearer).toContain("Authorization: Bearer $HOOK_SECRET");
    expect(bearer).not.toContain("X-Vicero-Signature");
  });
});
