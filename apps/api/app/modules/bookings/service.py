"""Appointment booking behind the agent's n8n tools (ADR-120).

n8n is the orchestration layer (signature check, input check, optional calendar mirror); Vicero owns the data.
Every call arrives with the tool-run id and the per-run token Vicero issued when it called the workflow, so a
workflow can only act inside the organisation, agent and conversation of the run it is serving.

* `book`            idempotent (tool-run id, and the same customer + start time), conflict-checked, rate-limited per
                    contact, and sends the confirmation on the channel the customer used.
* `free_slots`      the day's open slots (business hours minus existing bookings).
* `get_customer`    only the person in the current conversation (never someone else's data).
* calendar         when the organisation has connected Google Calendar (ADR-121), Vicero itself creates the event
                    after saving the booking; a failure there never affects the booking.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import ChannelAuthRejected
from app.channels.reconnect import flag_token_rejected
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import limiter
from app.integrations.n8n_client import valid_callback_token
from app.models import Booking, Channel, Contact, Conversation, CrmContact, Message, Tool, ToolRun
from app.modules.bookings import schemas
from app.modules.integrations import google_calendar

log = get_logger("bookings")

SLOT_MINUTES = 30
#: Conversation channels a message can be pushed to (the widget and playground answer in the reply instead).
PUSH_CHANNELS = ("telegram", "whatsapp", "instagram", "facebook", "slack", "discord")
#: A run's token is good for ten minutes, like the async callbacks (ADR-110).
RUN_TOKEN_WINDOW = dt.timedelta(minutes=10)
MAX_DAYS_AHEAD = 180
MIN_LEAD = dt.timedelta(minutes=5)
BOOKINGS_PER_CONTACT_PER_HOUR = 5
LOOKUPS_PER_CONTACT_PER_HOUR = 30

#: First and last allowed start, minutes after midnight, by weekday (Mon=0). Sunday is closed.
#: Matches docs/demo-knowledge/05-hours-and-booking.md; a per-agent override is a later step.
BUSINESS_HOURS: dict[int, tuple[int, int]] = {
    0: (10 * 60, 17 * 60 + 30),
    1: (10 * 60, 17 * 60 + 30),
    2: (10 * 60, 17 * 60 + 30),
    3: (10 * 60, 17 * 60 + 30),
    4: (10 * 60, 17 * 60 + 30),
    5: (10 * 60, 13 * 60 + 30),
}


def tz() -> ZoneInfo:
    return ZoneInfo(settings.booking_timezone)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


@dataclass(slots=True)
class RunContext:
    org_id: uuid.UUID
    agent_id: uuid.UUID
    run_id: uuid.UUID
    conversation: Conversation | None


async def resolve_run(session: AsyncSession, run_id: uuid.UUID, token: str) -> RunContext:
    """The tool run this call belongs to, or a 401/404. Never says which part was wrong."""
    run = await session.get(ToolRun, run_id)
    if run is None or not valid_callback_token(token, run.id, run.organization_id):
        raise AppError("bookings.bad_run", "Unknown or invalid run.", 401)
    if _now() - run.created_at > RUN_TOKEN_WINDOW:
        raise AppError("bookings.run_expired", "This run has expired.", 401)
    tool = await session.get(Tool, run.tool_id)
    if tool is None or tool.agent_id is None or tool.organization_id != run.organization_id:
        raise AppError("bookings.bad_run", "Unknown or invalid run.", 401)
    conversation = await session.get(Conversation, run.conversation_id) if run.conversation_id else None
    return RunContext(run.organization_id, tool.agent_id, run.id, conversation)


# ── slots ─────────────────────────────────────────────────────────────────────────────


def day_slots(day: dt.date) -> list[dt.datetime]:
    """Every allowed start on `day` (local business time), as aware datetimes."""
    hours = BUSINESS_HOURS.get(day.weekday())
    if hours is None:
        return []
    first, last = hours
    zone = tz()
    return [
        dt.datetime.combine(day, dt.time(m // 60, m % 60), tzinfo=zone) for m in range(first, last + 1, SLOT_MINUTES)
    ]


async def _taken(
    session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID, start: dt.datetime, end: dt.datetime
) -> set[dt.datetime]:
    rows = (
        await session.execute(
            select(Booking.start_at).where(
                Booking.organization_id == org_id,
                Booking.agent_id == agent_id,
                Booking.status == "confirmed",
                Booking.start_at >= start - dt.timedelta(minutes=SLOT_MINUTES),
                Booking.start_at < end,
            )
        )
    ).scalars()
    return {r.astimezone(dt.UTC) for r in rows}


async def free_slots(session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID, day: dt.date) -> list[dt.datetime]:
    slots = day_slots(day)
    if not slots:
        return []
    taken = await _taken(session, org_id, agent_id, slots[0], slots[-1] + dt.timedelta(minutes=SLOT_MINUTES))
    earliest = _now() + MIN_LEAD
    return [s for s in slots if s.astimezone(dt.UTC) not in taken and s.astimezone(dt.UTC) >= earliest]


def _label(moment: dt.datetime) -> str:
    local = moment.astimezone(tz())
    return f"{local:%a %d %b %Y, %I:%M %p} ({settings.booking_timezone})"


def _slot_out(moment: dt.datetime) -> dict[str, str]:
    local = moment.astimezone(tz())
    return {"start_iso": local.isoformat(timespec="minutes"), "label": f"{local:%I:%M %p}".lstrip("0")}


# ── tools ─────────────────────────────────────────────────────────────────────────────


def invalid(exc: ValidationError) -> dict[str, Any]:
    """A model-readable validation failure: field names and the rule, never the submitted values."""
    fields = {".".join(str(p) for p in e["loc"]): str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()}
    return {"ok": False, "error": "invalid_input", "fields": fields}


async def availability(session: AsyncSession, run: RunContext, args: schemas.AvailabilityArgs) -> dict[str, Any]:
    allowed, _ = await _hit(run, "lookup", LOOKUPS_PER_CONTACT_PER_HOUR)
    if not allowed:
        return {"ok": False, "error": "rate_limited", "message": "Too many requests; try again later."}
    day = dt.date.fromisoformat(args.date)
    today = dt.datetime.now(tz()).date()
    if day < today or day > today + dt.timedelta(days=MAX_DAYS_AHEAD):
        return {
            "ok": True,
            "date": args.date,
            "timezone": settings.booking_timezone,
            "slots": [],
            "note": "date out of range",
        }
    slots = await free_slots(session, run.org_id, run.agent_id, day)
    return {
        "ok": True,
        "date": args.date,
        "timezone": settings.booking_timezone,
        "slots": [_slot_out(s) for s in slots],
        "note": None if slots else "closed or fully booked that day",
    }


async def _hit(run: RunContext, kind: str, limit: int) -> tuple[bool, int]:
    """Per-contact budget: the conversation's contact, else the conversation, else the run's org."""
    conv = run.conversation
    who = (conv.contact_id or conv.id) if conv is not None else run.org_id
    return await limiter.hit(f"booking:{kind}:{run.org_id}:{who}", limit, 3600)


def _parse_start(raw: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError("start_iso must be an ISO 8601 date and time, e.g. 2026-10-20T15:00:00+05:30") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz())
    return parsed.astimezone(tz())


async def book(session: AsyncSession, run: RunContext, args: schemas.BookArgs) -> dict[str, Any]:
    allowed, _ = await _hit(run, "book", BOOKINGS_PER_CONTACT_PER_HOUR)
    if not allowed:
        return {"ok": False, "error": "rate_limited", "message": "Too many booking attempts; a team member will help."}

    try:
        start = _parse_start(args.start_iso)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_input", "fields": {"start_iso": str(exc)}}
    problem = _check_start(start)
    if problem:
        return {"ok": False, "error": "invalid_time", "message": problem}

    key = (args.idempotency_key or str(run.run_id)).strip()[:128]
    existing = await _existing(session, run.org_id, key, run.agent_id, args.contact, start)
    if existing is not None:
        if existing.external_event_id is None:
            # An earlier attempt may have saved the booking but not reached Google: try the calendar again.
            await google_calendar.mirror_booking(session, existing)
        return _booking_out(existing, duplicate=True)

    if start.astimezone(dt.UTC) in await _taken(
        session, run.org_id, run.agent_id, start, start + dt.timedelta(minutes=SLOT_MINUTES)
    ):
        nearby = (await free_slots(session, run.org_id, run.agent_id, start.date()))[:4]
        return {
            "ok": False,
            "error": "slot_taken",
            "message": "That time is already booked.",
            "alternatives": [_slot_out(s) for s in nearby],
        }

    conv = run.conversation
    booking = Booking(
        organization_id=run.org_id,
        agent_id=run.agent_id,
        conversation_id=conv.id if conv else None,
        contact_id=conv.contact_id if conv else None,
        channel=conv.channel if conv else None,
        customer_name=args.name,
        customer_contact=args.contact,
        start_at=start,
        duration_minutes=SLOT_MINUTES,
        notes=args.notes,
        idempotency_key=key,
    )
    session.add(booking)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        # Lost a race against the same retried call: hand back the winner.
        winner = await _existing(session, run.org_id, key, run.agent_id, args.contact, start)
        if winner is None:
            raise
        return _booking_out(winner, duplicate=True)

    booking.confirmation_sent = await _confirm(session, booking, conv)
    await google_calendar.mirror_booking(session, booking)  # never raises; the booking is already saved
    log.info(
        "booking_created",
        booking_id=str(booking.id),
        org=str(run.org_id),
        confirmed_on_channel=booking.confirmation_sent,
    )
    return _booking_out(booking, duplicate=False)


def _check_start(start: dt.datetime) -> str | None:
    now = _now()
    if start.astimezone(dt.UTC) < now + MIN_LEAD:
        return "That time has already passed."
    if start.astimezone(dt.UTC) > now + dt.timedelta(days=MAX_DAYS_AHEAD):
        return f"Bookings open up to {MAX_DAYS_AHEAD} days ahead."
    hours = BUSINESS_HOURS.get(start.weekday())
    minutes = start.hour * 60 + start.minute
    if hours is None:
        return "We are closed that day."
    if start.second or start.microsecond or minutes % SLOT_MINUTES:
        return f"Appointments start on the hour or half hour ({SLOT_MINUTES}-minute slots)."
    if not hours[0] <= minutes <= hours[1]:
        return "That time is outside our opening hours."
    return None


async def _existing(
    session: AsyncSession, org_id: uuid.UUID, key: str, agent_id: uuid.UUID, contact: str, start: dt.datetime
) -> Booking | None:
    by_key = (
        await session.execute(select(Booking).where(Booking.organization_id == org_id, Booking.idempotency_key == key))
    ).scalar_one_or_none()
    if by_key is not None:
        return by_key
    # The model asking twice in two different runs for the same person and time is the same booking.
    return (
        await session.execute(
            select(Booking).where(
                Booking.organization_id == org_id,
                Booking.agent_id == agent_id,
                Booking.customer_contact == contact,
                Booking.start_at == start,
                Booking.status == "confirmed",
            )
        )
    ).scalar_one_or_none()


def _booking_out(booking: Booking, *, duplicate: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "booking_id": str(booking.id),
        "reference": str(booking.id).split("-")[0].upper(),
        "status": booking.status,
        "start_iso": booking.start_at.astimezone(tz()).isoformat(timespec="minutes"),
        "when": _label(booking.start_at),
        "duration_minutes": booking.duration_minutes,
        "duplicate": duplicate,
        "confirmation_sent": booking.confirmation_sent,
        "calendar_event": booking.external_event_id is not None,
        "confirmation": (
            "A confirmation message was already sent to the customer on this channel. Do not repeat the details."
            if booking.confirmation_sent
            else "No separate confirmation was sent on this channel: confirm the details in your reply."
        ),
    }


async def _confirm(session: AsyncSession, booking: Booking, conv: Conversation | None) -> bool:
    """Send the confirmation on the channel the customer used. False for the widget/playground (the reply is the
    confirmation there) or when the channel refuses (e.g. WhatsApp's 24 h window); the booking stands either way."""
    if conv is None:
        return False
    # Local import: the inbox service imports the channel adapters, which this module should not pull in at import time.
    from app.channels import get_channel

    if conv.channel not in PUSH_CHANNELS or not conv.channel_user_id:
        return False
    channel = (
        await session.execute(
            select(Channel)
            .where(
                Channel.organization_id == conv.organization_id,
                Channel.agent_id == conv.agent_id,
                Channel.type == conv.channel,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    adapter = get_channel(conv.channel)
    if channel is None or adapter is None:
        return False
    text = (
        f"Booked! {_label(booking.start_at)} for {booking.customer_name}. "
        f"Your reference is {str(booking.id).split('-')[0].upper()}. Reply here if you need to change it."
    )
    try:
        await adapter.send(channel, conv.channel_user_id, text)
    except ChannelAuthRejected:
        await flag_token_rejected(session, channel)
        log.warning("booking_confirmation_failed", booking_id=str(booking.id), error="ChannelAuthRejected")
        return False
    except Exception as exc:
        log.warning("booking_confirmation_failed", booking_id=str(booking.id), error=type(exc).__name__)
        return False
    session.add(
        Message(
            conversation_id=conv.id,
            organization_id=conv.organization_id,
            role="assistant",
            content=text,
            provider="booking",
        )
    )
    return True


# ── customer lookup ───────────────────────────────────────────────────────────────────


def _identifiers(contact: Contact | None, crm: CrmContact | None, bookings: list[Booking]) -> set[str]:
    found: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value.strip():
            found.add(value.strip().lower())
            try:
                found.add(schemas.normalize_contact(value))
            except ValueError:
                pass

    if contact is not None:
        add(contact.external_id)
        add(contact.extra.get("phone"))
        add(contact.extra.get("email"))
    if crm is not None:
        add(crm.email)
        add(crm.phone)
    for b in bookings:
        add(b.customer_contact)
    return found


async def get_customer(session: AsyncSession, run: RunContext, args: schemas.CustomerArgs) -> dict[str, Any]:
    """The current conversation's customer, only. Asking about anyone else answers `found: false`."""
    allowed, _ = await _hit(run, "lookup", LOOKUPS_PER_CONTACT_PER_HOUR)
    if not allowed:
        return {"ok": False, "error": "rate_limited", "message": "Too many requests; try again later."}

    conv = run.conversation
    contact = await session.get(Contact, conv.contact_id) if conv and conv.contact_id else None
    crm = await session.get(CrmContact, contact.crm_contact_id) if contact and contact.crm_contact_id else None
    stmt = select(Booking).where(Booking.organization_id == run.org_id, Booking.agent_id == run.agent_id)
    if contact is not None:
        stmt = stmt.where(Booking.contact_id == contact.id)
    elif conv is not None:
        stmt = stmt.where(Booking.conversation_id == conv.id)
    else:
        return {"ok": True, "found": False, "reason": "no conversation to look up"}
    bookings = list((await session.execute(stmt.order_by(Booking.start_at.desc()).limit(20))).scalars())

    asked = {args.contact.strip().lower()}
    try:
        asked.add(schemas.normalize_contact(args.contact))
    except ValueError:
        pass
    if not asked & _identifiers(contact, crm, bookings):
        return {"ok": True, "found": False, "reason": "I can only look up the person I am chatting with."}

    now = _now()
    past_or_now = [b for b in bookings if b.status == "confirmed"]
    last = past_or_now[0] if past_or_now else None
    upcoming = [b for b in past_or_now if b.start_at >= now]
    name = (
        (crm.display_name if crm else None)
        or (contact.display_name if contact else None)
        or (bookings[0].customer_name if bookings else None)
    )
    return {
        "ok": True,
        "found": True,
        "name": name,
        "channel": conv.channel if conv else None,
        "last_booking": _short(last) if last else None,
        "next_booking": _short(min(upcoming, key=lambda b: b.start_at)) if upcoming else None,
        "order_status": crm.order_status if crm else None,
        # What the customer themselves wrote on their bookings. Staff notes on the CRM record stay internal.
        "notes": [b.notes for b in bookings if b.notes][:3],
    }


def _short(b: Booking) -> dict[str, Any]:
    return {"booking_id": str(b.id), "when": _label(b.start_at), "status": b.status, "notes": b.notes}
