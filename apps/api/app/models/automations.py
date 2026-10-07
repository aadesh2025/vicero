"""Client-visible automations (docs/26): staff-built n8n workflows, their requests and their run log.

Every row carries `organization_id`, and every query in `app.modules.automations` filters on it. n8n itself is
never shown to a client: these tables are the only thing a tenant can read.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

REQUEST_STATUSES = ("requested", "building", "active", "paused", "rejected")
AUTOMATION_STATUSES = ("active", "paused")
RUN_STATUSES = ("success", "failed", "running")


class AutomationRequest(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "automation_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','building','active','paused','rejected')", name="ck_automation_requests_status"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested", server_default="requested")
    #: Plain-language note shown to the client when staff reject or finish a request.
    staff_note: Mapped[str | None] = mapped_column(String(500))


class Automation(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "automations"
    __table_args__ = (
        CheckConstraint("status IN ('active','paused')", name="ck_automations_status"),
        # One n8n workflow belongs to exactly one automation, hence one org. This is what lets the ingestion
        # endpoint prove a reported run belongs to the org it claims.
        UniqueConstraint("n8n_workflow_id", name="uq_automations_n8n_workflow_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("automation_requests.id", ondelete="SET NULL"))
    n8n_workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `/webhook/<path>` of the workflow's trigger, when an agent may call it as a tool.
    webhook_path: Mapped[str | None] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    #: Per-org config, encrypted with the app key (`app.core.crypto`). Never returned by any endpoint.
    config_encrypted: Mapped[str | None] = mapped_column(Text)


class AutomationRun(Base, UUIDPrimaryKey):
    __tablename__ = "automation_runs"
    __table_args__ = (
        CheckConstraint("status IN ('success','failed','running')", name="ck_automation_runs_status"),
        # Reporting the same execution twice (push retry, or push + the pull backup) is one row.
        UniqueConstraint("automation_id", "n8n_execution_id", name="uq_automation_runs_execution"),
        Index("ix_automation_runs_org_started", "organization_id", "started_at"),
        Index("ix_automation_runs_automation_started", "automation_id", "started_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    automation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"))
    n8n_execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    #: Plain-language, already sanitized (`app.modules.automations.sanitize`). Never a raw n8n error.
    error_summary: Mapped[str | None] = mapped_column(String(500))
    input_summary: Mapped[str | None] = mapped_column(String(2000))
    output_summary: Mapped[str | None] = mapped_column(String(2000))
    #: True when this run was recorded after the plan's monthly run cap was reached (counted, flagged).
    over_cap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
