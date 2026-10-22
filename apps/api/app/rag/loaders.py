"""Document loaders — extract plain text by source type / mime type (docs/06 §2)."""

from __future__ import annotations

import csv
import io
import re

import httpx
import trafilatura

from app.core.logging import get_logger
from app.rag import crawler, web

log = get_logger("rag.loaders")

_TAG_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n\s*")


class LoaderError(Exception):
    """Raised when a document cannot be parsed into text."""


#: Below this, trafilatura probably returned a stub (a cookie wall, a JS-only shell) rather
#: than an article — fall back rather than ingest a sentence and call it a document.
_MIN_EXTRACTED_CHARS = 200


def strip_html(html: str) -> str:
    """Last-resort text extraction: drop every tag and keep what's left.

    Structure-blind by nature — nav menus, headers and footers end up in the same stream as
    the article. Kept only as the fallback for pages `extract_main_content` can't parse.
    """
    html = _TAG_RE.sub(" ", html)
    text = _HTML_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text)
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def extract_main_content(html: str, url: str | None = None) -> str:
    """The page's article plus every substantive line trafilatura left out.

    trafilatura (added for the docs-site nav-menu bug, see the test file) is built to find *the
    article*. On a business site there is no article: there are pricing cards, a services grid and
    a contact block, which trafilatura drops as boilerplate. That is how a client's homepage became
    a handful of chunks with no prices and no phone number.

    So both run. trafilatura's article comes first, because it is clean and in reading order.
    `web.extract_page` (every visible block except navigation menus) supplies the rest, but only
    lines with real content: five or more words, or a number, price, email or phone. Menu labels
    ("Forum", "Get started") are one to three words with no digits, so they still stay out. The
    page title, meta description and JSON-LD facts are added when the article does not have them.

    When trafilatura finds nothing usable (a landing page, a broken page), the full-recall text is
    used as it is, and `strip_html` remains the last resort.
    """
    full = web.extract_page(html, url)
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=False,
            favor_recall=True,
        )
    except Exception as exc:  # a parser failure must not fail the whole ingest
        log.warning("trafilatura_extract_failed", url=url, error=str(exc))
        extracted = None
    article = (extracted or "").strip()

    if len(article) < _MIN_EXTRACTED_CHARS:
        log.info("url_extract_full_recall", url=url, article_chars=len(article), full_chars=len(full.text))
        return full.as_markdown() or strip_html(html)

    seen = _squash(article)
    missing: list[str] = []
    for line in full.text.split("\n"):
        bare = line.strip().lstrip("-#| ").strip()
        key = _squash(bare)
        if not key or key in seen or not _substantive(bare):
            continue
        seen += " " + key
        missing.append(bare)
    parts: list[str] = []
    if full.title and _squash(full.title) not in _squash(article[:400]):
        parts.append(f"# {full.title}")
    parts.append(article)
    if full.description and _squash(full.description) not in seen:
        parts.append(full.description)
    if missing:
        parts.append("\n".join(missing))
    if full.structured:
        parts.append(full.structured)
    return "\n\n".join(parts).strip()


_SQUASH_RE = re.compile(r"[\W_]+", re.UNICODE)
_FACT_RE = re.compile(r"\d|@|[$€£₹¥]")


def _squash(text: str) -> str:
    """Lower-case, punctuation-free form for "is this line already in the article" checks."""
    return _SQUASH_RE.sub(" ", text.lower()).strip()


def _substantive(line: str) -> bool:
    """A line worth adding back: a sentence, or anything carrying a number, price or address."""
    words = len(line.split())
    return words >= 5 or (words >= 2 and bool(_FACT_RE.search(line)))


async def load_url(url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Fetch one page or text file. Every hop is SSRF-checked, not just the first: a public URL
    that 302s to a private or metadata address must not be followed (`crawler.fetch`). The name is
    resolved again by httpx after the check; the process DNS cache (`core.dnscache`) pins the
    answer for its TTL, which narrows that rebinding race without closing it."""
    async with httpx.AsyncClient(follow_redirects=False, transport=transport) as client:
        try:
            page = await crawler.fetch(client, url, transport=transport)
        except crawler.CrawlError as exc:
            raise LoaderError(str(exc)) from exc
    if crawler.is_html(page):
        return extract_main_content(page.body, page.url)
    return page.body.strip()


def pdf_page_count(data: bytes) -> int | None:
    """Page count, or `None` if the file is not a readable PDF.

    Cheap: pypdf reads the cross-reference table, not the page content. Deliberately returns
    `None` rather than raising on a corrupt file — a page cap has no business being the thing
    that decides an encrypted or malformed PDF cannot be ingested. The extractor gets to make
    that call, with its own error message.
    """
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return None


def load_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p)


def load_docx(data: bytes) -> str:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())


def load_csv(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return ""
    header, *body = rows
    lines = []
    for row in body:
        pairs = [f"{h}: {v}" for h, v in zip(header, row, strict=False) if v]
        if pairs:
            lines.append("; ".join(pairs))
    return "\n".join(lines) if lines else "\n".join(", ".join(r) for r in rows)


def load_bytes(data: bytes, *, filename: str | None, mime_type: str | None) -> str:
    """Dispatch to a parser by mime type / extension."""
    name = (filename or "").lower()
    mime = (mime_type or "").lower()
    if "pdf" in mime or name.endswith(".pdf"):
        return load_pdf(data)
    if "word" in mime or "officedocument.wordprocessing" in mime or name.endswith(".docx"):
        return load_docx(data)
    if "csv" in mime or name.endswith(".csv"):
        return load_csv(data)
    # txt, markdown, json, or anything else → decode as text.
    return data.decode("utf-8", errors="replace").strip()
