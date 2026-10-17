"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ImageOff } from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { apiBlob } from "@/lib/api/client";
import type { ApiAttachment, ApiMessage } from "@/lib/api/conversations";

/** Placeholder text the server stores when a message is only a photo. Not worth showing next to the photo. */
const PLACEHOLDERS = new Set(["[Photo]", "[Customer sent a photo]"]);

/** The text of a bubble, minus the system placeholder and minus the vision line (the photo itself shows it). */
export function visibleText(m: ApiMessage): string | null {
  const text = m.content ?? "";
  if (!m.attachments?.length) return text;
  const stripped = text
    .split("\n")
    .filter((line) => !/^\[Customer sent a photo/.test(line) && !PLACEHOLDERS.has(line))
    .join("\n")
    .trim();
  return stripped || null;
}

/** An authenticated <img>: the file is fetched with the session's token and shown from a blob URL. */
function useAuthedImage(mediaId: string) {
  const query = useQuery({
    queryKey: ["media", mediaId],
    queryFn: () => apiBlob(`/v1/media/${mediaId}`),
    staleTime: 5 * 60_000,
    retry: false,
  });
  // Derived, not stored: the blob URL is a pure function of the fetched bytes. The effect only cleans up.
  const url = useMemo(() => (query.data ? URL.createObjectURL(query.data) : null), [query.data]);
  useEffect(() => () => (url ? URL.revokeObjectURL(url) : undefined), [url]);
  return { url, isLoading: query.isLoading, isError: query.isError };
}

function Thumb({ attachment }: { attachment: ApiAttachment }) {
  const { url, isLoading, isError } = useAuthedImage(attachment.media_id);
  const [open, setOpen] = useState(false);
  const alt = attachment.description || (attachment.direction === "in" ? "Photo from the customer" : "Photo sent");

  if (isLoading) return <Skeleton className="size-40 rounded-xl" aria-label="Loading photo" />;
  if (isError || !url) {
    return (
      <div className="flex size-40 flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-border-strong p-3 text-center text-xs font-semibold text-faint">
        <ImageOff className="size-5" aria-hidden />
        {attachment.direction === "in" ? "Photo deleted after 30 days" : "Photo unavailable"}
      </div>
    );
  }
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Enlarge: ${alt}`}
        className="block overflow-hidden rounded-xl border border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- blob: URL, not optimisable by next/image */}
        <img src={url} alt={alt} className="max-h-60 max-w-full object-cover" />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl">
          <DialogTitle className="sr-only">Photo</DialogTitle>
          {/* eslint-disable-next-line @next/next/no-img-element -- blob: URL */}
          <img src={url} alt={alt} className="mx-auto max-h-[75vh] max-w-full rounded-lg object-contain" />
          {attachment.description && <p className="mt-3 text-xs font-medium text-muted">{attachment.description}</p>}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function MessageImages({ attachments }: { attachments: ApiAttachment[] }) {
  return (
    <div className="mb-1 flex flex-wrap gap-2">
      {attachments.map((a) => (
        <Thumb key={a.media_id} attachment={a} />
      ))}
    </div>
  );
}
