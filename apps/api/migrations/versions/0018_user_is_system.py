"""Mark machine accounts so the admin roster can hide them

Revision ID: 0018_user_is_system
Revises: 0017_conversation_flags
Create Date: 2026-08-05

`provision@vicero.dev` is a working credential — `scripts/provision-client.mjs` signs in as
it to create client orgs — so it cannot simply be deleted to tidy the Users list. This flags it
as a machine account instead, which the admin console filters out by default.

The backfill matches the seeded/provisioning domains only. A real operator's account can never
be caught by it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_user_is_system"
down_revision: str | None = "0017_conversation_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE users SET is_system = true WHERE email LIKE '%@vicero.dev'"
    )


def downgrade() -> None:
    op.drop_column("users", "is_system")
