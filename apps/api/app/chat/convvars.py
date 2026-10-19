"""Conversation variables (docs/28 T4.1, ADR-135): a small key/value bag on a conversation.

Filled by the `set_variable` / `collect_field` tools, an API tool's `capture` mapping, and inbound hooks; read by
`get_variable`, by `{{vars.name}}` in the agent's prompt (through the Phase F escaping) and in API tool templates.

Every value is untrusted - a customer typed it, or a third-party API returned it - so it is cleaned before it is
stored, and escaped again whenever it is interpolated into a prompt.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.guardrails import neutralize_injections
from app.models import Conversation
from app.webhooks.dispatch import emit_event, sanitize_payload

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_VARIABLES = 50
MAX_VALUE_CHARS = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class VariableError(ValueError):
    """The name or value cannot be stored. The message is safe to hand back to the model."""


def clean_value(value: Any) -> Any:
    """Numbers and booleans pass through; everything else becomes capped, control-free, neutralized text."""
    if isinstance(value, bool | int | float) or value is None:
        return value
    text = value if isinstance(value, str) else str(value)
    return neutralize_injections(_CONTROL.sub("", text)).strip()[:MAX_VALUE_CHARS]


def check_name(name: str) -> str:
    key = str(name).strip().lower()
    if not NAME_RE.match(key):
        raise VariableError("A variable name starts with a letter and uses lowercase letters, digits and '_' (max 40).")
    return key


async def apply(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    updates: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    """Merge `updates` into the conversation's variables and announce what was captured. Returns what was stored."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise VariableError("There is no conversation to store this on.")
    current = dict(conv.variables or {})
    stored: dict[str, Any] = {}
    for raw_name, raw_value in updates.items():
        name = check_name(raw_name)
        if name not in current and len(current) >= MAX_VARIABLES:
            raise VariableError(f"A conversation can hold at most {MAX_VARIABLES} variables.")
        value = clean_value(raw_value)
        current[name] = value
        stored[name] = value
    if stored:
        conv.variables = current  # a new dict, so the JSONB change is seen
        await session.flush()
        await emit_event(
            session,
            conv.organization_id,
            "variables.captured",
            {
                "agent_id": str(conv.agent_id),
                "conversation_id": str(conv.id),
                "source": source,
                "variables": sanitize_payload(stored),
            },
            agent_id=conv.agent_id,
        )
    return stored


def prompt_context(variables: dict[str, Any] | None) -> dict[str, Any]:
    """Raw variables for prompt rendering; `variables.build_context` escapes each one."""
    return dict(variables or {})
