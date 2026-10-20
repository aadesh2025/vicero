"""How a tool behaves for one agent (docs/28 T4, ADR-135): spoken messages, approval, rate limits, sync or async.

The tool row holds the defaults. A binding (`agent_tool_bindings.overrides`) may override them for one agent, with one
deliberate asymmetry: an override can make a tool *safer* (require approval, lower a limit) but never less safe, so an
agent editor cannot switch off a protection the tool's owner set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.errors import AppError
from app.models import Tool

MAX_MESSAGE_CHARS = 300
MAX_DELAY_S = 30
MAX_LIMIT = 100_000
#: Keys a binding override may carry.
OVERRIDE_KEYS = frozenset({"description", "messages", "approval_required", "rate_limit"})


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    messages: dict[str, Any] | None = None  # {on_start: str, on_delay: {after_s, text}, on_error: str}
    approval_required: bool = False
    rate_limit: dict[str, int] | None = None  # {per_conversation: n, per_minute: n}
    mode: str = "sync"  # sync | async


def clean_messages(raw: Any) -> dict[str, Any] | None:
    """Validate the messages a visitor will read while a tool runs. Plain text only, short, never raw errors."""
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise AppError("tools.bad_messages", "messages must be an object.", 400)
    extra = set(raw) - {"on_start", "on_delay", "on_error"}
    if extra:
        raise AppError("tools.bad_messages", f"Unknown message(s): {', '.join(sorted(extra))}.", 400)
    out: dict[str, Any] = {}
    for key in ("on_start", "on_error"):
        if raw.get(key):
            out[key] = _text(raw[key], key)
    delay = raw.get("on_delay")
    if delay:
        if not isinstance(delay, dict):
            raise AppError("tools.bad_messages", "on_delay must be {after_s, text}.", 400)
        try:
            after = float(delay.get("after_s", 4))
        except (TypeError, ValueError):
            raise AppError("tools.bad_messages", "on_delay.after_s must be a number.", 400) from None
        if not 1 <= after <= MAX_DELAY_S:
            raise AppError("tools.bad_messages", f"on_delay.after_s must be between 1 and {MAX_DELAY_S}.", 400)
        out["on_delay"] = {"after_s": after, "text": _text(delay.get("text"), "on_delay.text")}
    return out or None


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("tools.bad_messages", f"{label} must be text.", 400)
    text = value.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        raise AppError("tools.bad_messages", f"{label} is over {MAX_MESSAGE_CHARS} characters.", 400)
    if "{{" in text:
        raise AppError("tools.bad_messages", f"{label} cannot contain placeholders.", 400)
    return text


def clean_rate_limit(raw: Any) -> dict[str, int] | None:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict) or set(raw) - {"per_conversation", "per_minute"}:
        raise AppError("tools.bad_rate_limit", "rate_limit takes per_conversation and/or per_minute.", 400)
    out: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
            raise AppError("tools.bad_rate_limit", f"{key} must be a whole number from 1 to {MAX_LIMIT}.", 400)
        out[key] = value
    return out or None


def clean_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a binding's overrides (same rules as the tool's own settings)."""
    extra = set(raw) - OVERRIDE_KEYS
    if extra:
        raise AppError("tools.bad_override", f"Unsupported override(s): {', '.join(sorted(extra))}.", 400)
    out: dict[str, Any] = {}
    if "description" in raw:
        if not isinstance(raw["description"], str):
            raise AppError("tools.bad_override", "The description override must be text.", 400)
        out["description"] = raw["description"]
    if "messages" in raw:
        out["messages"] = clean_messages(raw["messages"])
    if "approval_required" in raw:
        if not isinstance(raw["approval_required"], bool):
            raise AppError("tools.bad_override", "approval_required must be true or false.", 400)
        out["approval_required"] = raw["approval_required"]
    if "rate_limit" in raw:
        out["rate_limit"] = clean_rate_limit(raw["rate_limit"])
    return out


def _tighter(a: dict[str, int] | None, b: dict[str, int] | None) -> dict[str, int] | None:
    if not a or not b:
        return a or b
    merged = dict(a)
    for key, value in b.items():
        merged[key] = min(value, merged.get(key, value))
    return merged


def effective_policy(tool: Tool, overrides: dict[str, Any] | None = None) -> ToolPolicy:
    """The tool's own settings with an agent's overrides applied (safer only; see the module docstring)."""
    o = overrides or {}
    return ToolPolicy(
        messages=o.get("messages") or tool.messages or None,
        approval_required=bool(tool.approval_required) or bool(o.get("approval_required")),
        rate_limit=_tighter(tool.rate_limit or None, o.get("rate_limit") or None),
        mode=tool.mode or "sync",
    )
