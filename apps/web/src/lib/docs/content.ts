/** Reading documentation pages off disk.
 *
 * Two collections live under `content/`, and the difference between them is a security
 * boundary rather than a filing convention:
 *
 *   `content/docs`     public. Anything here is world-readable and statically rendered.
 *   `content/internal` the private admin area only. Served only under `/vault`, behind its own
 *                      sign-in and session check.
 *
 * `collectionRoot` is the only place a collection name becomes a path, and it accepts the
 * two literals above and nothing else. Every read goes through `readDoc`, which resolves
 * the slug against a directory listing rather than joining it onto a path — a slug never
 * reaches the filesystem as text, so `../../.env` has nothing to traverse.
 *
 * Server-only. Importing this from a client component would bundle the internal content
 * into the browser payload, which is the one thing the split above exists to prevent.
 */

import "server-only";

import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import matter from "gray-matter";

import type { DocFrontmatter } from "./mdx";

export type Collection = "docs" | "internal";

export interface DocMeta extends DocFrontmatter {
  /** URL path segments, e.g. `["api", "authentication"]`. */
  slug: string[];
  /** The slug joined with `/` — the key used for lookups and links. */
  href: string;
}

export interface Doc extends DocMeta {
  /** Raw MDX body with the frontmatter removed. */
  body: string;
}

const CONTENT_ROOT = join(process.cwd(), "content");

function collectionRoot(collection: Collection): string {
  // An explicit switch, not a template string. This function turns caller-supplied data
  // into a filesystem path, so it accepts exactly two values and throws on anything else.
  switch (collection) {
    case "docs":
      return join(CONTENT_ROOT, "docs");
    case "internal":
      return join(CONTENT_ROOT, "internal");
    default:
      throw new Error(`unknown docs collection: ${String(collection)}`);
  }
}

/** Every `.mdx` file in a collection, as slug arrays. Directories nest into segments. */
async function listSlugs(collection: Collection): Promise<string[][]> {
  const root = collectionRoot(collection);
  let entries;
  try {
    entries = await readdir(root, { recursive: true, withFileTypes: true });
  } catch {
    // A collection with no directory yet is empty, not broken — this is what the site does
    // before any content is written.
    return [];
  }

  return entries
    .filter((e) => e.isFile() && e.name.endsWith(".mdx"))
    .map((e) => {
      // `parentPath` is the absolute directory; the part below the root becomes segments.
      const dir = e.parentPath.slice(root.length).split(/[\\/]/).filter(Boolean);
      const name = e.name.replace(/\.mdx$/, "");
      // `index.mdx` addresses its own directory rather than adding a segment.
      return name === "index" ? dir : [...dir, name];
    })
    .filter((slug) => slug.length > 0);
}

function sameSlug(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((part, i) => part === b[i]);
}

/**
 * Read one page, or `null` if the slug names no page in the collection.
 *
 * The slug is matched against the listing rather than joined onto a path, so a caller
 * cannot reach a file the listing does not contain — no `..`, no absolute path, no symlink
 * outside the root, and no `.env`. `null` (not a throw) so callers can answer 404.
 */
export async function readDoc(collection: Collection, slug: string[]): Promise<Doc | null> {
  const known = await listSlugs(collection);
  const match = known.find((candidate) => sameSlug(candidate, slug));
  if (!match) return null;

  const root = collectionRoot(collection);
  // `match` came from the listing, never from the caller.
  const nested = join(root, ...match, "index.mdx");
  const flat = join(root, ...match) + ".mdx";
  const raw = await readFile(flat, "utf8").catch(() => readFile(nested, "utf8"));

  const { data, content } = matter(raw);
  const frontmatter = data as Partial<DocFrontmatter>;
  return {
    slug: match,
    href: match.join("/"),
    // A page with no title still renders, named after its file — a missing frontmatter
    // line should not take a page off the site.
    title: frontmatter.title ?? match[match.length - 1],
    description: frontmatter.description,
    group: frontmatter.group,
    order: frontmatter.order,
    body: content,
  };
}

/** Every page's metadata, ordered for navigation. */
export async function listDocs(collection: Collection): Promise<DocMeta[]> {
  const slugs = await listSlugs(collection);
  const docs = await Promise.all(slugs.map((slug) => readDoc(collection, slug)));
  return docs
    .filter((doc): doc is Doc => doc !== null)
    // Name the metadata fields rather than spreading and dropping `body`: this value is
    // handed to client components, and an omit-by-destructure would silently start
    // shipping any new field added to `Doc` — including, for the internal collection,
    // the page text itself.
    .map(({ slug, href, title, description, group, order }) => ({
      slug,
      href,
      title,
      description,
      group,
      order,
    }))
    .sort(byOrderThenTitle);
}

/** `order` first, then title. A page without `order` sorts last rather than first, so
 *  forgetting the field never silently promotes a page to the top of a section. */
function byOrderThenTitle(a: DocMeta, b: DocMeta): number {
  const ao = a.order ?? Number.MAX_SAFE_INTEGER;
  const bo = b.order ?? Number.MAX_SAFE_INTEGER;
  if (ao !== bo) return ao - bo;
  return a.title.localeCompare(b.title);
}

export interface DocGroup {
  name: string;
  pages: DocMeta[];
}

/** Pages bucketed by their `group` frontmatter, in the order the groups first appear. */
export function groupDocs(docs: DocMeta[]): DocGroup[] {
  const groups = new Map<string, DocMeta[]>();
  for (const doc of docs) {
    const name = doc.group ?? "Overview";
    const bucket = groups.get(name);
    if (bucket) bucket.push(doc);
    else groups.set(name, [doc]);
  }
  return [...groups].map(([name, pages]) => ({ name, pages }));
}
