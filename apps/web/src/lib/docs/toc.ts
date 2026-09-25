import GithubSlugger from "github-slugger";

export interface TocEntry {
  depth: 2 | 3;
  text: string;
  id: string;
}

/** Fenced code blocks, so a `# comment` inside a shell example never becomes a heading. */
const FENCE_RE = /^(```|~~~)/;
const HEADING_RE = /^(#{2,3})\s+(.+?)\s*#*\s*$/;

/**
 * Pull the on-page contents out of raw MDX.
 *
 * Reads the source rather than the rendered tree because the page is compiled to React
 * elements, which are awkward to walk after the fact. The ids come from the same
 * `github-slugger` that `rehype-slug` uses, and a single slugger instance is reused across
 * the page so its duplicate-suffix counter (`overview`, `overview-1`) matches what ends up
 * in the HTML — a fresh instance per heading would silently break links on any page that
 * repeats a heading.
 *
 * h1 is skipped: the page title is rendered from frontmatter, not from the body.
 */
export function extractToc(body: string): TocEntry[] {
  const slugger = new GithubSlugger();
  const entries: TocEntry[] = [];
  let inFence = false;

  for (const line of body.split(/\r?\n/)) {
    if (FENCE_RE.test(line.trim())) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;

    const match = HEADING_RE.exec(line);
    if (!match) continue;

    // Strip the inline markup a heading can carry, so the contents list reads as plain
    // text: `**Bold**`, `` `code` ``, and `[label](href)` all reduce to their text.
    const text = match[2]
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/[*_`]/g, "")
      .trim();
    if (!text) continue;

    entries.push({ depth: match[1].length as 2 | 3, text, id: slugger.slug(text) });
  }

  return entries;
}
