"""Interactive chat blocks stored with the assistant message (docs/27 Phase 2).

Revision ID: 0037_message_blocks
Revises: 0036_media_and_products

Additive: one nullable JSONB column. NULL for every existing message and for every turn that did not
show a block, so nothing reads differently until an agent opts in. Safe to run with the API up.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0037_message_blocks"
down_revision: str | None = "0036_media_and_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("blocks", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "blocks")
