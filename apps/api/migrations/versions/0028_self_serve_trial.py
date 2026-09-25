"""self-serve signup + free trial (docs/18, ADR-088)

- `organizations.trial_started_at` / `trial_ends_at`, and `plan` now defaults to `legacy`.
- **Every existing organization is backfilled to `legacy`** so no current client is ever
  metered or locked. (The column previously held `free|pro|enterprise`, which nothing read.)
- `users.email_normalized` (indexed, not unique) backfilled from `email`.
- `org_message_usage`: the per-org message counter + lifecycle-email idempotency flags.

Reversible: `downgrade()` drops everything added and restores the old `free` default. The old
plan strings are not restored per row (they were never read and are not recoverable) — every row
goes back to `free`, which is the value the column defaulted to for every org before this.

Revision ID: 0028_self_serve_trial
Revises: 0027_workflow_tests
Create Date: 2026-09-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_self_serve_trial"
down_revision: str | None = "0027_workflow_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize(email: str) -> str:
    """Frozen copy of `app.modules.auth.policy.normalize_email` — a migration must not import
    application code that may change after it ships."""
    local, _, domain = email.strip().lower().partition("@")
    if domain == "googlemail.com":
        domain = "gmail.com"
    if domain in {"gmail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
    else:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def upgrade() -> None:
    op.add_column("organizations", sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    # Backfill BEFORE changing the default: everything that exists today is a client.
    op.execute("UPDATE organizations SET plan = 'legacy'")
    op.alter_column("organizations", "plan", server_default="legacy")

    op.add_column("users", sa.Column("email_normalized", sa.String(320), nullable=True))
    op.create_index("ix_users_email_normalized", "users", ["email_normalized"])
    conn = op.get_bind()
    for user_id, email in conn.execute(sa.text("SELECT id, email FROM users")).all():
        conn.execute(
            sa.text("UPDATE users SET email_normalized = :n WHERE id = :i"),
            {"n": _normalize(email), "i": user_id},
        )

    op.create_table(
        "org_message_usage",
        sa.Column(
            "organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("messages_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unanswered_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("emails_sent", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("org_message_usage")
    op.drop_index("ix_users_email_normalized", table_name="users")
    op.drop_column("users", "email_normalized")
    op.alter_column("organizations", "plan", server_default=None)
    op.execute("UPDATE organizations SET plan = 'free'")
    op.drop_column("organizations", "trial_ends_at")
    op.drop_column("organizations", "trial_started_at")
