/** Shared MDX compile configuration for the documentation site.
 *
 * Both the public docs pages and the staff-only internal route handler compile through
 * here, so a change to highlighting or heading anchors lands on both at once.
 *
 * This is deliberately separate from `src/lib/markdown.ts`, which stays as it is: that
 * renders *tenant-authored* Help Center articles and escapes its input before formatting,
 * so a customer cannot inject markup. The content compiled here is authored in this repo
 * and reviewed in git, which is what makes the far larger MDX surface acceptable. Never
 * point this at user-submitted text.
 */

import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import rehypeShiki from "@shikijs/rehype";
import type { MDXRemoteProps } from "next-mdx-remote/rsc";

type MdxOptions = NonNullable<NonNullable<MDXRemoteProps["options"]>["mdxOptions"]>;

/** Both themes are emitted at once and switched by CSS, because the app's theme lives in a
 *  `.dark` class rather than a media query — a single-theme highlight would be wrong in one
 *  of the two modes. `defaultColor: false` stops Shiki inlining one theme as the default and
 *  leaves both on CSS variables for globals.css to switch. */
const shikiOptions = {
  // The standard GitHub themes fail AA in places — `github-light` sets Python keyword
  // arguments at ~3.5:1 on white, and `github-dark` sets comments at ~3.0:1 — and the axe
  // gate catches both. The high-contrast variants are GitHub's own accessible palettes.
  themes: { light: "github-light-high-contrast", dark: "github-dark-high-contrast" },
  defaultColor: false,
  cssVariablePrefix: "--shiki-",
} as const;

export const mdxOptions: MdxOptions = {
  remarkPlugins: [remarkGfm],
  rehypePlugins: [
    rehypeSlug,
    // `wrap` keeps the heading text as the link's content, so an anchor never changes how a
    // heading reads to a screen reader.
    [rehypeAutolinkHeadings, { behavior: "wrap", properties: { className: ["heading-anchor"] } }],
    [rehypeShiki, shikiOptions],
  ],
  format: "mdx",
};

/** Frontmatter every docs page carries. `order` sorts within a group; pages without one
 *  sort last, alphabetically, so a new file is never silently hidden. */
export interface DocFrontmatter {
  title: string;
  description?: string;
  group?: string;
  order?: number;
}
