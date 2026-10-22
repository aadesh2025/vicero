"""Knowledge base, documents, and vector chunks."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey

# Dimension of the embedding vectors. Matches nomic-embed-text (768); make configurable
# per knowledge base later if a different embedding model is chosen.
EMBEDDING_DIM = 768


class KnowledgeBase(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "knowledge_bases"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    embedding_provider: Mapped[str] = mapped_column(String(32), default="ollama", nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), default="nomic-embed-text", nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=150, nullable=False)
    #: Postgres text-search configuration for the keyword half of hybrid retrieval. Per-KB
    #: rather than per-org: a client can legitimately keep an English product manual and a
    #: Tamil FAQ side by side, and one setting for both makes one of them worse.
    #:
    #: A stemmer is language-specific and applying the wrong one is not a small error —
    #: `to_tsvector('english', <Tamil>)` produces tokens that stem nothing and match nothing,
    #: which is why docs/11 §9.2a's "L1 is English-first" gap has a retrieval-side twin. Only
    #: names in `rag.fts.SUPPORTED_CONFIGS` are accepted; `simple` (tokenise, never stem) is
    #: the honest fallback for a language Postgres has no dictionary for.
    fts_config: Mapped[str] = mapped_column(String(32), default="english", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Document(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "documents"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)  # file|url|text|website
    filename: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(1024))
    #: `{kind: count}` from the ingest-time PII scan (docs/11 Phase B) — e.g.
    #: `{"email": 2, "phone": 1}`. Counts only, never the values: a PII report that echoes the
    #: PII is the same leak in a different place. `None` means the document predates the scan;
    #: `{}` means it was scanned and is clean, and the UI distinguishes the two.
    pii_flags: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    #: Which extractor produced this document's chunks — `legacy` (pypdf/python-docx/csv) or
    #: `docling`. Recorded so a re-ingest campaign is targetable and a quality regression is
    #: attributable to a backend. A mixed corpus is the *expected* state during the docs/14 §12
    #: rollout, not a transient one.
    extraction_backend: Mapped[str] = mapped_column(String(16), default="legacy", nullable=False)
    #: Path to the persisted `DoclingDocument` JSON, or `None` when there is no structured form
    #: (legacy extraction, or Docling before persistence existed). A **path**, not the JSON:
    #: these blobs carry per-element geometry and table cells, and a `jsonb` column would bloat
    #: the table every tenant query touches (docs/14 §6).
    #:
    #: What it buys: re-chunking never re-runs the ML pipeline. Chunk size, the tokenizer and
    #: the chunker itself become parameters the eval harness can optimise, rather than a
    #: one-shot commitment paid for by re-converting every document in every org.
    docling_json_path: Mapped[str | None] = mapped_column(String(1024))
    #: Whole-website imports only (`source_type="website"`, ADR-139): `{"max_pages": 25}` at creation;
    #: after ingest also `pages` ([{url, title, chars}]), `warnings` and `stopped_by`. NULL otherwise.
    crawl_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Chunk(Base, UUIDPrimaryKey):
    __tablename__ = "chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The chunk's heading path, joined — e.g. `"Returns Policy > International Orders"`. Set by
    #: the structural chunker; `NULL` for every chunk the character splitter produced.
    #:
    #: It exists so the **keyword** half of hybrid retrieval can see the heading, which is what
    #: the FTS index is built over (`rag/fts.SEARCHABLE_SQL`). The dense half gets the same
    #: context through `TextChunk.embed_text`, and measuring only that half is how docs/14 K2-5
    #: ended up net negative: moving the heading out of `content` improved the vector and
    #: silently removed the term from the lexical index. Duplicated here rather than read from
    #: `metadata->>'heading'` because an expression index over a JSONB extraction of an array is
    #: both slower and much easier to get subtly wrong.
    heading: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
