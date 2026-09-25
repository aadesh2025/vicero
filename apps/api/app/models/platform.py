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
