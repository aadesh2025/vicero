"""Knowledge-base, document, ingestion, and retrieval service."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.core import rbac
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.registry import build_embedding_provider
from app.models import Agent, AgentVersion, Chunk, Document, KnowledgeBase
from app.modules.knowledge import schemas
from app.modules.orgs.deps import OrgContext
from app.rag import formats, retrieval
from app.worker.tasks import enqueue_document_ingestion

log = get_logger("knowledge")


# ── Knowledge bases ─────────────────────────────────────────────────────────────
async def _get_kb(session: AsyncSession, ctx: OrgContext, kb_id: uuid.UUID) -> KnowledgeBase:
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None or kb.organization_id != ctx.org.id or kb.deleted_at is not None:
        raise AppError("kb.not_found", "Knowledge base not found.", 404)
    return kb


async def _doc_count(session: AsyncSession, kb_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Document).where(Document.knowledge_base_id == kb_id)
    return int((await session.execute(stmt)).scalar_one())


async def _attached_agents(
    session: AsyncSession, org_id: uuid.UUID, kb_id: uuid.UUID
) -> list[schemas.AttachedAgent]:
    """Agents whose RAG config references this knowledge base.

    Deleting a KB is quiet by design — `retrieve_for_version` filters `deleted_at`, so an agent
    still pointing at it simply retrieves nothing. That is the dangerous part: with no context
    block the model answers from general knowledge and invents specifics (CLAUDE.md 2026-08-02).
    So the operator is told which agents that would happen to, *before* confirming.

    JSONB containment: `rag_config @> {"knowledge_base_ids": ["<id>"]}` matches an array holding
    that id. Grouped per agent because draft and published versions both count as a reference.
    """
    stmt = (
        select(
            Agent.id,
            Agent.name,
            func.bool_or(AgentVersion.id == Agent.current_version_id).label("is_live"),
        )
        .join(AgentVersion, AgentVersion.agent_id == Agent.id)
        .where(
            Agent.organization_id == org_id,
            Agent.deleted_at.is_(None),
            AgentVersion.rag_config.contains({"knowledge_base_ids": [str(kb_id)]}),
        )
        .group_by(Agent.id, Agent.name)
        .order_by(Agent.name)
    )
    rows = (await session.execute(stmt)).all()
    return [schemas.AttachedAgent(id=r.id, name=r.name, is_live=bool(r.is_live)) for r in rows]


async def _kb_out(
    session: AsyncSession, kb: KnowledgeBase, *, with_usage: bool = False
) -> schemas.KBOut:
    return schemas.KBOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        embedding_provider=kb.embedding_provider,
        embedding_model=kb.embedding_model,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        fts_config=kb.fts_config,
        document_count=await _doc_count(session, kb.id),
        attached_agents=(
            await _attached_agents(session, kb.organization_id, kb.id) if with_usage else []
        ),
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


async def create_kb(session: AsyncSession, ctx: OrgContext, data: schemas.CreateKBRequest) -> schemas.KBOut:
    rbac.require_permission(ctx.role, rbac.KB_MANAGE)
    await usage.require_kb_slot(session, ctx.org)
    kb = KnowledgeBase(
        organization_id=ctx.org.id,
        name=data.name,
        description=data.description,
        embedding_provider=data.embedding_provider,
        embedding_model=data.embedding_model,
        chunk_size=data.chunk_size,
        chunk_overlap=data.chunk_overlap,
        fts_config=data.fts_config,
        created_by=ctx.user.id,
    )
    session.add(kb)
    await session.flush()
    return await _kb_out(session, kb)


async def list_kbs(session: AsyncSession, ctx: OrgContext) -> list[schemas.KBOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = (
        select(KnowledgeBase)
        .where(KnowledgeBase.organization_id == ctx.org.id, KnowledgeBase.deleted_at.is_(None))
        .order_by(KnowledgeBase.created_at.desc())
    )
    kbs = (await session.execute(stmt)).scalars().all()
    return [await _kb_out(session, kb) for kb in kbs]


async def get_kb(session: AsyncSession, ctx: OrgContext, kb_id: uuid.UUID) -> schemas.KBOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return await _kb_out(session, await _get_kb(session, ctx, kb_id), with_usage=True)


async def update_kb(
    session: AsyncSession, ctx: OrgContext, kb_id: uuid.UUID, data: schemas.UpdateKBRequest
) -> schemas.KBOut:
    rbac.require_permission(ctx.role, rbac.KB_MANAGE)
    kb = await _get_kb(session, ctx, kb_id)
    if data.name is not None:
        kb.name = data.name
    if data.description is not None:
        kb.description = data.description
    if data.chunk_size is not None:
        kb.chunk_size = data.chunk_size
    if data.chunk_overlap is not None:
        kb.chunk_overlap = data.chunk_overlap
    if data.fts_config is not None:
        # Takes effect on the next query, not the next ingest: `to_tsvector` runs at read time
        # against `chunks.content`, so nothing needs re-ingesting. What DOES need to exist is a
        # GIN index for the new configuration (migration 0019) — without one the keyword half
        # silently degrades to a sequential scan, which is P0-1 all over again.
        kb.fts_config = data.fts_config
    return await _kb_out(session, kb)


async def delete_kb(session: AsyncSession, ctx: OrgContext, kb_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.KB_MANAGE)
    kb = await _get_kb(session, ctx, kb_id)
    kb.deleted_at = dt.datetime.now(tz=dt.UTC)


# ── Documents ───────────────────────────────────────────────────────────────────
def _doc_out(doc: Document) -> schemas.DocumentOut:
    return schemas.DocumentOut(
        id=doc.id,
        knowledge_base_id=doc.knowledge_base_id,
        source_type=doc.source_type,
        filename=doc.filename,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        source_url=doc.source_url,
        status=doc.status,
        error_message=doc.error_message,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _store_file(org_id: uuid.UUID, document_id: uuid.UUID, filename: str | None, data: bytes) -> str:
    """Persist bytes under the upload dir and return the storage path."""
    suffix = Path(filename or "").suffix or ".txt"
    dest_dir = Path(settings.upload_dir) / str(org_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{document_id}{suffix}"
    dest.write_bytes(data)
    return str(dest)


def _remove_file(storage_path: str) -> None:
    Path(storage_path).unlink(missing_ok=True)


def _remove_document_files(doc: Document) -> None:
    """Every file this document owns on disk (docs/14 K5-3).

    **The retention policy for the persisted `DoclingDocument` is the document's own lifetime.**
    Its only purpose is to make re-chunking free (K2-4), which is meaningless once the document
    is gone — and it is not a derived artefact in any harmless sense: it holds the document's
    full text, element by element, including whatever `pii_flags` was raised on. Leaving it
    behind means a client who deleted a document still has its contents on our disk, which is
    the answer to a data-subject request being wrong.

    No separate expiry job, deliberately. A time-based policy would delete the structured form
    of documents that are still live, silently turning a free re-chunk into a full
    re-conversion, and would need its own scheduling to boot.
    """
    if doc.storage_path:
        _remove_file(doc.storage_path)
    if doc.docling_json_path:
        _remove_file(doc.docling_json_path)


async def _get_document(session: AsyncSession, ctx: OrgContext, document_id: uuid.UUID) -> Document:
    doc = await session.get(Document, document_id)
    if doc is None or doc.organization_id != ctx.org.id:
        raise AppError("kb.document_not_found", "Document not found.", 404)
    return doc


async def create_document(
    session: AsyncSession, ctx: OrgContext, kb_id: uuid.UUID, data: schemas.CreateDocumentRequest
) -> schemas.DocumentOut:
    rbac.require_permission(ctx.role, rbac.KB_MANAGE)
    kb = await _get_kb(session, ctx, kb_id)

    doc = Document(
        knowledge_base_id=kb.id,
        organization_id=ctx.org.id,
        source_type=data.source_type,
        status="queued",
        created_by=ctx.user.id,
    )
    if data.source_type == "text":
        if not data.text or not data.text.strip():
            raise AppError("kb.text_required", "text is required for a text document.", 400)
        raw = data.text.encode("utf-8")
        # Before writing anything (docs/22 §11): the document-count cap, then storage against
        # this text's own byte size.
        await usage.require_document_slot(session, ctx.org, len(raw))
        doc.filename = data.filename or "text-snippet.txt"
        doc.mime_type = "text/plain"
        doc.size_bytes = len(raw)
        session.add(doc)
        await session.flush()
        doc.storage_path = _store_file(ctx.org.id, doc.id, doc.filename, raw)
        await usage.record_document_stored(session, ctx.org.id, len(raw))
    else:  # url
        if not data.url:
            raise AppError("kb.url_required", "url is required for a url document.", 400)
        # The fetched size isn't known until the worker ingests it, so only the document-count
        # cap applies here — `org_storage_usage` is updated once a real size exists, same as
        # every other size-unknown-at-creation path in this codebase.
        await usage.require_document_slot(session, ctx.org, 0)
        doc.source_url = data.url
        doc.filename = data.filename or data.url
        session.add(doc)
        await session.flush()

    enqueue_document_ingestion(doc.id)
    return _doc_out(doc)


async def upload_document(
    session: AsyncSession,
    ctx: OrgContext,
    kb_id: uuid.UUID,
    *,
    filename: str,
    mime_type: str | None,
    data: bytes,
) -> schemas.DocumentOut:
    rbac.require_permission(ctx.role, rbac.KB_MANAGE)
    kb = await _get_kb(session, ctx, kb_id)
    if not data:
        raise AppError("kb.empty_file", "Uploaded file is empty.", 400)
    # Deny-by-default (docs/14 K3-1/K3-2). Before this, anything at all was accepted and
    # `load_bytes` decoded whatever it did not recognise as UTF-8 text — which meant `.eml`
    # threads, the highest-PII-density format there is, already ingested whole.
    decision = formats.classify(filename, mime_type)
    if not decision.allowed:
        log.info(
            "document_upload_refused",
            organization_id=str(ctx.org.id),
            outcome=decision.outcome,
            kind=decision.kind or None,
            # The name only — never the bytes, and never anything read out of them.
            filename=filename[:120],
        )
        code = "kb.format_gated" if decision.outcome == "gated" else "kb.format_unsupported"
        raise AppError(code, decision.reason, 400)
    # Before writing anything (docs/22 §11): the document-count cap, then storage against this
    # upload's own byte size.
    await usage.require_document_slot(session, ctx.org, len(data))
    doc = Document(
        knowledge_base_id=kb.id,
        organization_id=ctx.org.id,
        source_type="file",
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        status="queued",
        created_by=ctx.user.id,
    )
    session.add(doc)
    await session.flush()
    doc.storage_path = _store_file(ctx.org.id, doc.id, filename, data)
    await usage.record_document_stored(session, ctx.org.id, len(data))
    enqueue_document_ingestion(doc.id)
    return _doc_out(doc)


async def list_documents(session: AsyncSession, ctx: OrgContext, kb_id: uuid.UUID) -> list[schemas.DocumentOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_kb(session, ctx, kb_id)
    stmt = (
        select(Document)
        .where(Document.knowledge_base_id == kb_id, Document.organization_id == ctx.org.id)
        .order_by(Document.created_at.desc())
    )
    return [_doc_out(d) for d in (await session.execute(stmt)).scalars().all()]


async def get_document(session: AsyncSession, ctx: OrgContext, document_id: uuid.UUID) -> schemas.DocumentOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return _doc_out(await _get_document(session, ctx, document_id))


async def delete_document(session: AsyncSession, ctx: OrgContext, document_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.KB_MANAGE)
    doc = await _get_document(session, ctx, document_id)
    _remove_document_files(doc)
    await usage.record_document_removed(session, ctx.org.id, doc.size_bytes or 0)
    await session.delete(doc)  # chunks cascade via FK


async def reingest_document(session: AsyncSession, ctx: OrgContext, document_id: uuid.UUID) -> schemas.DocumentOut:
    rbac.require_permission(ctx.role, rbac.KB_MANAGE)
    doc = await _get_document(session, ctx, document_id)
    doc.status = "queued"
    doc.error_message = None
    await session.flush()
    enqueue_document_ingestion(doc.id)
    return _doc_out(doc)


async def list_chunks(session: AsyncSession, ctx: OrgContext, document_id: uuid.UUID) -> list[schemas.ChunkOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    doc = await _get_document(session, ctx, document_id)
    stmt = select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal.asc())
    return [
        schemas.ChunkOut(
            id=c.id, ordinal=c.ordinal, content=c.content, token_count=c.token_count, metadata=c.meta or {}
        )
        for c in (await session.execute(stmt)).scalars().all()
    ]


# ── Retrieval ───────────────────────────────────────────────────────────────────
async def search_kb(
    session: AsyncSession, ctx: OrgContext, kb_id: uuid.UUID, data: schemas.SearchRequest
) -> schemas.SearchResponse:
    rbac.require_permission(ctx.role, rbac.READ)
    kb = await _get_kb(session, ctx, kb_id)
    embedder = build_embedding_provider(kb.embedding_provider, kb.embedding_model)
    citations = await retrieval.search(
        session,
        ctx.org.id,
        [kb.id],
        data.query,
        embedder,
        top_k=data.top_k,
        score_threshold=data.score_threshold,
        hybrid=data.hybrid,
        fts_config=kb.fts_config,
    )
    return schemas.SearchResponse(query=data.query, citations=citations)
