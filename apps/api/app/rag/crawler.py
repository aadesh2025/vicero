"""Whole-website crawl for knowledge ingest: sitemap + same-site links, SSRF-checked on every hop.

**What it replaces.** URL ingest fetched exactly one page. A client who pasted their homepage got
the homepage, minus whatever trafilatura judged boilerplate, and none of the pricing, services,
FAQ or contact pages. Measured on production 2026-10-21: one website import, 7 chunks.

**How pages are found, in priority order.**

1. The start URL itself.
2. ``sitemap.xml``: the ``Sitemap:`` lines in ``robots.txt``, then ``/sitemap.xml`` and
   ``/sitemap_index.xml``. One level of nested sitemap index is followed. A sitemap is the site
   owner's own list of the pages that matter, so it beats guessing.
3. Breadth-first over ``<a href>`` links found on fetched pages, shallowest first.

**What keeps it bounded and safe.**

* **Same site only.** The start URL's host, with and without ``www.``. A link to a partner or to
  social media is never followed.
* **SSRF on every hop.** Each URL, and each redirect hop of each URL, goes through
  `ssrf.is_blocked_host`, exactly like `loaders.load_url`. A public page linking to
  ``http://169.254.169.254/`` or redirecting to ``localhost`` is refused, not fetched.
* **robots.txt is respected** for ``*`` and ``ViceroBot``.
* **Caps:** `max_pages` (the caller's choice, hard-limited by `crawl_max_pages_limit`), a total
  time budget, a per-page byte cap and a small concurrency. A crawl that hits a cap stops and
  keeps what it has: a partial knowledge base beats a failed one.
* **Never fetched:** binaries and media by extension, and account/cart/login/admin paths, which
  hold no business facts and can have side effects.

**What it cannot do.** It does not run JavaScript. A page whose text is built in the browser
yields only its title, description and structured data, and is reported in `CrawlResult.warnings`
so the UI can say so. Rendering would mean shipping a headless browser in the worker image and
re-implementing the SSRF checks for every subresource it loads (docs/14 §9's rule that a new code
path must not bypass an existing control). That trade is deferred, and recorded in ADR-139.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.ssrf import is_blocked_host
from app.rag import web

log = get_logger("rag.crawler")

#: A browser-shaped User-Agent that still names the bot. The previous ``Vicero-Ingest/1.0`` was
#: refused outright by common WAF/CDN bot rules, so a client's own site returned 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 ViceroBot/1.0"
)
ROBOTS_AGENT = "ViceroBot"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    "Accept-Language": "en;q=0.9,*;q=0.5",
}

MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 5 * 1024 * 1024
PAGE_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_MIN_PAGE_CHARS = 40

_SKIP_EXT = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|bmp|tiff?|avif|mp4|webm|mov|avi|mkv|mp3|wav|ogg|m4a|flac|"
    r"zip|rar|7z|gz|tgz|tar|exe|dmg|msi|apk|iso|bin|pdf|docx?|xlsx?|pptx?|odt|csv|"
    r"css|js|mjs|map|json|xml|rss|atom|woff2?|ttf|otf|eot)$",
    re.IGNORECASE,
)
_SKIP_PATH = re.compile(
    r"/(?:wp-admin|wp-login|wp-json|xmlrpc\.php|cart|checkout|basket|my-account|account|login|logout|"
    r"signin|sign-in|signup|sign-up|register|cdn-cgi|feed)(?:/|$)",
    re.IGNORECASE,
)
_TRACKING_PARAMS = re.compile(r"^(?:utm_[a-z]+|fbclid|gclid|mc_[a-z]+|ref|_ga|yclid|msclkid)$", re.IGNORECASE)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


class CrawlError(Exception):
    """The start URL itself could not be fetched or is not allowed. Nothing was ingested."""


@dataclass(slots=True)
class FetchedPage:
    url: str
    content_type: str
    body: str


@dataclass(slots=True)
class CrawledPage:
    url: str
    title: str
    text: str


@dataclass(slots=True)
class CrawlResult:
    pages: list[CrawledPage]
    #: Text repeated on most pages (header/footer), kept once. Empty for a 1-2 page crawl.
    shared: str = ""
    warnings: list[str] = field(default_factory=list)
    discovered: int = 0
    failed: int = 0
    stopped_by: str | None = None  # "max_pages" | "time_budget" | None


ProgressFn = Callable[[int, int], Awaitable[None] | None]


def _site_key(host: str) -> str:
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def normalise_url(url: str) -> str | None:
    """Canonical form for de-duplication, or `None` for a URL the crawler never fetches."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    path = parsed.path or "/"
    if _SKIP_EXT.search(path) or _SKIP_PATH.search(path):
        return None
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not _TRACKING_PARAMS.match(k)])
    netloc = parsed.hostname.lower() + (f":{parsed.port}" if parsed.port else "")
    return urlunparse((parsed.scheme, netloc, path, "", query, ""))


async def _blocked(host: str, transport: httpx.AsyncBaseTransport | None) -> bool:
    # A test transport means no real network, so there is nothing to resolve. Same rule as loaders.
    if transport is not None:
        return False
    return await asyncio.to_thread(is_blocked_host, host)


async def fetch(
    client: httpx.AsyncClient, url: str, *, transport: httpx.AsyncBaseTransport | None
) -> FetchedPage:
    """GET `url`, SSRF-checking every redirect hop and capping the body. Raises `CrawlError`."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise CrawlError("Only http(s) URLs are supported.")
        if await _blocked(parsed.hostname, transport):
            raise CrawlError("Refusing to fetch a private/loopback URL.")
        async with client.stream("GET", current, headers=HEADERS, timeout=PAGE_TIMEOUT) as resp:
            location = resp.headers.get("location")
            if resp.is_redirect and location:
                current = urljoin(current, location)
                continue
            if resp.status_code in (401, 403, 429, 503):
                raise CrawlError(
                    f"The site refused the request (HTTP {resp.status_code}). It may block automated "
                    "access; allow 'ViceroBot' in its firewall/CDN bot settings, or paste the text instead."
                )
            if resp.status_code >= 400:
                raise CrawlError(f"The page returned HTTP {resp.status_code}.")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            encoding = resp.charset_encoding or "utf-8"
            try:
                body = raw.decode(encoding, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")
            return FetchedPage(url=current, content_type=resp.headers.get("content-type", ""), body=body)
    raise CrawlError("Too many redirects.")


def is_html(page: FetchedPage) -> bool:
    return "html" in page.content_type.lower() or page.body.lstrip()[:200].lower().startswith(
        ("<!doctype html", "<html")
    )


class _Crawler:
    def __init__(
        self,
        start_url: str,
        *,
        max_pages: int,
        transport: httpx.AsyncBaseTransport | None,
        progress: ProgressFn | None,
        concurrency: int,
        time_budget: float,
    ) -> None:
        start = normalise_url(start_url)
        if start is None:
            raise CrawlError("That URL can't be crawled (only http(s) web pages are supported).")
        self.start = start
        self.site = _site_key(urlparse(start).hostname or "")
        self.max_pages = max_pages
        self.transport = transport
        self.progress = progress
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.deadline = time.monotonic() + time_budget
        self.robots: RobotFileParser | None = None
        self.seen: set[str] = {start}
        self.queue: list[tuple[int, str]] = [(0, start)]  # (depth, url); depth 0 = start/sitemap
        self.pages: list[CrawledPage] = []
        self.warnings: list[str] = []
        self.client_rendered: list[str] = []
        self.failed = 0
        self.stopped_by: str | None = None

    def in_scope(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        if _site_key(host) != self.site:
            return False
        return self.robots is None or self.robots.can_fetch(ROBOTS_AGENT, url)

    def enqueue(self, url: str, depth: int) -> None:
        norm = normalise_url(url)
        if norm is None or norm in self.seen or not self.in_scope(norm):
            return
        self.seen.add(norm)
        self.queue.append((depth, norm))

    async def _report(self) -> None:
        if self.progress is None:
            return
        try:
            result = self.progress(len(self.pages), self.max_pages)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # progress is cosmetic; it must never fail a crawl
            log.debug("crawl_progress_failed", error=str(exc)[:120])

    async def _text(self, client: httpx.AsyncClient, url: str) -> str | None:
        try:
            page = await fetch(client, url, transport=self.transport)
        except (CrawlError, httpx.HTTPError):
            return None
        return page.body

    async def load_robots_and_sitemaps(self, client: httpx.AsyncClient) -> None:
        parsed = urlparse(self.start)
        root = f"{parsed.scheme}://{parsed.netloc}"
        sitemaps: list[str] = []
        robots_txt = await self._text(client, f"{root}/robots.txt")
        if robots_txt and "<html" not in robots_txt[:500].lower():
            robots = RobotFileParser()
            robots.parse(robots_txt.splitlines())
            self.robots = robots
            sitemaps.extend(robots.site_maps() or [])
            if not robots.can_fetch(ROBOTS_AGENT, self.start):
                self.warnings.append(
                    "robots.txt asks crawlers to stay away from this site; only the page you entered was read."
                )
        sitemaps.extend([f"{root}/sitemap.xml", f"{root}/sitemap_index.xml"])

        found: list[str] = []
        visited: set[str] = set()
        nested: list[str] = []
        for sm in sitemaps:
            if sm in visited or len(visited) >= 6:
                continue
            visited.add(sm)
            body = await self._text(client, sm)
            if not body or "<loc>" not in body.lower():
                continue
            locs = _LOC_RE.findall(body)
            if "<sitemapindex" in body.lower():
                nested.extend(locs[:5])
            else:
                found.extend(locs)
            if found:
                break  # one good sitemap is enough; the others are usually aliases
        for sm in nested:
            if sm in visited or len(visited) >= 8:
                continue
            visited.add(sm)
            body = await self._text(client, sm)
            if body:
                found.extend(_LOC_RE.findall(body))
        # Shallow paths first: /pricing before /blog/2019/03/some-post.
        for loc in sorted(set(found), key=lambda u: (urlparse(u).path.count("/"), len(u)))[: self.max_pages * 3]:
            self.enqueue(loc, 0)

    async def crawl_one(self, client: httpx.AsyncClient, depth: int, url: str) -> None:
        async with self.sem:
            if len(self.pages) >= self.max_pages or time.monotonic() > self.deadline:
                return
            try:
                page = await fetch(client, url, transport=self.transport)
            except CrawlError as exc:
                if url == self.start:
                    raise
                self.failed += 1
                log.info("crawl_page_skipped", url=url, reason=str(exc)[:160])
                return
            except httpx.HTTPError as exc:
                if url == self.start:
                    raise CrawlError(f"Couldn't reach the site: {type(exc).__name__}.") from exc
                self.failed += 1
                return
        if not is_html(page):
            if url == self.start and page.body.strip():
                self.pages.append(CrawledPage(url=page.url, title="", text=page.body.strip()))
            return
        extracted = web.extract_page(page.body, page.url)
        for link in extracted.links:
            self.enqueue(link, depth + 1)
        if extracted.noindex and url != self.start:
            return
        text = extracted.as_markdown()
        if web.looks_client_rendered(page.body, extracted.text):
            self.client_rendered.append(page.url)
        if len(text) < _MIN_PAGE_CHARS:
            return
        if len(self.pages) < self.max_pages:
            self.pages.append(CrawledPage(url=page.url, title=extracted.title, text=text))
            await self._report()

    async def run(self) -> CrawlResult:
        async with httpx.AsyncClient(follow_redirects=False, transport=self.transport) as client:
            await self.load_robots_and_sitemaps(client)
            # The start page always goes first and alone: if it fails, the whole crawl fails loudly.
            self.queue.sort(key=lambda item: (item[0], item[1] != self.start))
            depth, first = self.queue.pop(0)
            await self.crawl_one(client, depth, first)
            while self.queue and len(self.pages) < self.max_pages:
                if time.monotonic() > self.deadline:
                    self.stopped_by = "time_budget"
                    break
                self.queue.sort(key=lambda item: item[0])
                batch_size = max(1, min(len(self.queue), (self.max_pages - len(self.pages)) * 2, 16))
                batch, self.queue = self.queue[:batch_size], self.queue[batch_size:]
                await asyncio.gather(*(self.crawl_one(client, d, u) for d, u in batch))
            if self.stopped_by is None and len(self.pages) >= self.max_pages and self.queue:
                self.stopped_by = "max_pages"

        texts, shared = web.dedupe_boilerplate([p.text for p in self.pages])
        pages = [
            CrawledPage(url=p.url, title=p.title, text=t)
            for p, t in zip(self.pages, texts, strict=True)
            if len(t) >= _MIN_PAGE_CHARS or p.url == self.start
        ]
        if self.client_rendered:
            self.warnings.append(
                f"{len(self.client_rendered)} page(s) build their text with JavaScript in the browser, so only "
                "their title, description and structured data could be read. Paste that content as text, or "
                "upload it as a file, for complete answers."
            )
        if self.stopped_by == "max_pages":
            self.warnings.append(f"Stopped at the {self.max_pages}-page limit; more pages were found.")
        elif self.stopped_by == "time_budget":
            self.warnings.append("Stopped at the time limit; the pages read so far were saved.")
        return CrawlResult(
            pages=pages,
            shared=shared,
            warnings=self.warnings,
            discovered=len(self.seen),
            failed=self.failed,
            stopped_by=self.stopped_by,
        )


async def crawl_site(
    start_url: str,
    *,
    max_pages: int,
    transport: httpx.AsyncBaseTransport | None = None,
    progress: ProgressFn | None = None,
    concurrency: int | None = None,
    time_budget: float | None = None,
) -> CrawlResult:
    """Crawl `start_url`'s site. Raises `CrawlError` only when the start page itself fails."""
    limit = max(1, min(int(max_pages), settings.crawl_max_pages_limit))
    crawler = _Crawler(
        start_url,
        max_pages=limit,
        transport=transport,
        progress=progress,
        concurrency=concurrency or settings.crawl_concurrency,
        time_budget=time_budget or settings.crawl_time_budget_seconds,
    )
    result = await crawler.run()
    log.info(
        "site_crawled",
        start=crawler.start,
        pages=len(result.pages),
        discovered=result.discovered,
        failed=result.failed,
        stopped_by=result.stopped_by,
        client_rendered=len(crawler.client_rendered),
    )
    return result
