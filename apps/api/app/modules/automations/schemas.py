"""Request/response models for client-visible automations (docs/26).

Nothing here exposes n8n: no workflow id, no webhook path, no config, no raw error text.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── tenant ────────────────────────────────────────────────────────────────────
class RunOut(BaseModel):
    id: uuid.UUID
    status: Literal["success", "failed", "running"]
    started_at: dt.datetime
    finished_at: dt.datetime | None
    duration_ms: int | None
    #: A short plain-language line: the sanitized reason for a failure, or "Completed".
    message: str
    #: True when the run happened after the plan's monthly run limit was reached.
    over_cap: bool


class RunsPage(BaseModel):
    items: list[RunOut]
    total: int
    page: int
    page_size: int


class AutomationCard(BaseModel):
    id: uuid.UUID
    name: str
    status: Literal["active", "paused"]
    agent_id: uuid.UUID | None
    agent_name: str | None
    last_run_at: dt.datetime | None
    last_status: Literal["success", "failed", "running"] | None
    runs_this_month: int
    #: Share of this month's finished runs that succeeded, 0..1. None until a run has finished.
    success_rate: float | None


class AutomationDetail(AutomationCard):
    runs_total: int
    success_this_month: int
    failed_this_month: int
    last_run: RunOut | None
    last_failure: RunOut | None


class UsageOut(BaseModel):
    plan: str
    automations_limit: int | None
    automations_used: int
    runs_limit: int | None
    runs_this_month: int
    #: False when the plan has no automations at all (the page shows an upgrade state).
    included: bool
    #: False when the plan allows automations but every slot is taken (requests or live).
    can_request: bool


class CreateRequest(BaseModel):
    description: str = Field(min_length=10, max_length=2000)
    agent_id: uuid.UUID | None = None


class RequestOut(BaseModel):
    id: uuid.UUID
    description: str
    agent_id: uuid.UUID | None
    status: Literal["requested", "building", "active", "paused", "rejected"]
    staff_note: str | None
    created_at: dt.datetime


# ── staff ─────────────────────────────────────────────────────────────────────
class StaffRequestOut(RequestOut):
    organization_id: uuid.UUID
    organization_name: str
    requested_by_email: str | None


class StaffRequestUpdate(BaseModel):
    status: Literal["requested", "building", "rejected"]
    staff_note: str | None = Field(default=None, max_length=500)


class RegisterAutomation(BaseModel):
    organization_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    #: The n8n workflow's id. One workflow belongs to one automation, hence one org.
    n8n_workflow_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    #: `/webhook/<path>` of the trigger, when an agent may call this as a tool.
    webhook_path: str | None = Field(default=None, max_length=255, pattern=r"^/webhook/[A-Za-z0-9_./-]+$")
    #: Naming convention: "ORG-<org_id> | <client name> | <purpose>". No personal data.
    name: str = Field(min_length=1, max_length=160)
    #: Per-org parameters. Stored encrypted and never returned.
    config: dict[str, Any] | None = None


class UpdateAutomation(BaseModel):
    status: Literal["active", "paused"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    webhook_path: str | None = Field(default=None, max_length=255, pattern=r"^/webhook/[A-Za-z0-9_./-]+$")
    config: dict[str, Any] | None = None


class StaffAutomationOut(AutomationCard):
    organization_id: uuid.UUID
    organization_name: str
    n8n_workflow_id: str
    webhook_path: str | None
    has_config: bool


class StaffRunOut(RunOut):
    n8n_execution_id: str
    input_summary: str | None
    output_summary: str | None


class StaffRunsPage(BaseModel):
    items: list[StaffRunOut]
    total: int
    page: int
    page_size: int


# ── ingestion (n8n -> Vicero) ─────────────────────────────────────────────────
class RunReport(BaseModel):
    """What n8n's "report run to Vicero" step sends. `org_id` is a CLAIM that is checked, never trusted."""

    workflow_id: str = Field(min_length=1, max_length=64)
    org_id: uuid.UUID
    execution_id: str = Field(min_length=1, max_length=64)
    status: Literal["success", "failed", "running"]
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    error: str | None = Field(default=None, max_length=8000)
    input: Any | None = None
    output: Any | None = None


class ReportAck(BaseModel):
    ok: bool = True
    #: False when the execution had already been reported (the existing row was updated, not duplicated).
    created: bool
