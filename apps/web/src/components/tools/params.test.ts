import { describe, expect, it } from "vitest";
import {
  newRow,
  paramNames,
  rowsToSchema,
  schemaToRows,
  validateRows,
} from "./params";

describe("params builder", () => {
  it("turns rows into a JSON Schema with types, descriptions, enums and required", () => {
    const schema = rowsToSchema([
      newRow({
        name: "order_id",
        description: "The order number",
        required: true,
      }),
      newRow({
        name: "kind",
        type: "string",
        required: false,
        enumValues: "refund, replace",
      }),
      newRow({ name: "qty", type: "integer", enumValues: "1, 2, 3" }),
      newRow({ name: "gift", type: "boolean", required: false }),
    ]);
    expect(schema).toEqual({
      type: "object",
      properties: {
        order_id: { type: "string", description: "The order number" },
        kind: { type: "string", enum: ["refund", "replace"] },
        qty: { type: "integer", enum: [1, 2, 3] },
        gift: { type: "boolean" },
      },
      required: ["order_id", "qty"],
    });
  });

  it("skips unnamed rows and trims names", () => {
    const schema = rowsToSchema([
      newRow({ name: "  id  " }),
      newRow({ name: "" }),
    ]);
    expect(Object.keys(schema.properties as object)).toEqual(["id"]);
  });

  it("round-trips a saved schema", () => {
    const rows = [
      newRow({ name: "a", description: "x", required: true }),
      newRow({
        name: "b",
        type: "number",
        required: false,
        enumValues: "1, 2.5",
      }),
    ];
    const back = schemaToRows(rowsToSchema(rows));
    const strip = (rs: typeof rows) => rs.map((r) => ({ ...r, id: "" }));
    expect(strip(back)).toEqual(strip(rows));
  });

  it("reads a schema it did not write without inventing anything", () => {
    expect(schemaToRows(null)).toEqual([]);
    const rows = schemaToRows({
      properties: { weird: { type: "array" } },
      required: ["weird"],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      name: "weird",
      type: "string",
      required: true,
    });
  });

  it.each([
    [[newRow({ name: "" })], "needs a name"],
    [[newRow({ name: "1bad" })], "not a valid name"],
    [[newRow({ name: "has space" })], "not a valid name"],
    [[newRow({ name: "a" }), newRow({ name: "a" })], "appears twice"],
    [
      [newRow({ name: "n", type: "number", enumValues: "1, x" })],
      "must be numbers",
    ],
  ])("flags a bad table (%#)", (rows, text) => {
    expect(validateRows(rows).join(" ")).toContain(text);
  });

  it("accepts a good table", () => {
    expect(
      validateRows([newRow({ name: "order_id" }), newRow({ name: "_x2" })]),
    ).toEqual([]);
  });

  it("lists only valid names for the autocomplete", () => {
    expect(
      paramNames([
        newRow({ name: "ok" }),
        newRow({ name: "no way" }),
        newRow({ name: "" }),
      ]),
    ).toEqual(["ok"]);
  });
});
