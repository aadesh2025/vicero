"""Ingestion pipeline: load → chunk → embed → store (docs/06 §2).

This is a plain async function so it is directly testable against the transaction-rolled-back
test session. The Celery task in `app.worker.tasks` wraps it with its own committing session.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.pii import scan_document_text
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.registry import build_embedding_provider
from app.models import Chunk, Document, KnowledgeBase
from app.rag import converters, crawler, loaders, progress
from app.rag.chunking import TextChunk, chunk_text
from app.rag.docling_chunking import DoclingChunkingUnavailable, chunk_docling_document
from app.webhooks.dispatch import emit_event

log = get_logger("rag.ingest")

_EMBED_BATCH = 32


def _stored_bytes(storage_path: str) -> bytes:
    """Read the uploaded file. Sync on purpose — see below.

    Kept out of the async path so the blocking read is honest rather than hidden inside a
    coroutine (ruff ASYNC240). It runs in the Celery ingest worker, where blocking on a local
    file for a few milliseconds is the expected shape of the job, not a latency problem.
    """
    path = Path(storage_path)
    if not path.exists():
        raise loaders.LoaderError(f"Stored file is missing: {path}")
    return path.read_bytes()


def _enforce_page_cap(data: bytes, document: Document) -> None:
    """Refuse a pathologically long PDF up front (docs/14 K5-2, §8).

    Enforced here rather than at upload because every route converges on it — file upload, a
    re-ingest, and a PDF fetched from a URL — and one check that covers all three cannot drift
    from the other two.

    The message names the number. "This document is too large" tells the client nothing they can
    act on; "620 pages, the limit is 800" tells them to split it.
    """
    cap = settings.max_pdf_pages
    if cap <= 0:
        return
    name = (document.filename or "").lower()
    if not (name.endswith(".pdf") or (document.mime_type or "").lower().endswith("pdf")):
        return
    pages = loaders.pdf_page_count(data)
    if pages is not None and pages > cap:
        raise loaders.LoaderError(
            f"This PDF has {pages} pages and the limit is {cap}. "
            f"Split it into smaller documents and upload them separately."
        )


async def _read_source(
    document: Document,
    *,
    url_transport: httpx.AsyncBaseTransport | None,
    docling_transport: httpx.AsyncBaseTransport | None = None,
) -> converters.ConvertedDocument:
    if document.source_type == "website":
        return await _crawl_website(document, url_transport=url_transport)
    if document.source_type == "url":
        if not document.source_url:
            raise loaders.LoaderError("Document has no source URL.")
        # URL ingest deliberately stays on trafilatura and does NOT go through Docling. The
        # SSRF controls in `loaders.load_url` (scheme check, private/loopback rejection) are
        # what stand between a visitor-suppliable URL and the internal network, and handing the
        # URL to docling-serve to fetch would route around them entirely — docs/14 §9's rule
        # that a new code path must not bypass an existing control. trafilatura also already
        # produces structured markdown for HTML, which is what Docling would add.
        text = await loaders.load_url(document.source_url, transport=url_transport)
        return converters.ConvertedDocument(text=text, backend=converters.BACKEND_LEGACY)
    if not document.storage_path:
        raise loaders.LoaderError("Document has no stored file.")
    data = _stored_bytes(document.storage_path)
    _enforce_page_cap(data, document)
    return await converters.convert_with_fallback(
        data,
        filename=document.filename,
        mime_type=document.mime_type,
        transport=docling_transport,
    )


async def _crawl_website(
    document: Document, *, url_transport: httpx.AsyncBaseTransport | None
) -> converters.ConvertedDocument:
    """Crawl the site and return one section per page (app/rag/crawler.py, ADR-139)."""
    if not document.source_url:
        raise loaders.LoaderError("Document has no source URL.")
    info = dict(document.crawl_info or {})
    max_pages = int(info.get("max_pages") or settings.crawl_default_max_pages)

    async def on_page(done: int, total: int) -> None:
        await progress.report(document.id, "crawling", done, total)

    await progress.report(document.id, "crawling", 0, max_pages)
    try:
        result = await crawler.crawl_site(
            document.source_url, max_pages=max_pages, transport=url_transport, progress=on_page
        )
    except crawler.CrawlError as exc:
        raise loaders.LoaderError(str(exc)) from exc
    sections = [(p.url, p.title, p.text) for p in result.pages]
    if result.shared:
        # Header/footer text repeated on every page (address, phone, hours), kept exactly once.
        sections.append((document.source_url, "Site-wide information", result.shared))
    info.update(
        max_pages=max_pages,
        pages=[{"url": p.url, "title": p.title[:200], "chars": len(p.text)} for p in result.pages],
        warnings=result.warnings,
        stopped_by=result.stopped_by,
        discovered=result.discovered,
        failed=result.failed,
    )
    document.crawl_info = info
    text = "\n\n".join(f"# {title or url}\nSource: {url}\n\n{body}" for url, title, body in sections)
    return converters.ConvertedDocument(
        text=text, backend=converters.BACKEND_LEGACY, sections=sections, info={"pages": len(result.pages)}
    )


async def _embed_all(
    document: Document, embedder: Any, inputs: list[str]
) -> list[list[float]]:
    """Embed in batches, a few at a time, reporting progress after each batch.

    Embedding is ~99% of ingest time (measured 2026-10-21: a 300 KB document, 511 chunks, 48 s on
    a laptop GPU; reading and chunking took 0.5 s together). Concurrency buys a little when the
    embedding server runs requests in parallel (Ollama with OLLAMA_NUM_PARALLEL > 1, or a hosted
    API) and costs nothing when it does not; the progress report is what turns a silent minute
    into a visible "Embedding 120 / 511".
    """
    batches = [inputs[i : i + _EMBED_BATCH] for i in range(0, len(inputs), _EMBED_BATCH)]
    results: list[list[list[float]] | None] = [None] * len(batches)
    sem = asyncio.Semaphore(max(1, settings.embed_concurrency))
    done = 0

    async def run(index: int, batch: list[str]) -> None:
        nonlocal done
        async with sem:
            results[index] = await embedder.embed(batch)
        done += len(batch)
        await progress.report(document.id, "embedding", done, len(inputs))

    await progress.report(document.id, "embedding", 0, len(inputs))
    tasks = [asyncio.ensure_future(run(i, b)) for i, b in enumerate(batches)]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # One failed batch fails the document; don't leave the others embedding into the void.
        for task in tasks:
            task.cancel()
        raise
    vectors: list[list[float]] = []
    for part in results:
        vectors.extend(part or [])
    return vectors


def _persist_docling_json(document: Document, payload: dict[str, Any]) -> str | None:
    """Write the `DoclingDocument` beside the source file. Never fails the ingest.

    Losing the structured form costs a future re-chunk a re-conversion (docs/14 §8: "fall back
    to full re-conversion; log it"). Losing the *document* because a disk was full would be a
    far worse trade, so this is best-effort by design.
    """
    if not document.storage_path:
        return None
    path = Path(document.storage_path).with_suffix(".docling.json")
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        log.warning(
            "docling_json_persist_failed",
            document_id=str(document.id),
            error=str(exc)[:200],
            impact="re-chunking this document will need a full re-conversion",
        )
        return None
    return str(path)


def _chunk(
    text: str,
    kb: KnowledgeBase,
    *,
    structured: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> list[TextChunk]:
    """Structural chunking when there is a `DoclingDocument`, character splitting otherwise.

    The fallback is not a formality. `docling-core` is an optional dependency of the API image,
    and `HybridChunker`'s tokenizer downloads its BPE ranks on first use — neither is worth
    failing a client's upload over when a working splitter is right there.
    """
    if structured:
        try:
            chunks = chunk_docling_document(
                structured,
                max_tokens=settings.docling_chunk_max_tokens,
                metadata=metadata,
                heading_mode=settings.docling_chunk_heading_mode,
            )
            if chunks:
                return chunks
            # An empty structural chunking of a document that *did* produce text means the
            # DoclingDocument is not describing the same content. Fall through rather than
            # store nothing.
            log.warning("docling_chunking_empty", filename=metadata.get("filename"))
        except DoclingChunkingUnavailable as exc:
            log.warning(
                "docling_chunking_unavailable",
                error=str(exc)[:200],
                impact="chunked by character split; heading context is not embedded",
            )
        except Exception as exc:  # a malformed persisted document must not fail the ingest
            log.warning("docling_chunking_failed", error=str(exc)[:200])
    return chunk_text(text, kb.chunk_size, kb.chunk_overlap, metadata=metadata)


def _chunk_sections(
    sections: list[tuple[str, str, str]], kb: KnowledgeBase, document: Document
) -> list[TextChunk]:
    """One page at a time, so no chunk spans two pages and each cites its own URL."""
    chunks: list[TextChunk] = []
    for url, title, body in sections:
        page_text = f"# {title}\n\n{body}" if title and not body.lstrip().startswith("#") else body
        for chunk in chunk_text(
            page_text,
            kb.chunk_size,
            kb.chunk_overlap,
            metadata={
                "filename": document.filename,
                "source_url": url,
                "page_title": title,
                "backend": converters.BACKEND_LEGACY,
            },
        ):
            chunk.ordinal = len(chunks)
            chunks.append(chunk)
    return chunks


def _discard_docling_json(document: Document) -> None:
    """Delete a previously persisted `DoclingDocument`. Best-effort, never fails an ingest."""
    if not document.docling_json_path:
        return
    try:
        Path(document.docling_json_path).unlink(missing_ok=True)
    except OSError as exc:
        log.warning(
            "docling_json_discard_failed",
            document_id=str(document.id),
            error=str(exc)[:200],
            impact="an orphaned extraction file remains on disk",
        )


async def ingest_document(
    session: AsyncSession,
    document_id: uuid.UUID,
    *,
    url_transport: httpx.AsyncBaseTransport | None = None,
    docling_transport: httpx.AsyncBaseTransport | None = None,
) -> Document:
    """Parse, chunk, embed and store a document. Sets status ready|failed; never raises."""
    document = await session.get(Document, document_id)
    if document is None:
        raise loaders.LoaderError(f"Document {document_id} not found.")
    kb = await session.get(KnowledgeBase, document.knowledge_base_id)
    if kb is None:
        raise loaders.LoaderError("Knowledge base not found.")

    document.status = "processing"
    document.error_message = None
    await session.flush()
    await progress.report(document.id, "fetching" if document.source_url else "reading")

    try:
        converted = await _read_source(
            document, url_transport=url_transport, docling_transport=docling_transport
        )
        text = converted.text
        if not text.strip():
            raise loaders.LoaderError("No extractable text in document.")
        document.extraction_backend = converted.backend
        # Persisted before chunking, so a later re-chunk (K2-4) can skip conversion entirely.
        if converted.document:
            document.docling_json_path = _persist_docling_json(document, converted.document)
        else:
            # Re-ingested without structure this time — Docling turned off, or an outage that
            # fell back to the legacy extractor. Clearing the column is not enough: the file
            # describes an extraction the chunks are no longer built from, and left on disk it
            # is an orphan holding the document's full text that nothing will ever delete,
            # because `delete_document` deletes by the path we just cleared (docs/14 K5-3).
            _discard_docling_json(document)
            document.docling_json_path = None

        # Scan before chunking, so a contact detail split across a chunk boundary is still
        # counted once against the whole document (docs/11 Phase B, §6).
        document.pii_flags = scan_document_text(text)
        if document.pii_flags:
            # Counts only — never the values. A PII report that echoes the PII is the same
            # leak in a different place.
            log.warning(
                "document_pii_detected",
                document_id=str(document.id),
                organization_id=str(document.organization_id),
                flags=document.pii_flags,
            )

        await progress.report(document.id, "chunking")
        if converted.sections:
            chunks = _chunk_sections(converted.sections, kb, document)
        else:
            chunks = _chunk(
                text,
                kb,
                structured=converted.document,
                metadata={
                    "filename": document.filename,
                    "source_url": document.source_url,
                    # Provenance on the chunk itself, so a retrieved result can be traced to the
                    # extractor that produced it without a join back to `documents`.
                    "backend": converted.backend,
                },
            )
        if not chunks:
            raise loaders.LoaderError("Document produced no chunks.")

        embedder = build_embedding_provider(kb.embedding_provider, kb.embedding_model)
        # `embedding_input`, not `content` — on the Docling path these differ by the chunk's
        # heading path (docs/14 §4.2). The enriched string improves the vector and is never
        # persisted, so a citation still shows the visitor exactly what the document said.
        vectors = await _embed_all(document, embedder, [c.embedding_input for c in chunks])
        await progress.report(document.id, "saving", len(chunks), len(chunks))

        # Reingest: clear any prior chunks for this document.
        await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        for chunk, vector in zip(chunks, vectors, strict=True):
            session.add(
                Chunk(
                    document_id=document.id,
                    knowledge_base_id=document.knowledge_base_id,
                    organization_id=document.organization_id,
                    ordinal=chunk.ordinal,
                    content=chunk.content,
                    # Indexed for FTS alongside `content` (fts.SEARCHABLE_SQL) so the keyword
                    # half sees the same heading context the embedding does. NULL for chunks
                    # from the character splitter, which has no notion of a heading.
                    heading=chunk.heading_path,
                    token_count=chunk.token_count,
                    meta=chunk.metadata,
                    embedding=vector,
                )
            )
        document.status = "ready"
        document.chunk_count = len(chunks)
        document.error_message = None
        await session.flush()
        await emit_event(
            session, document.organization_id, "document.ready",
            {"document_id": str(document.id), "chunks": len(chunks)},
        )
        log.info("document_ingested", document_id=str(document.id), chunks=len(chunks))
    except Exception as exc:  # record failure, don't crash the worker
        document.status = "failed"
        document.error_message = str(exc)[:1000]
        document.chunk_count = 0
        await session.flush()
        await emit_event(
            session, document.organization_id, "document.failed",
            {"document_id": str(document.id), "error": str(exc)[:200]},
        )
        log.warning("document_ingest_failed", document_id=str(document.id), error=str(exc))
    await progress.clear(document.id)
    return document
