"""Booking tools (ADR-120): typed schemas, the signed internal API n8n calls, idempotency, privacy, confirmation.

The calendar is mocked: the n8n side (a Google Calendar node that is off by default) is simulated by a function that
returns an event id and then calls `attach-event`, exactly as the workflow does.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel
from app.core.config import settings
from app.integrations.n8n_client import callback_token
from app.models import Booking, Channel, Contact, Conversation, CrmContact, Message, Tool, ToolRun
from app.modules.bookings import schemas, service
from tests.test_channels import _fake_agent, _headers

SECRET = "automation-report-secret-for-tests"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "automation_report_secret", SECRET)
    monkeypatch.setattr(settings, "booking_timezone", "Asia/Kolkata")


@pytest.fixture(autouse=True)
def _reset_transport() -> Any:
    yield
    for t in ("telegram", "whatsapp"):
        ch = get_channel(t)
        if ch:
            ch.transport = None


def _next(weekday: int, hour: int = 15, minute: int = 0, weeks: int = 1) -> dt.datetime:
    """A future local time on `weekday` (Mon=0), at least a week ahead so it is never in the past or too close."""
    today = dt.datetime.now(service.tz()).date()
    ahead = (weekday - today.weekday()) % 7 + 7 * weeks
    return dt.datetime.combine(today + dt.timedelta(days=ahead), dt.time(hour, minute), tzinfo=service.tz())


def _signed(path: str, payload: dict[str, Any], secret: str = SECRET, ts: int | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    stamp = str(ts if ts is not None else int(time.time()))
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "url": f"/internal/tools/{path}",
        "content": body,
        "headers": {"X-Vicero-Timestamp": stamp, "X-Vicero-Signature": sig, "Content-Type": "application/json"},
    }


class World:
    """One org with an agent, a customer conversation and a tool run to call from."""

    def __init__(self) -> None:
        self.org_id: uuid.UUID
        self.agent_id: uuid.UUID
        self.conv: Conversation
        self.contact: Contact
        self.tool: Tool
        self.db: AsyncSession
        self.client: AsyncClient

    async def run(self, *, age: dt.timedelta = dt.timedelta(0)) -> ToolRun:
        run = ToolRun(
            organization_id=self.org_id,
            tool_id=self.tool.id,
            conversation_id=self.conv.id,
            input={},
            status="pending",
            created_at=dt.datetime.now(tz=dt.UTC) - age,
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def call(
        self, path: str, args: dict[str, Any] | None = None, *, run: ToolRun | None = None, **extra: Any
    ) -> httpx.Response:
        run = run or await self.run()
        payload: dict[str, Any] = {"run_id": str(run.id), "callback_token": callback_token(run.id, run.organization_id)}
        if args is not None:
            payload["args"] = args
        payload.update(extra)
        return await self.client.post(**_signed(path, payload))

    async def bookings(self) -> list[Booking]:
        return list((await self.db.execute(select(Booking).where(Booking.organization_id == self.org_id))).scalars())


async def _world(
    client: AsyncClient, db: AsyncSession, email: str, *, channel: str = "widget", external_id: str = "visitor-1"
) -> World:
    w = World()
    headers = await _headers(client, email)
    w.client, w.db = client, db
    w.org_id = uuid.UUID(headers["X-Org-Id"])
    w.agent_id = uuid.UUID(await _fake_agent(client, headers))
    w.contact = Contact(
        organization_id=w.org_id,
        channel=channel,
        external_id=external_id,
        display_name="Asha",
        extra={"phone": "+919999900001"},
    )
    db.add(w.contact)
    await db.flush()
    w.conv = Conversation(
        organization_id=w.org_id,
        agent_id=w.agent_id,
        channel=channel,
        channel_user_id=external_id if channel != "widget" else None,
        contact_id=w.contact.id,
    )
    w.tool = Tool(
        organization_id=w.org_id,
        agent_id=w.agent_id,
        name=f"book_{uuid.uuid4().hex[:6]}",
        type="n8n",
        enabled=True,
        config={"webhook_url": "http://n8n/webhook/demo-book-appointment", "mode": "sync"},
        input_schema=schemas.TOOL_SPECS[0]["input_schema"],
    )
    db.add_all([w.conv, w.tool])
    await db.flush()
    return w


BOOK = {"name": "Asha K", "contact": "asha@example.com", "notes": "demo of the assistant"}


def _book_args(start: dt.datetime, **over: Any) -> dict[str, Any]:
    return {**BOOK, "start_iso": start.isoformat(), **over}


# ── the schemas the model sees ───────────────────────────────────────────────────────


def test_the_three_tools_have_strict_typed_schemas() -> None:
    specs = {s["name"]: s for s in schemas.TOOL_SPECS}
    assert set(specs) == {"book_appointment", "check_availability", "get_customer"}
    for spec in specs.values():
        schema = spec["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert all(p["type"] == "string" for p in schema["properties"].values())
        assert spec["description"] and spec["slug"]
    assert specs["book_appointment"]["input_schema"]["required"] == ["name", "contact", "start_iso"]
    assert specs["check_availability"]["input_schema"]["required"] == ["date"]
    assert specs["get_customer"]["input_schema"]["required"] == ["contact"]
    assert "args" not in specs["book_appointment"]["input_schema"]["properties"]  # typed, not a free-form blob


def test_book_args_validate_and_normalise() -> None:
    ok = schemas.BookArgs.model_validate(
        {
            "name": "  Asha   K ",
            "contact": "Asha@Example.COM",
            "start_iso": "2026-10-20T15:00:00+05:30",
            "notes": " hi ",
        }
    )
    assert (ok.name, ok.contact, ok.notes) == ("Asha K", "asha@example.com", "hi")
    assert (
        schemas.BookArgs.model_validate({**BOOK, "contact": "+91 98765-43210", "start_iso": "x" * 12}).contact
        == "+919876543210"
    )
    for bad in (
        {"name": "", "contact": "a@b.co", "start_iso": "2026-10-20T15:00"},
        {"name": "x" * 121, "contact": "a@b.co", "start_iso": "2026-10-20T15:00"},
        {"name": "A", "contact": "not-a-contact", "start_iso": "2026-10-20T15:00"},
        {"name": "A", "contact": "123", "start_iso": "2026-10-20T15:00"},
        {"name": "A", "contact": "a@b.co"},
        {"name": "\x00\x01", "contact": "a@b.co", "start_iso": "2026-10-20T15:00"},
    ):
        with pytest.raises(ValueError):
            schemas.BookArgs.model_validate(bad)
    assert schemas.AvailabilityArgs.model_validate({"date": "2026-10-20"}).date == "2026-10-20"
    for bad_date in ("2026-13-01", "20-10-2026", "2026-02-31", "tomorrow"):
        with pytest.raises(ValueError):
            schemas.AvailabilityArgs.model_validate({"date": bad_date})


# ── transport: signature and run binding ─────────────────────────────────────────────


async def test_unsigned_stale_and_forged_calls_are_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_auth@example.com")
    run = await w.run()
    payload = {
        "run_id": str(run.id),
        "callback_token": callback_token(run.id, w.org_id),
        "args": {"date": "2026-10-20"},
    }
    assert (await client.post("/internal/tools/availability", json=payload)).status_code == 401
    assert (await client.post(**_signed("availability", payload, secret="wrong"))).status_code == 401
    assert (await client.post(**_signed("availability", payload, ts=int(time.time()) - 3600))).status_code == 401


async def test_without_a_configured_secret_nothing_is_accepted(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await _world(client, db_session, "bk_nosecret@example.com")
    monkeypatch.setattr(settings, "automation_report_secret", None)
    assert (await w.call("availability", {"date": "2026-10-20"})).status_code == 503


async def test_the_run_token_is_bound_to_its_run_and_org_and_expires(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_token@example.com")
    other = await _world(client, db_session, "bk_token2@example.com")
    run = await w.run()
    foreign_token = callback_token(run.id, other.org_id)  # right run, wrong org
    bad = await client.post(
        **_signed(
            "availability", {"run_id": str(run.id), "callback_token": foreign_token, "args": {"date": "2026-10-20"}}
        )
    )
    assert bad.status_code == 401
    unknown = await client.post(
        **_signed(
            "availability", {"run_id": str(uuid.uuid4()), "callback_token": "0" * 64, "args": {"date": "2026-10-20"}}
        )
    )
    assert unknown.status_code == 401
    old = await w.run(age=dt.timedelta(minutes=11))
    assert (await w.call("availability", {"date": "2026-10-20"}, run=old)).status_code == 401
    assert (await w.call("availability", {"date": "2026-10-20"})).status_code == 200


async def test_a_run_that_is_not_an_agent_tool_cannot_use_the_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_orphan@example.com")
    w.tool.agent_id = None  # an org-level tool has no agent to book for
    await db_session.flush()
    assert (await w.call("availability", {"date": "2026-10-20"})).status_code == 401


# ── availability ─────────────────────────────────────────────────────────────────────


async def test_availability_follows_opening_hours_and_removes_booked_slots(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_avail@example.com")
    monday = _next(0)
    r = await w.call("availability", {"date": monday.date().isoformat()})
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["timezone"] == "Asia/Kolkata"
    assert len(body["slots"]) == 16 and body["slots"][0]["start_iso"].startswith(f"{monday.date()}T10:00")
    assert body["slots"][-1]["start_iso"].startswith(f"{monday.date()}T17:30")

    booked = await w.call("book", _book_args(monday.replace(hour=11, minute=30)))
    assert booked.json()["ok"], booked.text
    after = (await w.call("availability", {"date": monday.date().isoformat()})).json()
    assert len(after["slots"]) == 15 and not any("T11:30" in s["start_iso"] for s in after["slots"])

    saturday = (await w.call("availability", {"date": _next(5).date().isoformat()})).json()
    assert len(saturday["slots"]) == 8  # 10:00 to 13:30
    sunday = (await w.call("availability", {"date": _next(6).date().isoformat()})).json()
    assert sunday["slots"] == [] and "closed" in sunday["note"]
    past = (await w.call("availability", {"date": "2020-01-06"})).json()
    assert past["slots"] == []


async def test_availability_input_is_validated_without_an_http_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_avail2@example.com")
    r = await w.call("availability", {"date": "next tuesday"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["error"] == "invalid_input" and "date" in r.json()["fields"]


# ── booking ──────────────────────────────────────────────────────────────────────────


async def test_booking_creates_one_row_and_returns_an_id(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_book@example.com")
    start = _next(1, 14, 30)
    r = await w.call("book", _book_args(start))
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["duplicate"] is False and body["status"] == "confirmed"
    assert body["reference"] == body["booking_id"].split("-")[0].upper()
    rows = await w.bookings()
    assert len(rows) == 1
    b = rows[0]
    assert (b.customer_name, b.customer_contact, b.notes) == ("Asha K", "asha@example.com", "demo of the assistant")
    assert b.start_at == start and b.organization_id == w.org_id and b.agent_id == w.agent_id
    assert b.conversation_id == w.conv.id and b.contact_id == w.contact.id and b.channel == "widget"
    # The widget has no push channel: the model is told to confirm in its own reply.
    assert body["confirmation_sent"] is False and "confirm the details in your reply" in body["confirmation"]


async def test_a_retried_webhook_cannot_double_book(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_idem@example.com")
    run = await w.run()
    start = _next(2, 11, 0)
    first = (await w.call("book", _book_args(start), run=run)).json()
    again = (await w.call("book", _book_args(start), run=run)).json()  # same tool run: n8n retried
    assert first["ok"] and again["ok"] and again["duplicate"] is True
    assert again["booking_id"] == first["booking_id"]
    # A new run for the same person and time (the model asked twice) is the same appointment too.
    third = (await w.call("book", _book_args(start))).json()
    assert third["duplicate"] is True and third["booking_id"] == first["booking_id"]
    assert len(await w.bookings()) == 1


async def test_a_taken_slot_is_refused_with_alternatives(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_conflict@example.com")
    start = _next(3, 12, 0)
    assert (await w.call("book", _book_args(start))).json()["ok"]
    clash = (await w.call("book", _book_args(start, name="Ravi", contact="ravi@example.com"))).json()
    assert clash["ok"] is False and clash["error"] == "slot_taken"
    assert 1 <= len(clash["alternatives"]) <= 4 and all("T12:00" not in a["start_iso"] for a in clash["alternatives"])
    assert len(await w.bookings()) == 1


@pytest.mark.parametrize(
    ("make", "expect"),
    [
        (lambda: _next(6, 11, 0), "closed"),  # Sunday
        (lambda: _next(0, 9, 30), "opening hours"),
        (lambda: _next(0, 18, 0), "opening hours"),
        (lambda: _next(5, 14, 0), "opening hours"),  # Saturday afternoon
        (lambda: _next(0, 11, 15), "half hour"),
        (lambda: dt.datetime.now(service.tz()) - dt.timedelta(days=1), "passed"),
        (lambda: dt.datetime.now(service.tz()) + dt.timedelta(days=400), "days ahead"),
    ],
)
async def test_times_the_business_cannot_serve_are_refused(
    client: AsyncClient, db_session: AsyncSession, make: Any, expect: str
) -> None:
    w = await _world(client, db_session, f"bk_time{uuid.uuid4().hex[:6]}@example.com")
    r = (await w.call("book", _book_args(make()))).json()
    assert r["ok"] is False and r["error"] == "invalid_time" and expect in r["message"].lower()
    assert await w.bookings() == []


async def test_bad_input_is_reported_field_by_field_and_never_echoed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_bad@example.com")
    r = (await w.call("book", {"name": "A", "contact": "SECRET-NOT-A-CONTACT", "start_iso": "garbage"})).json()
    assert r["ok"] is False and r["error"] == "invalid_input" and "contact" in r["fields"]
    assert "SECRET-NOT-A-CONTACT" not in json.dumps(r)
    bad_time = (await w.call("book", _book_args(_next(1), start_iso="not-a-time"))).json()
    assert bad_time["error"] == "invalid_input" and "start_iso" in bad_time["fields"]


async def test_naive_times_are_read_in_the_business_timezone(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_naive@example.com")
    start = _next(1, 16, 0)
    r = (await w.call("book", _book_args(start, start_iso=start.replace(tzinfo=None).isoformat()))).json()
    assert r["ok"] and r["start_iso"].endswith("+05:30")
    assert (await w.bookings())[0].start_at == start


async def test_a_contact_cannot_flood_bookings(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_rate@example.com")
    results = []
    for i in range(service.BOOKINGS_PER_CONTACT_PER_HOUR + 1):
        slot = _next(4, 10 + (i // 2), 30 * (i % 2), weeks=2)
        results.append((await w.call("book", _book_args(slot, contact=f"p{i}@example.com"))).json())
    assert all(r["ok"] for r in results[:-1])
    assert results[-1]["ok"] is False and results[-1]["error"] == "rate_limited"
    assert len(await w.bookings()) == service.BOOKINGS_PER_CONTACT_PER_HOUR


# ── confirmation on the customer's channel ───────────────────────────────────────────


async def test_the_confirmation_goes_back_on_the_channel_the_customer_used(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_confirm@example.com", channel="telegram", external_id="778899")
    db_session.add(
        Channel(
            organization_id=w.org_id,
            agent_id=w.agent_id,
            type="telegram",
            enabled=True,
            config={"bot_token": "123456:TESTTOKEN"},
            webhook_secret="s",
        )
    )
    await db_session.flush()
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(200, json={"ok": True})

    get_channel("telegram").transport = httpx.MockTransport(handler)
    run = await w.run()
    start = _next(1, 10, 0)
    body = (await w.call("book", _book_args(start), run=run)).json()

    assert body["ok"] and body["confirmation_sent"] is True and "already sent" in body["confirmation"]
    assert len(sent) == 1 and str(sent[0]["body"]["chat_id"]) == "778899"
    assert body["reference"] in sent[0]["body"]["text"] and "Asha K" in sent[0]["body"]["text"]
    stored = (
        (
            await db_session.execute(
                select(Message).where(Message.conversation_id == w.conv.id, Message.provider == "booking")
            )
        )
        .scalars()
        .all()
    )
    assert len(stored) == 1

    # A retry of the same call must not message the customer a second time.
    again = (await w.call("book", _book_args(start), run=run)).json()
    assert again["duplicate"] is True and len(sent) == 1


async def test_a_failed_confirmation_does_not_undo_the_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_confirm2@example.com", channel="telegram", external_id="778800")
    db_session.add(
        Channel(
            organization_id=w.org_id,
            agent_id=w.agent_id,
            type="telegram",
            enabled=True,
            config={"bot_token": "1:x"},
            webhook_secret="s",
        )
    )
    await db_session.flush()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    get_channel("telegram").transport = httpx.MockTransport(boom)
    body = (await w.call("book", _book_args(_next(1, 10, 30)))).json()
    assert body["ok"] and body["confirmation_sent"] is False
    assert len(await w.bookings()) == 1


# ── customer lookup: only the person in this conversation ────────────────────────────


async def test_get_customer_returns_the_current_customer_and_nobody_else(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_cust@example.com")
    crm = CrmContact(
        organization_id=w.org_id,
        display_name="Asha Kumar",
        email="asha@example.com",
        phone="+919999900001",
        order_status="shipped",
        notes=[{"by": "staff", "text": "INTERNAL staff remark", "at": "x"}],
    )
    db_session.add(crm)
    await db_session.flush()
    w.contact.crm_contact_id = crm.id
    await db_session.flush()
    start = _next(1, 15, 0)
    await w.call("book", _book_args(start))

    for who in ("asha@example.com", "+91 99999 00001", "ASHA@example.com"):
        me = (await w.call("customer", {"contact": who})).json()
        assert me["ok"] and me["found"] is True, who
        assert me["name"] == "Asha Kumar" and me["order_status"] == "shipped"
        assert me["last_booking"]["status"] == "confirmed" and me["next_booking"]["booking_id"]
        assert me["notes"] == ["demo of the assistant"]
        assert "INTERNAL" not in json.dumps(me)  # staff notes stay internal

    someone_else = (await w.call("customer", {"contact": "someone.else@example.com"})).json()
    assert someone_else["ok"] and someone_else["found"] is False and "name" not in someone_else


async def test_other_customers_bookings_never_leak_across_contacts_or_orgs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    a = await _world(client, db_session, "bk_leak_a@example.com", external_id="visitor-a")
    b = await _world(client, db_session, "bk_leak_b@example.com", external_id="visitor-b")
    await a.call("book", _book_args(_next(1, 15, 0), name="Alice", contact="alice@example.com"))
    stranger = (await b.call("customer", {"contact": "alice@example.com"})).json()
    assert stranger["found"] is False
    # Same org, a different visitor on the same agent: still not visible.
    other_contact = Contact(organization_id=a.org_id, channel="widget", external_id="visitor-c", display_name="Mallory")
    db_session.add(other_contact)
    await db_session.flush()
    a.conv = Conversation(organization_id=a.org_id, agent_id=a.agent_id, channel="widget", contact_id=other_contact.id)
    db_session.add(a.conv)
    await db_session.flush()
    assert (await a.call("customer", {"contact": "alice@example.com"})).json()["found"] is False


async def test_bookings_table_counts(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(client, db_session, "bk_count@example.com")
    await w.call("book", _book_args(_next(1, 10, 0)))
    total = (
        await db_session.execute(select(func.count()).select_from(Booking).where(Booking.organization_id == w.org_id))
    ).scalar_one()
    assert total == 1


# ── "Saturday 11 am" (the model is now told today's date; the tools must serve the day it names) ─────────────


async def test_a_saturday_11_am_booking_is_stored_on_that_saturday_in_the_business_timezone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "bk_sat@example.com")
    saturday = _next(5, 11, 0)
    assert saturday.strftime("%A") == "Saturday" and saturday.utcoffset() == dt.timedelta(hours=5, minutes=30)

    offered = (await w.call("availability", {"date": saturday.date().isoformat()})).json()
    starts = [dt.datetime.fromisoformat(slot["start_iso"]) for slot in offered["slots"]]
    assert offered["ok"] and saturday in starts, "11:00 is an offered Saturday slot (10:00 to 13:30)"

    r = await w.call("book", _book_args(saturday))
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["status"] == "confirmed"
    (b,) = await w.bookings()
    assert b.start_at.astimezone(service.tz()).strftime("%A %H:%M") == "Saturday 11:00"
    assert b.start_at.astimezone(dt.UTC).strftime("%H:%M") == "05:30"
    # The reply names the weekday and time that were asked for.
    assert body["when"].startswith("Sat ") and "11:00 AM" in body["when"]


async def test_the_wrong_day_a_model_could_pick_is_not_booked_as_saturday(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sunday 11:00 (a day the model might name by mistake) is refused, not silently accepted."""
    w = await _world(client, db_session, "bk_sun@example.com")
    r = await w.call("book", _book_args(_next(6, 11, 0)))
    assert r.json()["ok"] is False and r.json()["error"] == "invalid_time"
    assert await w.bookings() == []
