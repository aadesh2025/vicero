"""Webhook endpoint + delivery schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field


class CreateWebhookRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    events: list[str] = Field(default_factory=lambda: ["*"])
    secret: str | None = None
    # Only this agent's events. Leave out for an org-wide endpoint (every event).
    agent_id: uuid.UUID | None = None


class UpdateWebhookRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    events: list[str] | None = None
    enabled: bool | None = None
    secret: str | None = None


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    agent_id: uuid.UUID | None = None
    events: list[str]
    enabled: bool
    secret: str | None  # returned once on create; masked afterwards
    created_at: dt.datetime


class WebhookDeliveryOut(BaseModel):
    id: uuid.UUID
    event: str
    status: str
    attempts: int
    response_status: int | None
    next_retry_at: dt.datetime | None
    created_at: dt.datetime
