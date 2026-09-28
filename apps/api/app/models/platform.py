"""Platform-level models: API keys, webhooks, audit, usage metering, quotas, billing."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class ApiKey(Base, UUIDPrimaryKey):
    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WebhookEndpoint(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "webhook_endpoints"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    events: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    secret: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WebhookDelivery(Base, UUIDPrimaryKey):
    __tablename__ = "webhook_deliveries"

    webhook_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    next_retry_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base, UUIDPrimaryKey):
    __tablename__ = "audit_logs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UsageRecord(Base, UUIDPrimaryKey):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "agent_id", "date", "provider", "model"),
        Index("ix_usage_org_date", "organization_id", "date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tokens_prompt: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class Quota(Base, UUIDPrimaryKey):
    __tablename__ = "quotas"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[str] = mapped_column(String(16), default="month", nullable=False)
    token_limit: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_used: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    request_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resets_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class OrgMessageUsage(Base):
    """Per-org message counter for metered plans (docs/18 §7, ADR-088).

    A separate table rather than columns on `Quota`: `Quota` is token/request-shaped, is not
    unique per org, and other code reads it — reshaping it here would change what those readers
    see. One row per metered org; `legacy` orgs have none (unlimited, nothing to count).

    `messages_used` moves only through the single atomic UPDATE in `app/billing/usage.py`.
    """

    __tablename__ = "org_message_usage"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    messages_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    #: Visitor messages saved but not answered because the plan ran out — the upgrade argument.
    unanswered_messages: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    #: Lifecycle emails already sent, so a re-run of the sweep never sends one twice.
    emails_sent: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: The counter's current 30-day window (docs/22 §6). `NULL` `period_end` means the window
    #: never rolls — `trial` and `legacy` stay on the lifetime-counter semantics they always
    #: had. A paid plan gets a real window via `billing.usage.start_period()`, and `reserve()`
    #: rolls a stale one lazily, in the same statement that increments — never a cron (the same
    #: reasoning `plans.py` uses for computed expiry: a scheduler that died would leave paying
    #: customers blocked).
    period_start: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    period_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Extra messages bought for the *current* period (docs/22 §7). Added to the plan's own cap
    #: to make `Entitlements.effective_max_messages` — a pack that didn't raise the enforced cap
    #: would be money taken for nothing. Reset to 0 on every rollover; packs never carry over.
    extra_messages: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlanGrant(Base, UUIDPrimaryKey):
    """Append-only billing evidence: what an org was granted, extended, revoked or sold
    (docs/22 §5). `Organization.plan_*` only holds the *current* state — this answers "what did
    we give this client in July?"

    Never updated or deleted after insert. Every write here is paired with a `write_audit(...)`
    call in `app/billing/cycles.py`: this table is the billing view, `audit_logs` the security
    view — two records on purpose.
    """

    __tablename__ = "plan_grants"
    __table_args__ = (Index("ix_plan_grants_org", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    #: granted | extended | revoked | pack_added | payment_marked
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    from_plan: Mapped[str | None] = mapped_column(String(32))
    to_plan: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set only for `pack_added` rows.
    extra_messages: Mapped[int | None] = mapped_column(Integer)
    #: What was invoiced, in cents — recorded for your records, **never charged**. Nothing in
    #: this codebase moves money (docs/22 §5).
    amount_usd_cents: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    #: A `pack_added` row not yet reflected in an invoice you sent the client (docs/22 §7's
    #: "Uninvoiced packs" view) — a flag on the row, not a status, since the row itself is
    #: append-only evidence and must never be edited otherwise.
    invoiced: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BillingCycle(Base, UUIDPrimaryKey):
    """One 30-day payment cycle per workspace (docs/22 §5.1) — the payment ledger.

    The app can never know a bank transfer arrived; a staff member's click in the admin panel
    is the only source of truth, exactly like the plan grant itself. **Payment status never
    gates access** — access is decided by `Organization.plan_expires_at` alone
    (`app/billing/cycles.payment_state` is informational, read by staff only).
    """

    __tablename__ = "billing_cycles"
    __table_args__ = (
        Index("ix_billing_cycles_org", "organization_id", "period_start"),
        Index("ix_billing_cycles_status", "status", "period_end"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    #: The plan at the time this cycle opened — a later plan change never rewrites history.
    plan: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Copied from `PlanSpec.price_usd_month * 100` at cycle open, so a later price change
    #: never rewrites this cycle's amount.
    amount_usd_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    #: pending | paid | waived. Never a fourth value here — "overdue" is computed
    #: (`payment_state()`), not stored, since it is a function of the clock, not an event.
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", nullable=False)
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    method: Mapped[str | None] = mapped_column(String(32))
    reference: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(500))
    marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrgStorageUsage(Base):
    """Per-org document storage accounting (docs/22 §3 rule 2's hard limit), same one-row-per-org
    shape as `OrgMessageUsage`. Backfilled from `documents.size_bytes` in migration 0029; kept
    current by whatever ingest/delete path writes/removes a document (outside this phase)."""

    __tablename__ = "org_storage_usage"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    bytes_used: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    documents_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FeatureFlag(Base, UUIDPrimaryKey, TimestampMixin):
    """Platform-wide feature flag, toggled by platform staff (docs/08 §17)."""

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))


class Subscription(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "subscriptions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    plan: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(32))
    current_period_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
