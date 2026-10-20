"""Connection schemas. Secrets go in as `auth`, come back only as `auth` with masked values."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field


class CreateConnectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048)
    auth_type: str = "none"
    # Per auth_type, see app.tools.connections.AUTH_FIELDS. Write-only: reads return it masked.
    auth: dict[str, Any] = Field(default_factory=dict)
    default_headers: dict[str, str] = Field(default_factory=dict)
    # Defaults to the base URL's host alone.
    allowed_hosts: list[str] | None = None
    timeout_s: int = Field(default=15, ge=1, le=30)
    health_path: str | None = Field(default=None, max_length=512)
    enabled: bool = True


class UpdateConnectionRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    auth_type: str | None = None
    # A secret left blank (or sent back masked) keeps the stored value.
    auth: dict[str, Any] | None = None
    default_headers: dict[str, str] | None = None
    allowed_hosts: list[str] | None = None
    timeout_s: int | None = Field(default=None, ge=1, le=30)
    health_path: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None


class ConnectionOut(BaseModel):
    id: uuid.UUID
    name: str
    base_url: str
    auth_type: str
    auth: dict[str, Any]
    default_headers: dict[str, str]
    allowed_hosts: list[str]
    timeout_s: int
    health_path: str | None
    enabled: bool
    tools_count: int = 0
    created_at: dt.datetime
    updated_at: dt.datetime


class ConnectionTestResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None
