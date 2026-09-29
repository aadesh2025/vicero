"""Document loaders — extract plain text by source type / mime type (docs/06 §2)."""

from __future__ import annotations

import asyncio
import csv
import io
import re
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from app.core.logging import get_logger
from app.core.ssrf import is_blocked_host

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
    """Article text, with nav/header/footer/sidebar boilerplate removed.

    The regex strip this replaces had no notion of document structure, so on a docs site the
    nav menu ("Docs Forum Changelog Get started Deploy Build Nodes…") landed in the same
    text stream as the content and dominated the first chunk. trafilatura is built for this
    one job.

    Markdown output keeps heading structure, which the recursive chunker splits on — so
    chunks land on section boundaries instead of mid-sentence.
    """
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=False,
        )
    except Exception as exc:  # a parser failure must not fail the whole ingest
        log.warning("trafilatura_extract_failed", url=url, error=str(exc))
        extracted = None

    if extracted and len(extracted.strip()) >= _MIN_EXTRACTED_CHARS:
        return extracted.strip()

    log.info(
        "trafilatura_fallback_to_strip_html",
        url=url,
        extracted_chars=len(extracted.strip()) if extracted else 0,
    )
    return strip_html(html)


_MAX_REDIRECTS = 5


async def _host_blocked(host: str, transport: httpx.AsyncBaseTransport | None) -> bool:
    # Skip the SSRF DNS check when a test transport is injected (no real network). The lookup
    # is blocking, so it runs off the event loop.
    if transport is not None:
        return False
    return await asyncio.to_thread(is_blocked_host, host)


async def load_url(url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Fetch a page or text file. Every hop is SSRF-checked, not just the first: a public URL
    that 302s to a private or metadata address must not be followed. (The name is resolved
    again by httpx after the check, so a DNS-rebinding host can still race it — closing that
    needs the resolved IP pinned on the connection.)"""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, transport=transport) as client:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise LoaderError("Only http(s) URLs are supported.")
            if await _host_blocked(parsed.hostname, transport):
                raise LoaderError("Refusing to fetch a private/loopback URL.")
            resp = await client.get(current, headers={"User-Agent": "Vicero-Ingest/1.0"})
            location = resp.headers.get("location")
            if resp.is_redirect and location:
                current = urljoin(current, location)
                continue
            resp.raise_for_status()
            break
        else:
            raise LoaderError("Too many redirects.")
        content_type = resp.headers.get("content-type", "")
        body = resp.text
    if "html" in content_type or body.lstrip().lower().startswith(("<!doctype", "<html")):
        return extract_main_content(body, url)
    return body.strip()


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
