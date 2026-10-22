"""Full-recall HTML → text for knowledge ingest, plus the links and metadata a crawler needs.

**Why a second extractor next to trafilatura.** `loaders.extract_main_content` asks trafilatura
for the *article*. That is right for a blog post and wrong for a business website, which is what
clients actually paste: trafilatura drops pricing cards, feature grids, contact blocks and the
footer (address, phone, opening hours) as "boilerplate". Those are precisely the facts a support
bot gets asked about. A live import of a client site on 2026-10-21 produced 7 chunks.

This extractor keeps **every visible block** and preserves structure as light markdown (headings,
list items, table rows), so the chunker still splits on section boundaries. Two things are
removed instead of guessed at:

* elements that are never visible text: ``script``, ``style``, ``noscript``, ``svg``, ``template``,
  ``iframe``, ``canvas`` (``<head>`` only ever contributes the title, description and JSON-LD);
* navigation menus (``<nav>`` and ``role="navigation"``). They are lists of link labels, carry no
  facts, and are the reason the regex stripper this repo started with put "Docs Forum Changelog"
  at the top of every first chunk. The crawler reads the links themselves separately.

Headers and footers are **kept** on purpose. On a single page they hold the phone number and the
address. Across a crawl, the repetition is removed by `dedupe_boilerplate`, which keeps one copy.

**Pages that only render in a browser.** A React/Vue/Three.js single-page app ships an empty
``<div id="root">`` and builds its text with JavaScript, which no HTTP fetch can run. For those,
what *is* in the HTML is collected: the title, the meta/OpenGraph description and any JSON-LD
structured data (business name, phone, address, opening hours, prices). `looks_client_rendered`
lets the caller say so honestly instead of storing an empty document as if it were complete.

Stdlib `html.parser` only: no new dependency, tolerant of broken markup, and it never fetches.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "nav", "select", "object"}
)
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "caption", "dd", "details", "dialog", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "header", "hr", "html", "legend",
        "main", "ol", "p", "pre", "section", "summary", "table", "tbody", "tfoot", "thead", "ul",
    }
)
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_WS_RE = re.compile(r"[ \t\r\f\v\u00a0]+")
_BLANKS_RE = re.compile(r"\n{3,}")
_SHELL_RE = re.compile(r"""<div[^>]+id=["'](?:root|app|__next|__nuxt|svelte)["'][^>]*>\s*</div>""", re.IGNORECASE)

#: JSON-LD keys worth keeping. Everything else in structured data is identifiers and URLs.
_LD_KEYS = (
    "name", "alternateName", "description", "slogan", "telephone", "email", "faxNumber", "address",
    "streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry", "openingHours",
    "openingHoursSpecification", "dayOfWeek", "opens", "closes", "priceRange", "price", "priceCurrency",
    "areaServed", "founder", "foundingDate", "question", "acceptedAnswer", "text", "offers", "mainEntity",
    "itemListElement", "serviceType", "brand", "sku", "availability",
)


@dataclass(slots=True)
class ExtractedPage:
    title: str = ""
    description: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    structured: str = ""
    canonical: str | None = None
    noindex: bool = False

    def as_markdown(self) -> str:
        """Title, description, body and structured data, in that order, without duplicates."""
        parts: list[str] = []
        body = self.text.strip()
        if self.title and not body.lower().startswith(("# " + self.title).lower()):
            parts.append(f"# {self.title}")
        if self.description and self.description not in body:
            parts.append(self.description)
        if body:
            parts.append(body)
        if self.structured:
            parts.append(self.structured)
        return "\n\n".join(parts).strip()


class _Parser(HTMLParser):
    def __init__(self, base_url: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.page = ExtractedPage()
        self._out: list[str] = []
        self._skip_depth = 0
        self._skip_stack: list[str] = []
        self._in_title = False
        self._title: list[str] = []
        self._in_ld = False
        self._ld: list[str] = []
        self._ld_blocks: list[str] = []
        self._cell_open = False
        self._meta: dict[str, str] = {}

    # ── helpers ──
    def _newline(self) -> None:
        if self._out and not self._out[-1].endswith("\n"):
            self._out.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            # Only the document title: an inline <svg> carries its own <title> per icon
            # ("x-twitter", "discord"), and those must not become the page's name.
            if not self._skip_depth and not self._title:
                self._in_title = True
            return
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self._meta[key] = a["content"]
            return
        if tag == "link" and "canonical" in a.get("rel", "").lower() and a.get("href"):
            self.page.canonical = urljoin(self.base_url or "", a["href"])
            return
        if tag == "a" and a.get("href"):
            # Collected even inside a skipped <nav>: the menu's text is noise, its links are the map.
            self.page.links.append(urljoin(self.base_url or "", a["href"]))
        if tag == "script":
            if "ld+json" in a.get("type", "").lower():
                self._in_ld = True
                self._ld = []
            if tag not in _VOID_TAGS:
                self._push_skip(tag)
            return
        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._push_skip(tag)
            return
        if tag in _SKIP_TAGS or a.get("role", "").lower() == "navigation" or "hidden" in a:
            if tag not in _VOID_TAGS:
                self._push_skip(tag)
            return
        if tag in _HEADINGS:
            self._newline()
            self._out.append("\n" + "#" * _HEADINGS[tag] + " ")
        elif tag == "li":
            self._newline()
            self._out.append("- ")
        elif tag == "tr":
            self._newline()
            self._cell_open = False
        elif tag in ("td", "th"):
            if self._cell_open:
                self._out.append(" | ")
            self._cell_open = True
        elif tag == "br":
            self._out.append("\n")
        elif tag in _BLOCK_TAGS:
            self._newline()
        elif tag == "img" and a.get("alt", "").strip():
            # Alt text is often the only place a logo-as-image site names its partners or products.
            self._out.append(f" {a['alt'].strip()} ")

    def _push_skip(self, tag: str) -> None:
        self._skip_stack.append(tag)
        self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            return
        if self._skip_depth:
            if tag in self._skip_stack:
                # Pop to the matching open tag; tolerates unclosed children inside a skipped block.
                while self._skip_stack:
                    self._skip_depth -= 1
                    if self._skip_stack.pop() == tag:
                        break
                if tag == "script" and self._in_ld:
                    self._in_ld = False
                    self._ld_blocks.append("".join(self._ld))
            return
        if tag in _HEADINGS or tag in _BLOCK_TAGS or tag in ("li", "tr"):
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
            return
        if self._in_ld:
            self._ld.append(data)
            return
        if self._skip_depth:
            return
        self._out.append(data)

    def result(self) -> ExtractedPage:
        page = self.page
        page.title = _clean_line(" ".join(self._title)) or _clean_line(self._meta.get("og:title", ""))
        page.description = _clean_line(self._meta.get("description") or self._meta.get("og:description") or "")
        page.noindex = "noindex" in self._meta.get("robots", "").lower()
        page.text = _normalise("".join(self._out))
        page.structured = _structured_text(self._ld_blocks)
        return page


def _clean_line(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _normalise(text: str) -> str:
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line in {"-", "|", "#", "##", "###"}:
            continue
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(line)
    return _BLANKS_RE.sub("\n\n", "\n".join(out)).strip()


def _flatten_ld(node: Any, out: list[str], depth: int = 0) -> None:
    if depth > 6 or len(out) > 200:
        return
    if isinstance(node, list):
        for item in node:
            _flatten_ld(item, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    for key in _LD_KEYS:
        if key not in node:
            continue
        value = node[key]
        if isinstance(value, str | int | float) and str(value).strip():
            out.append(f"{key}: {_clean_line(str(value))}")
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            out.append(f"{key}: {', '.join(_clean_line(v) for v in value)}")
        else:
            _flatten_ld(value, out, depth + 1)
    for child in node.get("@graph", []) if isinstance(node.get("@graph"), list) else []:
        _flatten_ld(child, out, depth + 1)


def _structured_text(blocks: list[str]) -> str:
    facts: list[str] = []
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        _flatten_ld(data, facts)
    unique = list(dict.fromkeys(facts))
    return ("Structured data:\n" + "\n".join(unique)) if unique else ""


def extract_page(markup: str, url: str | None = None) -> ExtractedPage:
    """Every visible block of `markup` as light markdown, plus its links and metadata. Never raises."""
    parser = _Parser(url)
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # html.parser is lenient, but a pathological page must not fail the ingest
        pass
    return parser.result()


def looks_client_rendered(markup: str, extracted_text: str) -> bool:
    """True for a JavaScript-only shell: little visible text and an empty app mount point."""
    if len(extracted_text) >= 400:
        return False
    if _SHELL_RE.search(markup):
        return True
    scripts = markup.lower().count("<script")
    return scripts >= 5 and len(extracted_text) < 200


def dedupe_boilerplate(pages: list[str], *, min_pages: int = 3, share: float = 0.6) -> tuple[list[str], str]:
    """Remove lines repeated on most pages (header, footer, cookie banner), keeping one shared copy.

    Returns ``(pages_without_repeats, shared_block)``. The shared block is not thrown away: on a
    business site the footer is the address, phone number and opening hours, which must be in the
    knowledge base exactly once rather than in every chunk of every page (where it would also
    dominate retrieval).
    """
    if len(pages) < min_pages:
        return pages, ""
    counts: dict[str, int] = {}
    for page in pages:
        for line in {ln.strip() for ln in page.split("\n") if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1
    threshold = max(min_pages, int(len(pages) * share + 0.5))
    repeated = {line for line, n in counts.items() if n >= threshold}
    if not repeated:
        return pages, ""
    shared: list[str] = []
    for line in pages[0].split("\n"):
        if line.strip() in repeated and line.strip() not in shared:
            shared.append(line.strip())
    cleaned = [
        _normalise("\n".join(ln for ln in page.split("\n") if ln.strip() not in repeated)) for page in pages
    ]
    # Lines that are only a heading marker or a lone word left behind are noise.
    return cleaned, "\n".join(s for s in shared if len(s) > 2)
