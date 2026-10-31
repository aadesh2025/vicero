"""Provider-credential schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field

#: How a provider's key was resolved, in the order `resolve_credential()` looks:
#: an org-stored credential, the platform env key, or the provider needing no key at all.
KeySource = Literal["org", "env", "not_required", "none"]


class CredentialCreate(BaseModel):
    # Validated against the catalog in the service rather than by a pattern here: a regex
    # listing provider names drifts the moment a provider is added to `llm/catalog.py`.
    provider: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=255)
    api_key: str = Field(min_length=1, max_length=512)
    base_url: str | None = Field(default=None, max_length=1024)
    is_default: bool = False


class CredentialUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    base_url: str | None = Field(default=None, max_length=1024)
    is_default: bool | None = None


class ProviderKeyUpsert(BaseModel):
    """Body of `PUT /v1/credentials/providers/{name}` — one key per provider.

    The Settings page presents providers, not credential rows: an operator clicks a provider
    and types a key. Re-saving replaces the key in place instead of stacking a second row that
    `resolve_credential()` would then have to arbitrate between.
    """

    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = Field(default=None, max_length=1024)
    label: str | None = Field(default=None, max_length=255)


class CredentialOut(BaseModel):
    id: uuid.UUID
    provider: str
    label: str | None
    masked_key: str
    base_url: str | None
    is_default: bool
    created_at: dt.datetime


class ModelOut(BaseModel):
    id: str
    label: str
    context: int | None = None
    tools: bool | None = None
    note: str | None = None
    #: False when no per-1K rate is published for this model, so a cost of $0 in analytics
    #: means "not tracked" rather than "free". Always True for free-tier providers.
    pricing_known: bool = False


class ProviderInfo(BaseModel):
    name: str
    label: str
    free: bool
    requires_key: bool
    #: Model ids only — the original shape, kept for existing callers.
    models: list[str]
    available_models: list[ModelOut]
    #: True when this org can actually run the provider today.
    configured: bool
    key_source: KeySource
    masked_key: str | None = None
    credential_id: uuid.UUID | None = None
    base_url: str | None = None
    base_url_required: bool = False
    api_key_url: str | None = None
    key_hint: str | None = None
    description: str | None = None


class ProviderModels(BaseModel):
    provider: str
    #: `live` = the provider's own model list; `catalog` = the static seed in `llm/catalog.py`
    #: because discovery was unavailable (no key, network, or the provider has no models API).
    source: Literal["live", "catalog"]
    models: list[ModelOut]
    error: str | None = None


class CredentialTestResult(BaseModel):
    ok: bool
    models: list[str] | None = None
    error: str | None = None
    chat_ok: bool = False
    tools_ok: bool | None = None
    status: Literal["ok", "invalid_key", "unreachable", "chat_unavailable", "tools_unsupported"] | None = None
