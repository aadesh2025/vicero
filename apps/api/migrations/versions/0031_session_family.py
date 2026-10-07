"""session_family_id for refresh-token reuse detection (docs/SECURITY.md §1)

Reusing an already-rotated refresh token now revokes every session in its family, not just
itself (the `refresh()` service function). Existing sessions each become their own singleton
family — `gen_random_uuid()` as the server default backfills every current row at add-time;
new rows always set this explicitly in app code (inherited from the parent on rotation, fresh
on login/signup).

Reversible: downgrade() drops the column.

Revision ID: 0031_session_family
Revises: 0030_billing_currency
Create Date: 2026-10-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_session_family"
down_revision: str | None = "0030_billing_currency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "session_family_id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
    )
    op.create_index("ix_sessions_session_family_id", "sessions", ["session_family_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_session_family_id", table_name="sessions")
    op.drop_column("sessions", "session_family_id")
