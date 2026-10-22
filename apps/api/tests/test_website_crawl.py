# ruff: noqa: E501  (inline HTML fixtures read better unwrapped)
"""Whole-website import (ADR-139): discovery, scope, SSRF, extraction recall, and the ingest path.

Before: a URL import read one page through a main-article extractor, and a client's business
homepage produced 7 chunks with no prices and no phone number. These tests pin the new behaviour
against a small fake site served by `httpx.MockTransport`. No real network is touched.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Chunk
from app.rag import crawler, web
from app.rag.ingest import ingest_document
from app.rag.loaders import extract_main_content

_NAV = """<nav><a href="/">Home</a><a href="/pricing">Pricing</a><a href="/contact">Contact</a>
<a href="/about/">About</a><a href="https://twitter.com/acme">Twitter</a></nav>"""
_FOOTER = "<footer><p>Acme Studio, 12 Anna Salai, Chennai 600002</p><p>Call +91 93455 00000</p></footer>"


def _page(title: str, body: str, extra_head: str = "") -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title>"
        f'<meta name="description" content="{title} at Acme Studio">{extra_head}</head>'
        f"<body><header><a href='/'>Acme</a></header>{_NAV}<main>{body}</main>{_FOOTER}</body></html>"
    )


SITE: dict[str, tuple[int, str, str]] = {
    "/": (
        200,
        "text/html",
        _page(
            "Acme Studio",
            "<h1>We build websites</h1><p>Fast, accessible sites for small businesses in Chennai.</p><a href='/blog/post-1'>Read</a><a href='/wp-admin/'>admin</a><a href='/brochure.pdf'>pdf</a>",
        ),
    ),
    "/pricing": (
        200,
        "text/html",
        _page(
            "Pricing",
            "<h1>Pricing</h1><div class='card'><h3>Starter</h3><p>₹9,999 one-time</p></div><div class='card'><h3>Growth</h3><p>₹24,999 one-time</p></div>",
        ),
    ),
    "/contact": (
        200,
        "text/html",
        _page("Contact", "<h1>Contact us</h1><p>Email hello@acme.example. Open Monday to Saturday, 10am to 7pm.</p>"),
    ),
    "/about": (
        200,
        "text/html",
        _page("About", "<h1>About</h1><p>Founded in 2019 by two designers who wanted honest pricing.</p>"),
    ),
    "/blog/post-1": (
        200,
        "text/html",
        _page("Post", "<h1>How we work</h1><p>Every project starts with a free thirty minute call.</p>"),
    ),
    "/secret": (200, "text/html", _page("Secret", "<p>Disallowed by robots and must never be read.</p>")),
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /secret\nSitemap: https://acme.example/sitemap.xml\n"),
    "/sitemap.xml": (
        200,
        "application/xml",
        "<urlset><url><loc>https://acme.example/pricing</loc></url><url><loc>https://acme.example/secret</loc></url><url><loc>https://other.example/x</loc></url></urlset>",
    ),
}


def _transport(site: dict[str, tuple[int, str, str]], seen: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.host not in ("acme.example", "www.acme.example"):
            return httpx.Response(500, text="off-site request")
        status, ctype, body = site.get(request.url.path, (404, "text/html", "<h1>Not found</h1>"))
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    return httpx.MockTransport(handler)


# ── Extraction ──────────────────────────────────────────────────────────────────────────────
def test_full_recall_keeps_prices_and_footer_but_not_the_menu() -> None:
    page = web.extract_page(SITE["/pricing"][2], "https://acme.example/pricing")
    text = page.as_markdown()
    assert "₹9,999 one-time" in text and "₹24,999 one-time" in text
    assert "+91 93455 00000" in text
    assert "Twitter" not in text  # the <nav> menu is dropped
    assert "https://acme.example/contact" in page.links  # ...but its links are still collected


def test_single_page_extraction_adds_back_what_trafilatura_dropped() -> None:
    text = extract_main_content(SITE["/pricing"][2], "https://acme.example/pricing")
    assert "₹9,999" in text and "₹24,999" in text
    assert "+91 93455 00000" in text


def test_svg_titles_do_not_become_the_page_title() -> None:
    html = (
        "<html><head><title>Real</title></head><body><svg><title>x-twitter</title></svg><p>Hi there</p></body></html>"
    )
    assert web.extract_page(html).title == "Real"


def test_json_ld_facts_survive_a_javascript_only_page() -> None:
    shell = (
        '<html><head><title>Glow Spa</title><script type="application/ld+json">'
        '{"@type":"LocalBusiness","name":"Glow Spa","telephone":"+91 44 1234 5678",'
        '"address":{"streetAddress":"5 Beach Road","addressLocality":"Chennai"},"openingHours":"Mo-Sa 10:00-20:00"}'
        '</script></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
    )
    page = web.extract_page(shell)
    assert "telephone: +91 44 1234 5678" in page.structured
    assert "streetAddress: 5 Beach Road" in page.structured
    assert web.looks_client_rendered(shell, page.text)


def test_boilerplate_is_kept_once_not_on_every_page() -> None:
    pages = [f"Unique {i}\nAcme Studio, Chennai\nCall +91 93455 00000" for i in range(4)]
    cleaned, shared = web.dedupe_boilerplate(pages)
    assert all("Call +91" not in p for p in cleaned)
    assert "Call +91 93455 00000" in shared


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://Acme.example/About/?utm_source=x#team", "https://acme.example/About"),
        ("https://acme.example/logo.png", None),
        ("https://acme.example/cart", None),
        ("mailto:hello@acme.example", None),
    ],
)
def test_url_normalisation(url: str, expected: str | None) -> None:
    assert crawler.normalise_url(url) == expected


# ── Crawl ───────────────────────────────────────────────────────────────────────────────────
async def test_crawl_reads_the_site_and_stays_in_scope() -> None:
    seen: list[str] = []
    result = await crawler.crawl_site("https://acme.example/", max_pages=10, transport=_transport(SITE, seen))
    urls = {p.url for p in result.pages}
    assert {"https://acme.example/", "https://acme.example/pricing", "https://acme.example/contact"} <= urls
    assert "https://acme.example/about" in urls and "https://acme.example/blog/post-1" in urls
    assert not any("secret" in u for u in seen), "robots.txt Disallow was ignored"
    assert not any("other.example" in u or "twitter.com" in u for u in seen), "left the site"
    assert not any(u.endswith((".pdf", "/wp-admin")) for u in seen)
    # Footer text is repeated on every page: removed from each, kept once.
    assert "+91 93455 00000" in result.shared
    assert all("+91 93455 00000" not in p.text for p in result.pages)
    pricing = next(p for p in result.pages if p.url.endswith("/pricing"))
    assert "₹24,999" in pricing.text


async def test_max_pages_is_respected_and_reported() -> None:
    result = await crawler.crawl_site("https://acme.example/", max_pages=2, transport=_transport(SITE))
    assert len(result.pages) == 2
    assert result.stopped_by == "max_pages"
    assert any("2-page limit" in w for w in result.warnings)


async def test_progress_is_reported_per_page() -> None:
    calls: list[tuple[int, int]] = []
    await crawler.crawl_site(
        "https://acme.example/", max_pages=3, transport=_transport(SITE), progress=lambda d, t: calls.append((d, t))
    )
    assert calls[0] == (1, 3) and calls[-1][0] == 3


async def test_a_redirect_to_a_private_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def blocked(host: str, transport: object) -> bool:
        return host == "internal.example"

    monkeypatch.setattr(crawler, "_blocked", blocked)
    site = dict(SITE)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/contact":
            return httpx.Response(302, headers={"location": "http://internal.example/admin"})
        status, ctype, body = site.get(request.url.path, (404, "text/html", "x"))
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    result = await crawler.crawl_site("https://acme.example/", max_pages=10, transport=httpx.MockTransport(handler))
    assert not any("internal.example" in u for u in seen)
    assert result.failed >= 1


async def test_a_start_page_that_refuses_bots_fails_with_an_actionable_message() -> None:
    site = {"/": (403, "text/html", "Forbidden")}
    with pytest.raises(crawler.CrawlError, match="ViceroBot"):
        await crawler.crawl_site("https://acme.example/", max_pages=5, transport=_transport(site))


async def test_requests_use_a_browser_shaped_user_agent() -> None:
    agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["user-agent"])
        status, ctype, body = SITE.get(request.url.path, (404, "text/html", "x"))
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    await crawler.crawl_site("https://acme.example/", max_pages=1, transport=httpx.MockTransport(handler))
    assert agents and all("Mozilla/5.0" in a and "ViceroBot" in a for a in agents)


# ── Ingest + API ────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.modules.knowledge import service

    monkeypatch.setattr(service, "enqueue_document_ingestion", lambda *_a, **_k: None)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))


async def _setup(client: AsyncClient) -> tuple[dict[str, str], str]:
    signup = await client.post(
        "/v1/auth/signup", json={"email": f"{uuid.uuid4().hex[:8]}@ex.com", "password": "password123"}
    )
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "Crawl"}, headers={"Authorization": f"Bearer {token}"})
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    kb = await client.post(
        "/v1/knowledge",
        json={"name": "Site", "embedding_provider": "fake", "embedding_model": "fake-embed"},
        headers=headers,
    )
    return headers, kb.json()["id"]


async def test_website_document_crawls_and_cites_each_page(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, kb_id = await _setup(client)
    r = await client.post(
        f"/v1/knowledge/{kb_id}/documents",
        json={"source_type": "website", "url": "https://www.acme.example/", "max_pages": 10},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["source_type"] == "website"
    assert created["filename"] == "acme.example (website)"
    assert created["crawl"]["max_pages"] == 10

    doc = await ingest_document(db_session, uuid.UUID(created["id"]), url_transport=_transport(SITE))
    assert doc.status == "ready", doc.error_message
    assert doc.chunk_count >= 5
    assert len(doc.crawl_info["pages"]) >= 5

    chunks = (await db_session.execute(Chunk.__table__.select().where(Chunk.document_id == doc.id))).all()
    sources = {c.metadata["source_url"] for c in chunks}
    assert "https://acme.example/pricing" in sources and "https://acme.example/contact" in sources
    assert any("₹24,999" in c.content for c in chunks)
    assert sum("+91 93455 00000" in c.content for c in chunks) == 1, "footer should be stored exactly once"

    listed = await client.get(f"/v1/knowledge/{kb_id}/documents", headers=headers)
    out = next(d for d in listed.json() if d["id"] == created["id"])
    assert len(out["crawl"]["pages"]) >= 5


async def test_max_pages_is_capped_server_side(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "crawl_max_pages_limit", 50)
    headers, kb_id = await _setup(client)
    r = await client.post(
        f"/v1/knowledge/{kb_id}/documents",
        json={"source_type": "website", "url": "https://acme.example/", "max_pages": 400},
        headers=headers,
    )
    assert r.json()["crawl"]["max_pages"] == 50


async def test_a_failed_crawl_marks_the_document_failed_with_the_reason(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, kb_id = await _setup(client)
    r = await client.post(
        f"/v1/knowledge/{kb_id}/documents",
        json={"source_type": "website", "url": "https://acme.example/"},
        headers=headers,
    )
    doc = await ingest_document(
        db_session, uuid.UUID(r.json()["id"]), url_transport=_transport({"/": (403, "text/html", "no")})
    )
    assert doc.status == "failed"
    assert "ViceroBot" in (doc.error_message or "")


async def test_live_progress_shows_a_queued_row_as_processing(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.knowledge import service

    headers, kb_id = await _setup(client)
    r = await client.post(
        f"/v1/knowledge/{kb_id}/documents", json={"source_type": "text", "text": "hello world"}, headers=headers
    )
    doc_id = r.json()["id"]

    async def fake_read(ids: object) -> dict[str, dict[str, int | str]]:
        return {doc_id: {"stage": "embedding", "done": 64, "total": 511}}

    monkeypatch.setattr(service.ingest_progress, "read_many", fake_read)
    listed = await client.get(f"/v1/knowledge/{kb_id}/documents", headers=headers)
    out = next(d for d in listed.json() if d["id"] == doc_id)
    assert out["status"] == "processing"
    assert out["progress"] == {"stage": "embedding", "done": 64, "total": 511}


async def test_ingest_reports_embedding_progress(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.rag import ingest as ingest_mod

    reports: list[tuple[str, int, int]] = []

    async def fake_report(_doc: object, stage: str, done: int = 0, total: int = 0) -> None:
        reports.append((stage, done, total))

    async def fake_clear(_doc: object) -> None:
        reports.append(("cleared", 0, 0))

    monkeypatch.setattr(ingest_mod.progress, "report", fake_report)
    monkeypatch.setattr(ingest_mod.progress, "clear", fake_clear)
    headers, kb_id = await _setup(client)
    text = "\n\n".join(f"Paragraph {i} " + "word " * 150 for i in range(40))
    r = await client.post(
        f"/v1/knowledge/{kb_id}/documents", json={"source_type": "text", "text": text}, headers=headers
    )
    doc = await ingest_document(db_session, uuid.UUID(r.json()["id"]))
    assert doc.status == "ready"
    stages = [s for s, _, _ in reports]
    assert stages[0] == "reading" and "chunking" in stages and "saving" in stages and stages[-1] == "cleared"
    embedding = [(d, t) for s, d, t in reports if s == "embedding"]
    assert embedding[0][0] == 0 and embedding[-1] == (doc.chunk_count, doc.chunk_count)
