"""Conversation variables and the payload a tool approval carries (docs/28 T4).

Revision ID: 0041_tool_behaviour
Revises: 0040_inbound_hooks

Additive. `conversations.variables` defaults to an empty object, so nothing reads differently until a tool or a
hook writes one. `handoffs.payload` is NULL for every existing handoff; only a tool-approval request fills it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0041_tool_behaviour"
down_revision: str | None = "0040_inbound_hooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("variables", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("handoffs", sa.Column("payload", JSONB))


def downgrade() -> None:
    op.drop_column("handoffs", "payload")
    op.drop_column("conversations", "variables")
