"""Whole-website import: what was crawled, recorded on the document (ADR-139).

Revision ID: 0042_document_crawl
Revises: 0041_tool_behaviour

Additive and nullable. `documents.crawl_info` holds the page limit a website import was created with
and, after ingest, which pages were read and any warnings (JavaScript-only pages, a page cap reached).
NULL for every existing document, and for every file, text and single-URL document.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.db.migration_helpers import views_suspended

revision: str = "0042_document_crawl"
down_revision: str | None = "0041_tool_behaviour"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("crawl_info", JSONB))


def downgrade() -> None:
    with views_suspended("documents", best_effort=True):
        op.drop_column("documents", "crawl_info")
