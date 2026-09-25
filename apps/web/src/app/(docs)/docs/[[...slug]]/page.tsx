import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DocArticle } from "@/components/docs/doc-article";
import { DocsSidebar } from "@/components/docs/docs-sidebar";
import { Toc } from "@/components/docs/toc";
import { groupDocs, listDocs, readDoc } from "@/lib/docs/content";
import { extractToc } from "@/lib/docs/toc";

/** `/docs` itself renders `introduction`, so the index is a real page rather than a stub
 *  that has to be kept in step with whatever the first section happens to be. */
const INDEX_SLUG = ["introduction"];

type Params = { slug?: string[] };

export async function generateStaticParams(): Promise<Params[]> {
  const docs = await listDocs("docs");
  // The empty slug renders the index; Next needs it listed explicitly.
  return [{ slug: [] }, ...docs.map((doc) => ({ slug: doc.slug }))];
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { slug } = await params;
  const doc = await readDoc("docs", slug?.length ? slug : INDEX_SLUG);
  if (!doc) return {};
  return {
    title: doc.title,
    description: doc.description,
    openGraph: { title: doc.title, description: doc.description, type: "article" },
  };
}

export default async function DocsPage({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const doc = await readDoc("docs", slug?.length ? slug : INDEX_SLUG);
  if (!doc) notFound();

  const groups = groupDocs(await listDocs("docs"));

  return (
    <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-10 px-4 py-10 md:px-6 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_200px]">
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <DocsSidebar basePath="/docs" groups={groups} />
      </aside>

      <DocArticle title={doc.title} description={doc.description} body={doc.body} />

      <aside className="hidden xl:sticky xl:top-20 xl:block xl:self-start">
        <Toc entries={extractToc(doc.body)} />
      </aside>
    </div>
  );
}
