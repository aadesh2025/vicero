import { describe, expect, it } from "vitest";
import type { McpBinding } from "@/lib/api/mcp";
import {
  enabledCount,
  isEnabled,
  roomLeft,
  setOverride,
  toggleTool,
} from "./mcp-selection";

function binding(over: Partial<McpBinding> = {}): McpBinding {
  return {
    server_id: "s1",
    server_name: "CRM",
    slug: "crm",
    enabled: true,
    enabled_tools: [],
    tool_overrides: {},
    available_tools: [],
    tools_cached_at: null,
    cache_stale: false,
    ...over,
  };
}

describe("mcp tool selection", () => {
  it("binds a server the first time one of its tools is switched on", () => {
    const next = toggleTool([], "s1", "lookup", true, 0);
    expect(next).toEqual([
      {
        server_id: "s1",
        enabled: true,
        enabled_tools: ["lookup"],
        tool_overrides: {},
      },
    ]);
  });

  it("keeps the list sorted and adds to an existing binding", () => {
    const next = toggleTool(
      [binding({ enabled_tools: ["zeta"] })],
      "s1",
      "alpha",
      true,
      0,
    );
    expect(next?.[0].enabled_tools).toEqual(["alpha", "zeta"]);
  });

  it("unbinds a server when its last tool goes off, and drops that tool's overrides", () => {
    const b = binding({
      enabled_tools: ["lookup"],
      tool_overrides: { lookup: { description: "x" } },
    });
    expect(toggleTool([b], "s1", "lookup", false, 0)).toEqual([]);
    const two = binding({
      enabled_tools: ["a", "b"],
      tool_overrides: { a: { description: "x" } },
    });
    const after = toggleTool([two], "s1", "a", false, 0);
    expect(after?.[0].enabled_tools).toEqual(["b"]);
    expect(after?.[0].tool_overrides).toEqual({});
  });

  it("empty means none: a binding with no tools offers nothing", () => {
    expect(enabledCount([binding()])).toBe(0);
    expect(isEnabled([binding()], "s1", "x")).toBe(false);
  });

  it("a disabled binding offers nothing", () => {
    const b = binding({ enabled: false, enabled_tools: ["a", "b"] });
    expect(enabledCount([b])).toBe(0);
    expect(isEnabled([b], "s1", "a")).toBe(false);
  });

  it("refuses to pass the 20-tool ceiling, counting the agent's other tools", () => {
    const full = binding({
      enabled_tools: Array.from({ length: 15 }, (_, i) => `t${i}`),
    });
    expect(roomLeft([full], 5)).toBe(0);
    expect(toggleTool([full], "s1", "one_more", true, 5)).toBeNull();
    expect(toggleTool([full], "s1", "one_more", true, 4)).not.toBeNull();
    // Switching a tool that is already on, or off, is never blocked by the ceiling.
    expect(toggleTool([full], "s1", "t0", false, 5)).not.toBeNull();
    expect(toggleTool([full], "s1", "t0", true, 5)).not.toBeNull();
  });

  it("sets and clears a tool's overrides", () => {
    const b = binding({ enabled_tools: ["lookup"] });
    const set = setOverride([b], "s1", "lookup", {
      description: "  Find an order  ",
      approval_required: true,
    });
    expect(set[0].tool_overrides).toEqual({
      lookup: { description: "Find an order", approval_required: true },
    });
    const cleared = setOverride(
      [
        binding({
          enabled_tools: ["lookup"],
          tool_overrides: set[0].tool_overrides,
        }),
      ],
      "s1",
      "lookup",
      {
        description: " ",
        approval_required: false,
      },
    );
    expect(cleared[0].tool_overrides).toEqual({});
  });
});
