"""Product catalog schemas (docs/26 §5-6)."""

from __future__ import annotations

import datetime as dt
import re
import uuid

from pydantic import BaseModel, Field, field_validator

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
MAX_ATTRIBUTES = 20


def _validate_currency(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    code = v.strip().upper()
    if not _CURRENCY_RE.match(code):
        raise ValueError("must be a 3-letter currency code such as INR or USD")
    return code


def _validate_url(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    url = v.strip()
    if not re.match(r"^https?://\S+$", url, re.IGNORECASE):
        raise ValueError("must be an http(s) link")
    return url


def _validate_attributes(v: dict[str, str]) -> dict[str, str]:
    if len(v) > MAX_ATTRIBUTES:
        raise ValueError(f"at most {MAX_ATTRIBUTES} attributes")
    out: dict[str, str] = {}
    for key, value in v.items():
        k, val = str(key).strip(), str(value).strip()
        if not k or len(k) > 50 or len(val) > 200:
            raise ValueError("attribute names are 1-50 characters and values at most 200")
        out[k] = val
    return out


class ProductImageOut(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    #: A signed, expiring link the dashboard can use in an <img> tag.
    url: str
    position: int
    alt_text: str | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    name: str
    sku: str | None
    price_minor: int | None
    currency: str | None
    description: str
    attributes: dict[str, str]
    in_stock: bool
    url: str | None
    is_active: bool
    images: list[ProductImageOut]
    created_at: dt.datetime
    updated_at: dt.datetime


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


class _ProductFields(BaseModel):
    sku: str | None = Field(default=None, max_length=100)
    price_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    description: str | None = Field(default=None, max_length=8000)
    attributes: dict[str, str] | None = None
    in_stock: bool | None = None
    url: str | None = Field(default=None, max_length=2000)

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return _validate_currency(v)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _validate_url(v)

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else _validate_attributes(v)

    @field_validator("sku")
    @classmethod
    def _sku(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


class CreateProductRequest(_ProductFields):
    name: str = Field(min_length=1, max_length=200)


class UpdateProductRequest(_ProductFields):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class ReorderImagesRequest(BaseModel):
    image_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
