import { describe, expect, it } from "vitest";
import { extractToc } from "./toc";

describe("extractToc", () => {
  it("keeps underscores, so the link matches the id the page generates", () => {
    // rehype-slug turns "Customer API keys (bf_)" into an id that still ends in "bf_".
    const [entry] = extractToc("## Customer API keys (`bf_`)\n");
    expect(entry.text).toBe("Customer API keys (bf_)");
    expect(entry.id).toBe("customer-api-keys-bf_");
  });

  it("strips emphasis and code marks from the label", () => {
    expect(extractToc("## The **bold** `code` part\n")[0].text).toBe("The bold code part");
  });

  it("reduces a link to its text", () => {
    expect(extractToc("## See [the guide](/docs/x)\n")[0].text).toBe("See the guide");
  });

  it("numbers repeated headings the way the page does", () => {
    const ids = extractToc("## Notes\n\ntext\n\n## Notes\n").map((e) => e.id);
    expect(ids).toEqual(["notes", "notes-1"]);
  });

  it("ignores headings inside fenced code and the h1 title", () => {
    const entries = extractToc("# Title\n\n```bash\n## not a heading\n```\n\n### Real\n");
    expect(entries.map((e) => e.text)).toEqual(["Real"]);
  });
});
