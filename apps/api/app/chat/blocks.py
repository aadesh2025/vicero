"""Interactive chat blocks (docs/27 Phase 2): tappable UI the agent can show instead of plain text.

A block is **data the agent proposes and the server validates** — never markup and never code. The
widget renders every string with `textContent`, only `https:` image URLs, and a reply comes back as an
ordinary user message, so a block adds no new path around the guardrails or the rate limits.

Security shape, in the order a block travels:

1. A tool call (`present_options`, ...) is validated here against hard caps. Anything over a cap, with a
   disallowed field type, or asking for something sensitive is **rejected** and the model is told why, so it
   can fall back to plain text. Nothing is silently truncated into a different meaning.
2. The server — not the model — assigns the block id and writes the `fallback_text`.
3. `run_turn` emits the blocks only after the output guard has passed (`passes_output_guard`): a turn the
   guard rewrote or suppressed shows no blocks, and neither does a turn whose provider failed.
4. They are persisted with the assistant message; `content` already carries the fallback text, so the inbox,
   transcripts and exports show plain text with no knowledge of this module.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import output_guard, pii
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.types import Message
from app.models import Message as DbMessage
from app.models import WidgetConfig

log = get_logger("chat.blocks")

#: Block types the widget can render. Only the first five are offered to the model as tools.
BLOCK_TYPES = ("options", "cards", "form", "datetime", "counter", "rating", "steps", "handoff")
#: Settings keys: the master switch plus one per type. Stored as `interactiveBlocks` on the widget config.
FLAG_KEYS = ("enabled", *BLOCK_TYPES)

MAX_BLOCKS_PER_TURN = 2
MAX_BLOCK_BYTES = 8_000

MAX_OPTIONS, MAX_LABEL = 8, 60
MAX_CARDS, MAX_ACTIONS = 6, 3
MAX_FIELDS = 5
MAX_DAYS, MAX_SLOTS = 14, 12
MAX_COUNTER = 20
MAX_STEPS = 8
MAX_REASONS = 6

FORM_FIELD_TYPES = ("text", "email", "tel")
# A label that asks for something the form must never collect, whatever type the model declared.
_SENSITIVE = re.compile(
    r"\b(password|passcode|pin\s*(?:code|number)?|cvv|cvc|card\s*(?:number|no)|credit\s*card|debit\s*card|"
    r"aadhaa?r|ssn|social\s*security|passport|otp|one[-\s]*time)\b",
    re.IGNORECASE,
)
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class BlockError(ValueError):
    """A block the server refuses to show. The message is written for the model, which sees it."""


# ── Settings ────────────────────────────────────────────────────────────────────────


def normalize_flags(raw: Any) -> dict[str, bool] | None:
    """The stored `interactiveBlocks` map, or None when the feature is off.

    Off means: not a dict, or the master switch is not an explicit True. A type key that is absent
    counts as on once the master switch is on, so adding a new block type later does not need every
    agent re-saved.
    """
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    flags = {"enabled": True}
    for t in BLOCK_TYPES:
        flags[t] = raw.get(t, True) is not False
    return flags


async def load_flags(session: AsyncSession, agent_id: uuid.UUID) -> dict[str, bool] | None:
    """One indexed lookup per widget turn. `widget_configs` is unversioned, so a save is live."""
    theme = (
        await session.execute(select(WidgetConfig.theme).where(WidgetConfig.agent_id == agent_id))
    ).scalar_one_or_none()
    return normalize_flags((theme or {}).get("interactiveBlocks"))


# ── Validation helpers ──────────────────────────────────────────────────────────────


def _text(value: Any, field: str, limit: int, *, required: bool = True) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise BlockError(f"{field} is required")
        return ""
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        raise BlockError(f"{field} must be text")
    text = " ".join(str(value).split())
    if len(text) > limit:
        raise BlockError(f"{field} is {len(text)} characters; the limit is {limit}")
    return text


def _list(value: Any, field: str, limit: int, *, minimum: int = 1) -> list[Any]:
    if not isinstance(value, list):
        raise BlockError(f"{field} must be a list")
    if len(value) < minimum:
        raise BlockError(f"{field} needs at least {minimum} item(s)")
    if len(value) > limit:
        raise BlockError(f"{field} has {len(value)} items; the limit is {limit}")
    return value


def _item_id(value: Any, fallback: int) -> str:
    if isinstance(value, str) and _ID.match(value):
        return value
    return f"i{fallback + 1}"


def https_url(value: Any) -> str | None:
    """`https:` only (a model-supplied URL is untrusted). Anything else is dropped, not rewritten."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if len(url) > 2000 or not url.lower().startswith("https://"):
        return None
    if any(ch in url for ch in " \t\r\n<>\"'`"):
        return None
    return url


def _media_id(value: Any) -> str | None:
    """A catalog image reference. Only ever set by the server from the org's own product rows (a model's
    tool arguments never reach `build_block` for cards), kept so the signed URL can be re-made on read."""
    try:
        return str(uuid.UUID(str(value))) if value else None
    except ValueError:
        return None


# ── Per-type sanitizers: payload in, clean dict out (or BlockError) ─────────────────────


def _options(p: dict[str, Any]) -> dict[str, Any]:
    items = []
    for i, raw in enumerate(_list(p.get("items"), "items", MAX_OPTIONS)):
        if isinstance(raw, str):
            raw = {"label": raw}
        if not isinstance(raw, dict):
            raise BlockError("each option must be an object with a label")
        item = {"id": _item_id(raw.get("id"), i), "label": _text(raw.get("label"), "label", MAX_LABEL)}
        sub = _text(raw.get("sub"), "sub", 120, required=False)
        if sub:
            item["sub"] = sub
        items.append(item)
    out: dict[str, Any] = {"items": items, "multi": bool(p.get("multi"))}
    prog = p.get("progress")
    if prog is not None:
        if not isinstance(prog, dict):
            raise BlockError("progress must be {step, total}")
        try:
            step, total = int(prog.get("step") or 0), int(prog.get("total") or 0)
        except (TypeError, ValueError):
            raise BlockError("progress step and total must be whole numbers") from None
        if not (1 <= step <= total <= 20):
            raise BlockError("progress needs 1 <= step <= total <= 20")
        out["progress"] = {"step": step, "total": total}
    return out


def _cards(p: dict[str, Any]) -> dict[str, Any]:
    items = []
    for i, raw in enumerate(_list(p.get("items"), "items", MAX_CARDS)):
        if not isinstance(raw, dict):
            raise BlockError("each card must be an object")
        card: dict[str, Any] = {
            "id": _item_id(raw.get("id"), i),
            "title": _text(raw.get("title"), "title", 80),
        }
        for key, limit in (("subtitle", 120), ("price", 30), ("badge", 24)):
            val = _text(raw.get(key), key, limit, required=False)
            if val:
                card[key] = val
        img = https_url(raw.get("image_url"))
        if img:
            card["image_url"] = img
        media_id = _media_id(raw.get("image_media_id"))
        if media_id:
            card["image_media_id"] = media_id
        actions = []
        for j, act in enumerate(_list(raw.get("actions") or [{"label": "Choose"}], "actions", MAX_ACTIONS)):
            if isinstance(act, str):
                act = {"label": act}
            if not isinstance(act, dict):
                raise BlockError("each action must be an object with a label")
            actions.append({"id": _item_id(act.get("id"), j), "label": _text(act.get("label"), "label", 30)})
        card["actions"] = actions
        items.append(card)
    return {"items": items}


def _form(p: dict[str, Any]) -> dict[str, Any]:
    fields = []
    for i, raw in enumerate(_list(p.get("fields"), "fields", MAX_FIELDS)):
        if not isinstance(raw, dict):
            raise BlockError("each field must be an object")
        ftype = str(raw.get("type") or "text").lower()
        if ftype not in FORM_FIELD_TYPES:
            raise BlockError(f"field type '{ftype}' is not allowed; use one of {', '.join(FORM_FIELD_TYPES)}")
        label = _text(raw.get("label"), "label", MAX_LABEL)
        if _SENSITIVE.search(label):
            raise BlockError("forms must never ask for passwords, card numbers, ID numbers or one-time codes")
        fields.append(
            {
                "id": _item_id(raw.get("id"), i),
                "label": label,
                "type": ftype,
                "required": raw.get("required", True) is not False,
            }
        )
    if len({f["id"] for f in fields}) != len(fields):
        raise BlockError("field ids must be unique")
    return {"fields": fields, "submit_label": _text(p.get("submit_label") or "Send", "submit_label", 30)}


def _datetime(p: dict[str, Any]) -> dict[str, Any]:
    days = []
    for raw in _list(p.get("days"), "days", MAX_DAYS):
        if isinstance(raw, str):
            raw = {"date": raw}
        if not isinstance(raw, dict):
            raise BlockError("each day must be an object with a date")
        date = str(raw.get("date") or raw.get("id") or "")
        if not _DATE.match(date):
            raise BlockError("each day needs a date like 2026-10-22")
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise BlockError(f"'{date}' is not a real date") from None
        day: dict[str, Any] = {"id": date, "label": _text(raw.get("label"), "label", 30, required=False) or date}
        if raw.get("disabled") or raw.get("closed"):
            day["disabled"] = True
        days.append(day)
    slots = []
    for i, raw in enumerate(_list(p.get("slots"), "slots", MAX_SLOTS)):
        if isinstance(raw, str):
            raw = {"label": raw}
        if not isinstance(raw, dict):
            raise BlockError("each slot must be an object with a label")
        slot: dict[str, Any] = {
            "id": _item_id(raw.get("id") or raw.get("time"), i),
            "label": _text(raw.get("label") or raw.get("time"), "label", 20),
        }
        sub = _text(raw.get("sub") or raw.get("note"), "sub", 40, required=False)
        if sub:
            slot["sub"] = sub
        if raw.get("disabled") or raw.get("full"):
            slot["disabled"] = True
        slots.append(slot)
    return {"days": days, "slots": slots}


def _counter(p: dict[str, Any]) -> dict[str, Any]:
    try:
        lo, hi = int(p.get("min", 1)), int(p.get("max", 8))
        default = int(p.get("default", lo))
    except (TypeError, ValueError):
        raise BlockError("min, max and default must be whole numbers") from None
    if not (0 <= lo <= hi <= MAX_COUNTER):
        raise BlockError(f"counter needs 0 <= min <= max <= {MAX_COUNTER}")
    if not lo <= default <= hi:
        raise BlockError("default must be between min and max")
    return {"label": _text(p.get("label"), "label", MAX_LABEL), "min": lo, "max": hi, "default": default}


def _rating(p: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"prompt": _text(p.get("prompt"), "prompt", 120)}
    for key in ("reasons_good", "reasons_bad"):
        raw = p.get(key)
        out[key] = [_text(r, key, 40) for r in _list(raw, key, MAX_REASONS, minimum=0)] if raw is not None else []
    return out


def _steps(p: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for raw in _list(p.get("steps"), "steps", MAX_STEPS):
        if not isinstance(raw, dict):
            raise BlockError("each step must be an object")
        state = str(raw.get("state") or "todo").lower()
        if state not in ("done", "now", "todo"):
            raise BlockError("step state must be done, now or todo")
        step = {"label": _text(raw.get("label"), "label", MAX_LABEL), "state": state}
        when = _text(raw.get("time"), "time", 20, required=False)
        if when:
            step["time"] = when
        steps.append(step)
    out: dict[str, Any] = {"steps": steps}
    eta = _text(p.get("eta"), "eta", 30, required=False)
    if eta:
        out["eta"] = eta
    return out


def _handoff(p: dict[str, Any]) -> dict[str, Any]:
    state = str(p.get("state") or "joined").lower()
    if state not in ("queued", "joining", "joined"):
        raise BlockError("handoff state must be queued, joining or joined")
    return {"agent_name": _text(p.get("agent_name"), "agent_name", 60), "state": state}


_SANITIZERS = {
    "options": _options,
    "cards": _cards,
    "form": _form,
    "datetime": _datetime,
    "counter": _counter,
    "rating": _rating,
    "steps": _steps,
    "handoff": _handoff,
}


def fallback_for(block: dict[str, Any]) -> str:
    """The plain-text version of a block (docs/27 §5: inbox, transcripts and exports show this)."""
    t = block.get("type")
    if t == "options":
        return "Options: " + " / ".join(i["label"] for i in block["items"])
    if t == "cards":
        return "Items: " + "; ".join(
            c["title"] + (f" ({c['price']})" if c.get("price") else "") for c in block["items"]
        )
    if t == "form":
        return "Details requested: " + ", ".join(f["label"] for f in block["fields"])
    if t == "datetime":
        return (
            "Pick a day and time. Days: "
            + ", ".join(d["label"] for d in block["days"])
            + ". Times: "
            + ", ".join(s["label"] for s in block["slots"])
        )
    if t == "counter":
        return f"{block['label']} ({block['min']}-{block['max']})"
    if t == "rating":
        return f"{block['prompt']} (1-5)"
    if t == "steps":
        marks = {"done": "done", "now": "in progress", "todo": "to do"}
        return "Progress: " + "; ".join(f"{s['label']} ({marks[s['state']]})" for s in block["steps"])
    if t == "handoff":
        return f"{block['agent_name']} {'has joined' if block['state'] == 'joined' else 'is joining'} the chat."
    return ""


def build_block(block_type: str, payload: Any) -> dict[str, Any]:
    """Validate a proposed block and return the clean one the widget will see. Raises `BlockError`."""
    if block_type not in _SANITIZERS:
        raise BlockError(f"unknown block type '{block_type}'")
    if not isinstance(payload, dict):
        raise BlockError("the block payload must be an object")
    body = _SANITIZERS[block_type](payload)
    block = {"id": uuid.uuid4().hex[:12], "type": block_type, **body}
    block["fallback_text"] = fallback_for(block)[:600]
    if len(json.dumps(block)) > MAX_BLOCK_BYTES:
        raise BlockError("the block is too large")
    return block


def sanitize_stored(blocks: Any) -> list[dict[str, Any]]:
    """Defence in depth for blocks read back from the database: anything that no longer validates is
    dropped (its fallback text is already in the message content)."""
    if not isinstance(blocks, list):
        return []
    clean: list[dict[str, Any]] = []
    for raw in blocks[:MAX_BLOCKS_PER_TURN]:
        if not isinstance(raw, dict) or raw.get("type") not in _SANITIZERS:
            continue
        try:
            block = build_block(raw["type"], raw)
        except BlockError:
            continue
        block["id"] = str(raw.get("id") or block["id"])[:64]
        if isinstance(raw.get("resolved"), dict):
            block["resolved"] = raw["resolved"]
        clean.append(block)
    return clean


# ── Text views ──────────────────────────────────────────────────────────────────────


def fallback_suffix(blocks: list[dict[str, Any]]) -> str:
    """What is appended to the stored message `content`. Deterministic, so the widget history can strip it."""
    return "".join("\n\n" + b["fallback_text"] for b in blocks if b.get("fallback_text"))


def strip_fallback(content: str | None, blocks: list[dict[str, Any]]) -> str:
    text = content or ""
    suffix = fallback_suffix(blocks)
    return text[: -len(suffix)] if suffix and text.endswith(suffix) else text


def visible_text(blocks: list[dict[str, Any]]) -> str:
    """Every string a visitor would read in these blocks, for the output guard."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for key, val in node.items():
                # fallback_text is derived from the other strings; counting it would dilute the leak score.
                if key not in ("id", "type", "image_url", "image_media_id", "resolved", "fallback_text"):
                    walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)

    for b in blocks:
        walk(b)
    return "\n".join(parts)


def passes_output_guard(
    blocks: list[dict[str, Any]],
    messages: Iterable[Message],
    protected_prompt: str | None,
    pii_allowlist: set[str] | None,
) -> bool:
    """False if the L5 output guard would have changed any text in these blocks.

    Block text comes from the model and often from the knowledge base, so it gets the same prompt-leak,
    persona and PII-egress check as the reply. Conservative on purpose: the guard rewrites a *whole* text,
    and there is no meaningful partial version of an option list, so a hit drops the blocks.
    """
    if not blocks:
        return True
    if not settings.guard_output_enabled:
        return True
    customer_supplied = (
        pii.customer_supplied_contacts(m.content for m in messages if m.role == "user")
        if pii_allowlist is not None and settings.guard_pii_egress_enabled
        else None
    )
    verdict = output_guard.apply(
        visible_text(blocks),
        protected_prompt,
        leak_threshold=settings.guard_output_leak_threshold,
        pii_allowlist=pii_allowlist if settings.guard_pii_egress_enabled else None,
        pii_regions=[r.strip() for r in settings.guard_pii_phone_regions.split(",") if r.strip()],
        redact_addresses=settings.guard_pii_redact_addresses,
        customer_supplied=customer_supplied,
    )
    return not verdict.changed


# ── The visitor's reply ─────────────────────────────────────────────────────────────


def clean_reply_value(value: Any, *, _depth: int = 0) -> Any:
    """A block reply's `value` is visitor input that gets stored: keep JSON primitives, cap everything."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:500]
    if _depth >= 2:
        return None
    if isinstance(value, list):
        return [clean_reply_value(v, _depth=_depth + 1) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k)[:64]: clean_reply_value(v, _depth=_depth + 1) for k, v in list(value.items())[:10]}
    return None


async def resolve_block(session: AsyncSession, conversation_id: uuid.UUID, block_id: str, value: Any) -> bool:
    """Mark a block answered, so it renders read-only (with the chosen value) after a reload.

    Looks only inside this conversation, so a forged id from another thread resolves nothing, and only
    ever resolves a block once. A miss is not an error: the reply still goes through as a normal message.
    """
    rows = (
        (
            await session.execute(
                select(DbMessage)
                .where(DbMessage.conversation_id == conversation_id, DbMessage.blocks.is_not(None))
                .order_by(DbMessage.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    for msg in rows:
        blocks = list(msg.blocks or [])
        for i, b in enumerate(blocks):
            if isinstance(b, dict) and b.get("id") == block_id and not b.get("resolved"):
                # A form's values are contact details the visitor typed. They already live in the user message,
                # which is the one place the normal pipeline (retention, deletion, redaction) knows about; a
                # second copy here would be storage that pipeline does not reach. Only the fact is kept.
                kept = None if b.get("type") == "form" else clean_reply_value(value)
                blocks[i] = {**b, "resolved": {"value": kept, "at": dt.datetime.now(tz=dt.UTC).isoformat()}}
                msg.blocks = blocks  # reassign: JSONB change tracking needs a new object
                await session.flush()
                return True
    return False


# ── Tools the model calls ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class BlockTool:
    name: str
    block_type: str
    description: str
    parameters: dict[str, Any]


def _text_schema(maxlen: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": maxlen}


BLOCK_TOOLS: dict[str, BlockTool] = {
    "present_options": BlockTool(
        name="present_options",
        block_type="options",
        description=(
            "Show the visitor 2-8 tappable choices instead of listing them in text. Use it for a short, "
            "closed question (which size, what time of day, which topic)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": MAX_OPTIONS,
                    "items": {
                        "type": "object",
                        "properties": {"label": _text_schema(MAX_LABEL), "sub": _text_schema(120)},
                        "required": ["label"],
                    },
                },
                "multi": {"type": "boolean", "description": "allow picking several"},
            },
            "required": ["items"],
        },
    ),
    "present_cards": BlockTool(
        name="present_cards",
        block_type="cards",
        description=(
            "Show up to 6 products from this business's catalog as cards with photo, price and a button. "
            "Pass product ids (from search_products) or a short search query."
        ),
        parameters={
            "type": "object",
            "properties": {
                "product_ids": {"type": "array", "maxItems": MAX_CARDS, "items": {"type": "string"}},
                "query": {"type": "string", "description": "used when no ids are given"},
            },
            "required": [],
        },
    ),
    "request_details": BlockTool(
        name="request_details",
        block_type="form",
        description=(
            "Show a small form (max 5 fields: text, email or phone) to collect contact or booking details. "
            "Never ask for passwords, card numbers, ID numbers or one-time codes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "maxItems": MAX_FIELDS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": _text_schema(MAX_LABEL),
                            "type": {"type": "string", "enum": list(FORM_FIELD_TYPES)},
                            "required": {"type": "boolean"},
                        },
                        "required": ["label"],
                    },
                },
                "submit_label": _text_schema(30),
            },
            "required": ["fields"],
        },
    ),
    "pick_datetime": BlockTool(
        name="pick_datetime",
        block_type="datetime",
        description=(
            "Show a day and time-slot picker. Only offer days and slots you got from a tool or the "
            "business's own information; mark unavailable ones as closed/full."
        ),
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "array",
                    "maxItems": MAX_DAYS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "label": _text_schema(30),
                            "closed": {"type": "boolean"},
                        },
                        "required": ["date"],
                    },
                },
                "slots": {
                    "type": "array",
                    "maxItems": MAX_SLOTS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": _text_schema(20),
                            "note": _text_schema(40),
                            "full": {"type": "boolean"},
                        },
                        "required": ["time"],
                    },
                },
            },
            "required": ["days", "slots"],
        },
    ),
    "ask_rating": BlockTool(
        name="ask_rating",
        block_type="rating",
        description="Ask the visitor to rate the conversation 1-5, with optional reason chips for good/bad scores.",
        parameters={
            "type": "object",
            "properties": {
                "prompt": _text_schema(120),
                "reasons_good": {"type": "array", "maxItems": MAX_REASONS, "items": _text_schema(40)},
                "reasons_bad": {"type": "array", "maxItems": MAX_REASONS, "items": _text_schema(40)},
            },
            "required": ["prompt"],
        },
    ),
}


#: Appended to the system prompt only for an agent that opted in. Prompt text is guidance, not enforcement:
#: every limit it mentions is also enforced in code (`build_block`, the output guard, the per-turn cap).
BLOCKS_INSTRUCTION = (
    "The chat window can show tappable controls. When the visitor must choose between a few options, pick a "
    "day and time, or give contact details, call the matching tool (present_options, present_cards, "
    "pick_datetime, request_details, ask_rating) instead of typing the choices out. Keep your own text to one "
    "short sentence when you do, show at most one control per reply, and then wait for the visitor's answer, "
    "which arrives as a normal message. Never use a control to ask for passwords, card numbers, ID numbers "
    "or one-time codes. If a tool reports an error, just ask in plain text."
)


def tools_for(flags: dict[str, bool] | None) -> dict[str, BlockTool]:
    """The block tools to offer: none unless the master switch is on, then one per enabled type."""
    if not flags or not flags.get("enabled"):
        return {}
    return {name: t for name, t in BLOCK_TOOLS.items() if flags.get(t.block_type, True)}


def refresh_media_urls(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-sign catalog card images on read (the persisted signed URL has expired by the next day)."""
    from app.media import urls

    for b in blocks:
        if b.get("type") != "cards":
            continue
        for card in b.get("items", []):
            media_id = card.get("image_media_id")
            if media_id:
                try:
                    card["image_url"] = urls.public_url(uuid.UUID(str(media_id)))
                except ValueError:
                    card.pop("image_url", None)
    return blocks


def public_view(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What leaves the server: validated, URLs re-signed, internal ids removed."""
    out = refresh_media_urls(sanitize_stored(blocks))
    for b in out:
        for card in b.get("items", []) if b.get("type") == "cards" else []:
            card.pop("image_media_id", None)
    return out
