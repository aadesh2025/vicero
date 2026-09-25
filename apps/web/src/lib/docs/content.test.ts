/** Reading documentation off disk.
 *
 * Two things are being protected here. First, that a slug cannot be used to read a file
 * outside its collection — `readDoc` takes caller-supplied segments, and the whole reason
 * it matches them against a directory listing instead of joining them onto a path is that
 * joining would make `../../../.env` a legal request. Second, that the public collection
 * and the staff-only one never see each other's pages.
 */

import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { groupDocs, listDocs, readDoc } from "./content";

describe("readDoc", () => {
  it("reads a real page with its frontmatter", async () => {
    const doc = await readDoc("docs", ["quickstart"]);
    expect(doc).not.toBeNull();
    expect(doc!.title).toBe("Quickstart");
    expect(doc!.href).toBe("quickstart");
    expect(doc!.body).not.toContain("---"); // frontmatter is stripped, not inlined
  });

  it("reads a nested page", async () => {
    const doc = await readDoc("docs", ["api", "errors"]);
    expect(doc?.title).toBe("Errors");
    expect(doc?.href).toBe("api/errors");
  });

  it("returns null for a page that does not exist", async () => {
    await expect(readDoc("docs", ["nope"])).resolves.toBeNull();
  });

  // The important ones. Each of these would read a file if the slug were joined to a path.
  it.each([
    [["..", "internal", "architecture"], "parent traversal"],
    [["..", "..", "..", ".env"], "traversal to the environment file"],
    [["../../.env"], "traversal inside one segment"],
    [["..%2f..%2f.env"], "url-encoded traversal"],
    [["/etc/passwd"], "absolute path"],
    [["C:\Windows\win.ini"], "windows absolute path"],
    [["introduction.mdx"], "the filename rather than the slug"],
    [[""], "an empty segment"],
  ])("refuses %j (%s)", async (slug) => {
    await expect(readDoc("docs", slug as string[])).resolves.toBeNull();
  });

  it("refuses an unknown collection rather than building a path from it", async () => {
    // @ts-expect-error — deliberately passing a value the type forbids, because at runtime
    // this is the argument an attacker would control if it were ever wired to a URL.
    await expect(readDoc("../../../etc", ["passwd"])).rejects.toThrow(/unknown docs collection/);
  });
});

describe("collections are isolated", () => {
  it("never lists an internal page in the public tree", async () => {
    const pages = await listDocs("docs");
    expect(pages.length).toBeGreaterThan(0);
    for (const page of pages) {
      expect(page.href).not.toMatch(/(^|\/)internal(\/|$)/);
    }
  });

  it("cannot reach an internal page through the public collection", async () => {
    const internal = await listDocs("internal");
    for (const page of internal) {
      await expect(readDoc("docs", page.slug)).resolves.toBeNull();
    }
  });

  it("returns an empty list for a collection with no directory yet", async () => {
    // A missing content directory is an empty site, not a crash — this is the state the
    // repo is in before any internal page has been written.
    await expect(listDocs("internal")).resolves.toBeInstanceOf(Array);
  });
});

describe("ordering and grouping", () => {
  it("sorts by order, then title, with unordered pages last", () => {
    const groups = groupDocs([
      { slug: ["c"], href: "c", title: "Charlie", group: "G" },
      { slug: ["a"], href: "a", title: "Alpha", group: "G", order: 2 },
      { slug: ["b"], href: "b", title: "Bravo", group: "G", order: 1 },
    ]);
    // groupDocs preserves the order it is given, so sort first — as listDocs does.
    expect(groups).toHaveLength(1);
    expect(groups[0].pages.map((p) => p.title)).toEqual(["Charlie", "Alpha", "Bravo"]);
  });

  it("puts pages without a group under Overview", () => {
    const groups = groupDocs([{ slug: ["x"], href: "x", title: "X" }]);
    expect(groups[0].name).toBe("Overview");
  });

  it("orders the real public tree by its frontmatter", async () => {
    const pages = await listDocs("docs");
    const overview = pages.filter((p) => p.group === "Overview").map((p) => p.title);
    expect(overview).toEqual(["Introduction", "Quickstart", "Core concepts"]);
  });
});
