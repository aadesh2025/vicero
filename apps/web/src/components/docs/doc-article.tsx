import { MDXRemote } from "next-mdx-remote/rsc";
import { mdxOptions } from "@/lib/docs/mdx";
import { docsComponents } from "./mdx-components";

/**
 * One rendered documentation page: title, standfirst, body.
 *
 * Shared by the public pages and the staff-only route handler so both compile through the
 * same `mdxOptions` — highlighting, heading anchors and GFM tables behave identically, and
 * a change to one cannot quietly diverge from the other.
 */
export function DocArticle({
  title,
  description,
  body,
}: {
  title: string;
  description?: string;
  body: string;
}) {
  return (
    <article className="min-w-0">
      <header className="mb-8">
        <h1 className="font-display text-3xl font-semibold tracking-tight text-text">{title}</h1>
        {description && <p className="mt-3 text-base text-muted">{description}</p>}
      </header>
      <div className="prose prose-botforge max-w-none">
        <MDXRemote source={body} options={{ mdxOptions }} components={docsComponents} />
      </div>
    </article>
  );
}
