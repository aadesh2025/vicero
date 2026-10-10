"""Channel schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

CHANNEL_TYPES = ("telegram", "whatsapp", "instagram", "facebook", "slack", "discord")


class CreateChannelRequest(BaseModel):
    agent_id: uuid.UUID
    type: str = Field(pattern=f"^({'|'.join(CHANNEL_TYPES)})$")
    name: str | None = Field(default=None, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)


class UpdateChannelRequest(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class ChannelOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    type: str
    name: str | None
    enabled: bool
    config: dict[str, Any]  # secrets masked
    webhook_url: str | None
    # Shared secret the provider echoes back to prove an inbound webhook is genuine.
    # Shown to the channel owner so they can configure the provider side (and verify).
    webhook_secret: str | None
    created_at: dt.datetime
    # Meta one-click connect (ADR-113). Manual channels report connection_source="manual", status="active".
    connection_source: str = "manual"
    status: str = "active"
    external_id: str | None = None
    external_parent_id: str | None = None
    token_expires_at: dt.datetime | None = None
    last_health_check_at: dt.datetime | None = None
