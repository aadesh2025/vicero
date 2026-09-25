import { notFound } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { DocArticle } from "@/components/docs/doc-article";
import { DocsSidebar } from "@/components/docs/docs-sidebar";
import { internalComponents } from "@/components/docs/internal-components";
import { SessionRefresh } from "@/components/docs/session-refresh";
import { Toc } from "@/components/docs/toc";
import { groupDocs, listDocs, readDoc } from "@/lib/docs/content";
import { checkStaff } from "@/lib/docs/staff";
import { extractToc } from "@/lib/docs/toc";

/**
 * Platform-staff engineering reference.
 *
 * A server component, deliberately. The gate runs before a single byte of content is read
 * from disk, so a visitor who is not staff receives a refusal and nothing else — unlike the
 * client-side guard used by `/admin`, which relies on a redirect firing before anyone opens
 * devtools. See `lib/docs/staff.ts`.
 */

// Two independent reasons, both load-bearing:
//   1. This content must never be prerendered into a static file at build time. A static
//      page would be served without ever running the gate.
//   2. Every request has to re-check staff status, because it can be revoked.
// `cookies()` already forces dynamic rendering; this states the requirement so that a
// future refactor which stops reading cookies cannot silently make the page static.
export const dynamic = "force-dynamic";

const INDEX_SLUG = ["architecture"];

type Params = { slug?: string[] };

export const metadata = {
  title: "Internal reference",
  // Not that it stops a determined crawler, but nothing here should be indexed.
  robots: { index: false, follow: false },
};

function Denied() {
  return (
    <div className="mx-auto grid min-h-[50vh] max-w-md place-items-center text-center">
      <div>
        <ShieldAlert className="mx-auto mb-3 size-6 text-faint" aria-hidden />
        <h1 className="font-display text-lg font-semibold text-text">Platform staff only</h1>
        <p className="mt-2 text-sm text-muted">
          This section documents BotForge&apos;s internals and is limited to platform staff.
        </p>
      </div>
    </div>
  );
}

export default async function InternalDocsPage({ params }: { params: Promise<Params> }) {
  const access = await checkStaff();
  if (access.status === "expired") return <SessionRefresh />;
  if (access.status !== "ok") return <Denied />;

  const { slug } = await params;
  // `readDoc` matches the slug against a directory listing rather than joining it onto a
  // path, so nothing here can be walked out of `content/internal`.
  const doc = await readDoc("internal", slug?.length ? slug : INDEX_SLUG);
  if (!doc) notFound();

  const groups = groupDocs(await listDocs("internal"));

  return (
    <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-10 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_200px]">
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <DocsSidebar basePath="/internal-docs" groups={groups} />
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
