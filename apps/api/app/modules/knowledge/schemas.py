"""Knowledge-base, document, chunk, and search schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.rag.fts import DEFAULT_CONFIG, FtsConfigName
from app.rag.retrieval import Citation

EMBEDDING_PROVIDERS = ("ollama", "openai", "gemini", "fake")


class CreateKBRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    embedding_provider: str = Field(default="ollama", pattern="^(ollama|openai|gemini|fake)$")
    embedding_model: str = Field(default="nomic-embed-text", max_length=128)
    chunk_size: int = Field(default=1000, ge=64, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    #: Language for the keyword half of hybrid retrieval. `simple` (tokenise, never stem) is
    #: the honest choice for a language Postgres has no dictionary for.
    fts_config: FtsConfigName = DEFAULT_CONFIG


class UpdateKBRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    chunk_size: int | None = Field(default=None, ge=64, le=8000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=2000)
    fts_config: FtsConfigName | None = None


class AttachedAgent(BaseModel):
    """An agent whose RAG config points at this knowledge base."""

    id: uuid.UUID
    name: str
    #: True when the *published* version is one of the versions referencing it — i.e. deleting
    #: the KB changes what real visitors are answered with, not just a draft.
    is_live: bool


class KBOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    embedding_provider: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    fts_config: str
    document_count: int
    #: Populated on the detail endpoint only — the list would need one query per card, and
    #: nothing on the list acts on it.
    attached_agents: list[AttachedAgent] = []
    created_at: dt.datetime
    updated_at: dt.datetime


class CreateDocumentRequest(BaseModel):
    """Add a document from raw text, one web page (`url`) or a whole website (`website`).

    File uploads use the /upload endpoint. `max_pages` applies to `website` only and is capped
    server-side by `CRAWL_MAX_PAGES_LIMIT`.
    """

    source_type: str = Field(pattern="^(text|url|website)$")
    text: str | None = None
    url: str | None = Field(default=None, max_length=2048)
    filename: str | None = Field(default=None, max_length=512)
    max_pages: int | None = Field(default=None, ge=1, le=500)


class IngestProgress(BaseModel):
    """Where a queued/processing document is right now (app/rag/progress.py)."""

    stage: str
    done: int = 0
    total: int = 0


class CrawlPage(BaseModel):
    url: str
    title: str = ""
    chars: int = 0


class CrawlInfo(BaseModel):
    """What a whole-website import read. `pages` is empty until the crawl has run."""

    max_pages: int | None = None
    pages: list[CrawlPage] = []
    warnings: list[str] = []
    stopped_by: str | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    source_type: str
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    source_url: str | None
    status: str
    error_message: str | None
    chunk_count: int
    #: `{kind: count}` from the ingest-time PII scan (docs/11 Phase B). Counts only — the
    #: values are never returned. `None` = ingested before the scan existed and never
    #: checked; `{}` = scanned and clean. The UI must not present those as the same thing.
    pii_flags: dict[str, int] | None = None
    #: Live stage while ingesting (`None` once ready/failed, or when progress is unavailable).
    progress: IngestProgress | None = None
    #: Whole-website imports only.
    crawl: CrawlInfo | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ChunkOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    content: str
    token_count: int
    metadata: dict[str, Any]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    hybrid: bool = True


class SearchResponse(BaseModel):
    query: str
    citations: list[Citation]
