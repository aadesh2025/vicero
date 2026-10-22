import type { ApiIngestProgress } from "@/lib/api/types";

/**
 * What a busy document is doing, in words, and how far along it is (0-100, or null when the stage
 * has no countable total). Embedding is ~99% of ingest time, so it is the one that gets numbers.
 */
export function describeProgress(p: ApiIngestProgress | null | undefined): {
  label: string;
  percent: number | null;
} {
  if (!p) return { label: "Waiting for a worker", percent: null };
  const pct = p.total > 0 ? Math.min(100, Math.round((p.done / p.total) * 100)) : null;
  switch (p.stage) {
    case "crawling":
      return { label: `Reading website · ${p.done}/${p.total} pages`, percent: pct };
    case "embedding":
      return { label: `Embedding · ${p.done}/${p.total} chunks`, percent: pct };
    case "fetching":
      return { label: "Fetching page", percent: null };
    case "reading":
      return { label: "Reading file", percent: null };
    case "chunking":
      return { label: "Splitting into chunks", percent: null };
    case "saving":
      return { label: "Saving", percent: 100 };
    default:
      return { label: "Starting", percent: null };
  }
}

/** "1.8 MB of 3.2 MB" style text for an upload in flight. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Run `task` over `items` with at most `limit` in flight. Resolves when all have settled. */
export async function runLimited<T>(
  items: T[],
  limit: number,
  task: (item: T) => Promise<void>,
): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++];
      try {
        await task(item);
      } catch {
        /* each task reports its own failure */
      }
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker));
}
