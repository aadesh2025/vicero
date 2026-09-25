import type { Metadata } from "next";
import Link from "next/link";
import { DocsSidebar } from "@/components/docs/docs-sidebar";
import { EndpointGroup } from "@/components/docs/endpoint";
import { countOperations, operationsByTag } from "@/lib/docs/openapi";
import { groupDocs, listDocs } from "@/lib/docs/content";

export const metadata: Metadata = {
  title: "Endpoint reference",
  description: "Every public BotForge REST endpoint, generated from the API's own schema.",
};

/**
 * The endpoint reference.
 *
 * A real route rather than an MDX page, because it renders generated data rather than
 * prose. It is a static segment, so it takes precedence over the `[[...slug]]` catch-all
 * alongside it.
 */
export default async function ApiReferencePage() {
  const groups = operationsByTag("public");
  const total = countOperations(groups);
  const nav = groupDocs(await listDocs("docs"));

  return (
    <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-10 px-4 py-10 md:px-6 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_200px]">
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <DocsSidebar basePath="/docs" groups={nav} />
      </aside>

      <article className="min-w-0">
        <header className="mb-8">
          <h1 className="font-display text-3xl font-semibold tracking-tight text-text">
            Endpoint reference
          </h1>
          <p className="mt-3 text-base text-muted">
            {total} endpoints, generated from the API&apos;s own schema — so this page cannot drift
            from what the server actually serves. Base path is <code>/v1</code>. See{" "}
            <Link href="/docs/api/authentication" className="underline decoration-border-strong">
              authentication
            </Link>{" "}
            for how to call them.
          </p>
        </header>

        {groups.map((group) => (
          <EndpointGroup key={group.tag} tag={group.tag} operations={group.operations} />
        ))}
      </article>

      <aside className="hidden xl:sticky xl:top-20 xl:block xl:self-start">
        <nav aria-label="Resources" className="text-sm">
          <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
            Resources
          </h2>
          <ul className="flex flex-col gap-1 border-l border-border">
            {groups.map((group) => (
              <li key={group.tag}>
                <a
                  href={`#${group.tag}`}
                  className="-ml-px block border-l border-transparent py-0.5 pl-3 capitalize text-muted transition-colors hover:text-text"
                >
                  {group.tag.replace(/-/g, " ")}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
    </div>
  );
}
