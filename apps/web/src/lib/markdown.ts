/** A deliberately small Markdown subset for Help Center articles.
 *
 * Not a spec-complete parser — headings, emphasis, code, links, lists and paragraphs cover
 * what a help article needs, and a full parser is a dependency (and an attack surface) this
 * doesn't earn yet.
 *
 * Security: the input is escaped **before** any formatting is applied, so raw HTML in an
 * article body renders as visible text and can never become live markup. Every tag emitted
 * below is one this function wrote itself.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Inline spans, applied to already-escaped text. */
function inline(text: string): string {
  return (
    text
      // `code` first: its contents shouldn't then be treated as emphasis.
      .replace(/`([^`]+)`/g, '<code class="rounded bg-surface-2 px-1 py-0.5 text-[0.9em]">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
      // Only http(s) links — the escaping above already neutralised javascript: URLs, and
      // this keeps the emitted href to something a browser will treat as navigation.
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" rel="noreferrer noopener" target="_blank" class="text-accent underline">$1</a>',
      )
  );
}

export function renderMarkdown(source: string): string {
  const lines = escapeHtml(source).split(/\r?\n/);
  const out: string[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${inline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (listItems.length) {
      out.push(`<ul class="list-disc space-y-1 pl-5">${listItems.join("")}</ul>`);
      listItems = [];
    }
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCodeBlock) {
        out.push(
          `<pre class="overflow-x-auto rounded-md border border-border bg-surface-2 p-3 text-sm"><code>${codeLines.join("\n")}</code></pre>`,
        );
        codeLines = [];
        inCodeBlock = false;
      } else {
        flushAll();
        inCodeBlock = true;
      }
      continue;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushAll();
      const level = heading[1].length;
      const size = ["text-2xl", "text-xl", "text-lg", "text-base"][level - 1];
      out.push(
        `<h${level} class="font-display ${size} font-semibold text-text">${inline(heading[2])}</h${level}>`,
      );
      continue;
    }

    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      listItems.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }

    if (!line.trim()) {
      flushAll();
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }

  // An unterminated ``` fence still renders its contents rather than swallowing them.
  if (inCodeBlock && codeLines.length) {
    out.push(
      `<pre class="overflow-x-auto rounded-md border border-border bg-surface-2 p-3 text-sm"><code>${codeLines.join("\n")}</code></pre>`,
    );
  }
  flushAll();
  return out.join("\n");
}
