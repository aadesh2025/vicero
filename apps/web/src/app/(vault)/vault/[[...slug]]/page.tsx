import { notFound, redirect } from "next/navigation";
import { DocArticle } from "@/components/docs/doc-article";
import { DocsSidebar } from "@/components/docs/docs-sidebar";
import { internalComponents } from "@/components/docs/internal-components";
import { Toc } from "@/components/docs/toc";
import { groupDocs, listDocs, readDoc } from "@/lib/docs/content";
import { extractToc } from "@/lib/docs/toc";
import { getVaultSession } from "@/lib/vault/session";

/**
 * The private reference: architecture, auth, the data model, every endpoint, and the real
 * configuration values behind a Reveal button.
 *
 * A server component, and the session check is the first thing it does — before a byte of
 * content is read from disk. A visitor without a vault session is redirected to the login
 * page and receives none of it, which is the difference between this and a client-side
 * guard: a redirect that fires *after* the content shipped protects nothing.
 */

// Never prerendered, never cached: a static copy would be served without running the check,
// and every request must re-verify (the allow-list can change under a live cookie).
export const dynamic = "force-dynamic";

const INDEX_SLUG = ["architecture"];

type Params = { slug?: string[] };

export const metadata = { title: "Reference" };

export default async function VaultPage({ params }: { params: Promise<Params> }) {
  if (!(await getVaultSession())) redirect("/vault/login");

  const { slug } = await params;
  // `readDoc` matches the slug against a directory listing rather than joining it onto a
  // path, so nothing here can be walked out of `content/internal`.
  const doc = await readDoc("internal", slug?.length ? slug : INDEX_SLUG);
  if (!doc) notFound();

  const groups = groupDocs(await listDocs("internal"));

  return (
    <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-10 px-4 py-10 md:px-6 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_200px]">
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <DocsSidebar basePath="/vault" groups={groups} />
      </aside>

      <DocArticle
        title={doc.title}
        description={doc.description}
        body={doc.body}
        components={internalComponents}
      />

      <aside className="hidden xl:sticky xl:top-20 xl:block xl:self-start">
        <Toc entries={extractToc(doc.body)} />
      </aside>
    </div>
  );
}
