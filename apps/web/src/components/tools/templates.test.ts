import { describe, expect, it } from "vitest";
import {
  applySuggestion,
  candidateNames,
  referencedNames,
  suggest,
  templateToken,
  unknownReferences,
} from "./templates";

describe("template autocomplete", () => {
  it("finds the open {{ the caret is in", () => {
    const text = "/orders/{{ord";
    expect(templateToken(text, text.length)).toEqual({
      start: 8,
      query: "ord",
    });
    expect(templateToken("/orders/{{", 10)).toEqual({ start: 8, query: "" });
    expect(templateToken("a {{vars.cu", 11)).toEqual({
      start: 2,
      query: "vars.cu",
    });
  });

  it("finds nothing outside a placeholder", () => {
    expect(templateToken("/orders/42", 10)).toBeNull();
    expect(templateToken("{{done}} and more", 17)).toBeNull();
    expect(templateToken("{{ a b", 6)).toBeNull(); // not a name
    expect(templateToken("{{x}", 4)).toBeNull(); // a stray brace is not a name
  });

  it("only looks before the caret", () => {
    expect(templateToken("{{abc}} {{def", 4)).toEqual({
      start: 0,
      query: "ab",
    });
  });

  it("suggests by prefix, case-insensitively, exact match last", () => {
    const all = ["order_id", "order", "customer", "vars.customer_id"];
    expect(suggest(all, "or")).toEqual(["order_id", "order"]);
    expect(suggest(all, "order")).toEqual(["order_id", "order"]);
    expect(suggest(all, "VARS.")).toEqual(["vars.customer_id"]);
    expect(suggest(all, "zzz")).toEqual([]);
    expect(suggest(all, "")).toEqual(all);
  });

  it("completes the placeholder and puts the caret after it", () => {
    const text = "/orders/{{or}}/items";
    const caret = 11;
    const token = templateToken(text, caret);
    expect(token).not.toBeNull();
    const out = applySuggestion(text, caret, token!, "order_id");
    expect(out.text).toBe("/orders/{{order_id}}/items");
    expect(out.text.slice(out.caret)).toBe("/items");
  });

  it("completes when nothing closes it yet", () => {
    const text = "x={{";
    const out = applySuggestion(text, 4, templateToken(text, 4)!, "q");
    expect(out).toEqual({ text: "x={{q}}", caret: 7 });
  });

  it("lists parameters first, then conversation variables", () => {
    expect(candidateNames(["a", "b"], ["c"])).toEqual(["a", "b", "vars.c"]);
  });

  it("finds every referenced name once", () => {
    expect(
      referencedNames("/a/{{x}}/{{ y }}", "{{x}}", undefined, "{{vars.z}}"),
    ).toEqual(["x", "y", "vars.z"]);
  });

  it("flags references nothing provides, but trusts vars.", () => {
    expect(
      unknownReferences(["/a/{{x}}/{{typo}}", "{{vars.anything}}"], ["x"]),
    ).toEqual(["typo"]);
    expect(unknownReferences(["no placeholders"], [])).toEqual([]);
  });
});
