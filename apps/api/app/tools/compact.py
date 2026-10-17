"""Compact tool definitions: what the model is shown, without what only the server needs (ADR-125).

Every tool definition is sent on every model call, and a turn that uses a tool makes two. Keywords that only
constrain values (`minLength`, `maxLength`, `default`, `additionalProperties`) and `title`s do not help the model
choose arguments, and the server validates the call again anyway. Descriptions are collapsed to one short run.
"""

from __future__ import annotations

from typing import Any

#: Schema keywords the model does not need. Property NAMES inside `properties` are never touched.
_DROP = frozenset({"title", "additionalProperties", "minLength", "maxLength", "default", "examples", "$schema"})
DESCRIPTION_LIMIT = 200


def compact_description(text: str | None, limit: int = DESCRIPTION_LIMIT) -> str:
    """Whitespace collapsed, cut at the last sentence end within `limit`, else at `limit`."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end >= limit // 2 else cut.rstrip() + "..."


def compact_schema(node: Any, *, names: bool = False) -> Any:
    """A copy of a JSON schema without the keywords in `_DROP`. `names=True` marks a `properties` mapping."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if not names and key in _DROP:
                continue
            if key == "description" and isinstance(value, str) and not names:
                out[key] = compact_description(value, 120)
            elif key == "properties" and isinstance(value, dict) and not names:
                out[key] = {name: compact_schema(sub) for name, sub in value.items()}
            else:
                out[key] = compact_schema(value)
        return out
    if isinstance(node, list):
        return [compact_schema(item) for item in node]
    return node
