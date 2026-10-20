"""Inbound hook schemas. The secret appears in a response exactly twice: on create and on rotate."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

ACTIONS = ("send_message", "run_workflow", "set_variables", "agent_turn")


class CreateInboundHookRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    action: str = Field(pattern="^(send_message|run_workflow|set_variables|agent_turn)$")
    # Per action, see app/modules/inbound_hooks/actions.py. Examples:
    #   send_message  {"channel": "whatsapp", "contact_field": "phone", "message": "Order {{order_id}} shipped"}
    #   run_workflow  {"workflow_id": "<uuid>"}
    #   set_variables {"channel": "whatsapp", "contact_field": "phone"}
    #   agent_turn    {"message": "Order {{order_id}} status changed to {{status}}."}
    action_config: dict[str, Any] = Field(default_factory=dict)
    # {field: "$.json.path"}: which parts of the caller's JSON the action may use. Nothing else is read.
    payload_mapping: dict[str, str] = Field(default_factory=dict)
    auth_mode: str = Field(default="hmac", pattern="^(hmac|bearer)$")
    enabled: bool = True


class UpdateInboundHookRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    action_config: dict[str, Any] | None = None
    payload_mapping: dict[str, str] | None = None
    enabled: bool | None = None


class InboundHookOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    action: str
    action_config: dict[str, Any]
    payload_mapping: dict[str, str]
    auth_mode: str
    enabled: bool
    # Path on the API host, e.g. /v1/hooks/in/<public_id>. Prefix it with your API base URL.
    url_path: str
    last_called_at: dt.datetime | None
    created_at: dt.datetime
    # Only set on create and rotate-secret.
    secret: str | None = None


class InboundHookEventOut(BaseModel):
    id: uuid.UUID
    status: str
    http_status: int | None
    latency_ms: int | None
    payload_sanitized: dict[str, Any] | None
    error: str | None
    created_at: dt.datetime
