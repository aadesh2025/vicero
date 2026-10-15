"""Booking tools: what the model may send (typed, validated) and what comes back (ADR-120).

The three tools are n8n workflows bound to an agent. The model sees the JSON Schemas in `TOOL_SPECS`; the same
rules are enforced again here (never trust the model, never trust the workflow) when n8n calls back with the call.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_PHONE = re.compile(r"^\+?\d{7,15}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Control characters (except tab/newline in notes) have no place in a name, an address or a calendar title.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def normalize_contact(raw: str) -> str:
    """`Name@Example.com` -> lowercased email; `+91 98765-43210` -> `+919876543210`. Raises ValueError."""
    value = raw.strip()
    if _EMAIL.match(value):
        return value.lower()
    digits = re.sub(r"[\s().-]", "", value)
    if _PHONE.match(digits):
        return digits
    raise ValueError("contact must be an email address or a phone number")


def clean_text(value: str, *, multiline: bool = False) -> str:
    text = _CONTROL.sub("", value).strip()
    return text if multiline else " ".join(text.split())


class BookArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=120)
    contact: str = Field(min_length=3, max_length=255)
    start_iso: str = Field(min_length=10, max_length=40)
    notes: str | None = Field(default=None, max_length=500)
    #: Optional caller-chosen key; by default the tool-run id is the key, which is what makes a retry safe.
    idempotency_key: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = clean_text(v)
        if not v:
            raise ValueError("name is required")
        return v

    @field_validator("contact")
    @classmethod
    def _contact(cls, v: str) -> str:
        return normalize_contact(v)

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str | None) -> str | None:
        return clean_text(v, multiline=True) or None if v else None


class AvailabilityArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str = Field(min_length=10, max_length=10)

    @field_validator("date")
    @classmethod
    def _date(cls, v: str) -> str:
        if not _DATE.match(v):
            raise ValueError("date must be YYYY-MM-DD")
        dt.date.fromisoformat(v)  # raises ValueError for 2026-02-31
        return v


class CustomerArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact: str = Field(min_length=1, max_length=255)

    @field_validator("contact")
    @classmethod
    def _contact(cls, v: str) -> str:
        v = clean_text(v)
        if not v:
            raise ValueError("contact is required")
        return v


class ToolCall(BaseModel):
    """What the n8n workflow posts to Vicero: the run it is serving, the token proving it, and the validated args."""

    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    callback_token: str = Field(min_length=16, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)


# ── what the model is shown ───────────────────────────────────────────────────────────

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "book_appointment",
        "slug": "book-appointment",
        "description": (
            "Book an appointment once you have the customer's name, an email address or phone number, and the exact "
            "start time they agreed to (check_availability first). Returns a booking id. A confirmation message is "
            "sent to the customer automatically; do not repeat the details, just close the conversation warmly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 120, "description": "Customer's full name."},
                "contact": {"type": "string", "description": "Customer's email address or phone number."},
                "start_iso": {
                    "type": "string",
                    "description": "Start time, ISO 8601 with offset, e.g. 2026-10-20T15:00:00+05:30.",
                },
                "notes": {"type": "string", "maxLength": 500, "description": "What the customer wants to discuss."},
            },
            "required": ["name", "contact", "start_iso"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_availability",
        "slug": "check-availability",
        "description": "List the free appointment slots on one day. Use before booking, and offer the customer a few.",
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "The day to check, YYYY-MM-DD."}},
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_customer",
        "slug": "get-customer",
        "description": (
            "Look up the person you are chatting with: their name, their last booking and any notes. Only works for "
            "the current customer; pass the email or phone they gave you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"contact": {"type": "string", "description": "Their email address or phone number."}},
            "required": ["contact"],
            "additionalProperties": False,
        },
    },
]
