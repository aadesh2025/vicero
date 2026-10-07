"""Multi-currency ledger: `currency` + `amount_minor` on billing_cycles and plan_grants (ADR-106).

Revision ID: 0030_billing_currency
Revises: 0029_paid_plans_admin_grants

Amounts become INTEGER MINOR UNITS (cents / euro cents / paise) in an explicit currency. Existing
rows are USD and are backfilled `amount_minor = amount_usd_cents`. `amount_usd_cents` is kept and
still written for USD rows only (so old readers stay correct) — which means it can no longer be
NOT NULL on billing_cycles: an INR cycle has no USD amount.

Downgrade drops the new columns; non-USD cycles get `amount_usd_cents = 0` first so the old
NOT NULL constraint can be restored (a non-USD amount cannot be represented in the old shape).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_billing_currency"
down_revision: str | None = "0029_paid_plans_admin_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "currency IN ('USD', 'EUR', 'INR')"


def upgrade() -> None:
    for table in ("billing_cycles", "plan_grants"):
        op.add_column(
            table, sa.Column("currency", sa.String(3), nullable=False, server_default="USD")
        )
        op.add_column(table, sa.Column("amount_minor", sa.BigInteger(), nullable=True))
        op.execute(f"UPDATE {table} SET amount_minor = amount_usd_cents")
        op.create_check_constraint(f"ck_{table}_currency", table, _CHECK)
    # Every cycle has an amount: backfilled above, and written on every new row.
    op.alter_column("billing_cycles", "amount_minor", nullable=False)
    op.alter_column("billing_cycles", "amount_usd_cents", nullable=True)


def downgrade() -> None:
    op.execute("UPDATE billing_cycles SET amount_usd_cents = 0 WHERE amount_usd_cents IS NULL")
    op.alter_column("billing_cycles", "amount_usd_cents", nullable=False)
    for table in ("plan_grants", "billing_cycles"):
        op.drop_constraint(f"ck_{table}_currency", table, type_="check")
        op.drop_column(table, "amount_minor")
        op.drop_column(table, "currency")
