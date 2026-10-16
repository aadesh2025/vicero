"""Manual end-to-end check of the booking tools through the real n8n workflows (docs/25 section 15).

    docker exec vicero-prod-api-1 python scripts/test_booking_tools.py --agent-id <uuid>

The first step goes through the real agent path (`execute_tool_call`): the contact, conversation and tool run
are created in one still-open transaction and the tool is dispatched before anything is committed, exactly like
a customer turn. If n8n's callback to /internal/tools/* cannot see the run, that step fails (`bookings.bad_run`).
The remaining steps call `execute_n8n_tool` on a run committed up front (so they can retry the same run),
with a throwaway contact and conversation, then everything it created is deleted. It books ONE slot far enough
ahead that no real customer is affected, tries the same call again (must be a duplicate, not a second booking),
asks the customer lookup for someone else (must be refused), and prints PASS or FAIL per step. Exit code 1 if
any step fails.

It does not send a customer message (the throwaway conversation is on the `api` channel) and uses no credential beyond
the ones already in this container's environment.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import uuid
from typing import Any


def next_open_day(today: dt.date, days_ahead: int = 21) -> dt.date:
    """A Tuesday at least `days_ahead` days out: inside business hours, far from real bookings."""
    day = today + dt.timedelta(days=days_ahead)
    return day + dt.timedelta(days=(1 - day.weekday()) % 7)


async def run(agent_id: uuid.UUID) -> int:
    from sqlalchemy import delete, select

    from app.db.session import SessionFactory
    from app.llm.types import ToolCall
    from app.models import Agent, AgentVersion, Booking, Contact, Conversation, Tool, ToolRun
    from app.modules.bookings import service as bookings
    from app.tools.base import ToolContext
    from app.tools.n8n_tool import execute_n8n_tool
    from app.tools.service import execute_tool_call

    results: list[tuple[str, bool, str]] = []

    def check(step: str, ok: bool, detail: str = "") -> None:
        results.append((step, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {step}" + (f"  ({detail})" if detail else ""))

    async with SessionFactory() as session:
        agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
        if agent is None:
            print(f"error: no agent {agent_id}", file=sys.stderr)
            return 1
        tools = {
            t.name: t
            for t in (
                await session.execute(select(Tool).where(Tool.agent_id == agent.id, Tool.type == "n8n"))
            ).scalars()
        }
        missing = {"book_appointment", "check_availability", "get_customer"} - set(tools)
        if missing:
            print(
                f"error: tools not installed: {sorted(missing)} (run scripts/install_booking_tools.py)", file=sys.stderr
            )
            return 1
        version = (
            await session.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.version.desc())
                .limit(1)
            )
        ).scalar_one()

        tag = uuid.uuid4().hex[:8]
        contact = Contact(
            organization_id=agent.organization_id,
            channel="api",
            external_id=f"manual-{tag}",
            display_name="Manual Test",
        )
        session.add(contact)
        await session.flush()
        conv = Conversation(
            organization_id=agent.organization_id, agent_id=agent.id, channel="api", contact_id=contact.id
        )
        session.add(conv)
        await session.flush()
        await session.commit()  # n8n calls Vicero over HTTP, so the rows it needs must be visible to the api process

        email = f"manual.test.{tag}@example.com"
        day = next_open_day(dt.datetime.now(bookings.tz()).date())
        created_runs: list[uuid.UUID] = []
        real_conversations: list[uuid.UUID] = []
        real_contacts: list[uuid.UUID] = []

        async def real_agent_path() -> None:
            """A customer turn: nothing is committed when the tool is dispatched (execute_tool_call commits first)."""
            async with SessionFactory() as s:
                c2 = Contact(
                    organization_id=agent.organization_id,
                    channel="api",
                    external_id=f"manual-real-{tag}",
                    display_name="Manual Test",
                )
                s.add(c2)
                await s.flush()
                real_contacts.append(c2.id)
                cv2 = Conversation(
                    organization_id=agent.organization_id, agent_id=agent.id, channel="api", contact_id=c2.id
                )
                s.add(cv2)
                await s.flush()
                real_conversations.append(cv2.id)
                ctx = ToolContext(
                    session=s,
                    org_id=agent.organization_id,
                    agent_id=agent.id,
                    version=version,
                    conversation_id=cv2.id,
                )
                call_ = ToolCall(id="real-path", name="check_availability", arguments={"date": day.isoformat()})
                result = await execute_tool_call(s, ctx, tools, call_)
                await s.commit()
                run = await s.get(ToolRun, ctx.run_id) if ctx.run_id else None
            body = result.output.get("response") if isinstance(result.output.get("response"), dict) else {}
            check(
                "real agent path: n8n's callback sees the tool run",
                result.status == "success" and bool(body.get("ok")) and bool(body.get("slots")),
                f"run {run.status if run else 'missing'}; {result.error or 'no error'}",
            )

        async def call(
            name: str, args: dict[str, Any], run_id: uuid.UUID | None = None
        ) -> tuple[dict[str, Any], uuid.UUID]:
            async with SessionFactory() as s:
                tool = await s.get(Tool, tools[name].id)
                if run_id is None:
                    run_row = ToolRun(
                        organization_id=agent.organization_id,
                        tool_id=tool.id,
                        conversation_id=conv.id,
                        input=args,
                        status="pending",
                    )
                    s.add(run_row)
                    await s.flush()
                    run_id = run_row.id
                    created_runs.append(run_id)
                await s.commit()
                ctx = ToolContext(
                    session=s,
                    org_id=agent.organization_id,
                    agent_id=agent.id,
                    version=version,
                    conversation_id=conv.id,
                    run_id=run_id,
                )
                result = await execute_n8n_tool(tool.config, args, ctx)
            payload = result.output.get("response") if isinstance(result.output.get("response"), dict) else {}
            return (
                payload if result.status == "success" else {"ok": False, "error": result.error or "tool error"}
            ), run_id

        try:
            await real_agent_path()
            avail, _ = await call("check_availability", {"date": day.isoformat()})
            slots = avail.get("slots") or []
            check(
                "check_availability returns free slots",
                bool(avail.get("ok")) and len(slots) >= 8,
                f"{len(slots)} slots on {day}",
            )
            if not slots:
                return 1
            start = slots[-1]["start_iso"]

            args = {
                "name": "Manual Test",
                "contact": email,
                "start_iso": start,
                "notes": "created by scripts/test_booking_tools.py",
            }
            booked, run_id = await call("book_appointment", args)
            check(
                "book_appointment books the slot",
                bool(booked.get("ok")) and booked.get("duplicate") is False,
                str(booked.get("reference")),
            )

            async with SessionFactory() as s:
                row = await s.get(Booking, uuid.UUID(booked["booking_id"])) if booked.get("booking_id") else None
            event = row.external_event_id if row else None
            where = f"event {event}" if event else "not connected (booking is in the bookings table only)"
            print(f"info  calendar: {where}")

            again, _ = await call("book_appointment", args, run_id)  # n8n retry of the same tool run
            check(
                "a retried call is a duplicate, not a second booking",
                again.get("duplicate") is True and again.get("booking_id") == booked.get("booking_id"),
            )

            taken, _ = await call(
                "book_appointment", {**args, "name": "Someone Else", "contact": f"other.{tag}@example.com"}
            )
            check("the same slot cannot be booked by someone else", taken.get("error") == "slot_taken")

            bad, _ = await call("book_appointment", {"name": "", "contact": "nope", "start_iso": "garbage"})
            check(
                "bad input is refused field by field",
                bad.get("error") == "invalid_input" and "contact" in (bad.get("fields") or {}),
            )

            avail2, _ = await call("check_availability", {"date": day.isoformat()})
            check("the booked slot is gone from availability", len(avail2.get("slots") or []) == len(slots) - 1)

            me, _ = await call("get_customer", {"contact": email})
            check(
                "get_customer finds the person in this conversation",
                bool(me.get("found")) and bool((me.get("last_booking") or {}).get("booking_id")),
            )

            other, _ = await call("get_customer", {"contact": "somebody.else@example.com"})
            check("get_customer refuses anyone else", other.get("found") is False)
        finally:
            async with SessionFactory() as s:
                all_convs = [conv.id, *real_conversations]
                await s.execute(delete(Booking).where(Booking.conversation_id.in_(all_convs)))
                await s.execute(delete(ToolRun).where(ToolRun.conversation_id.in_(all_convs)))
                await s.execute(delete(Conversation).where(Conversation.id.in_(all_convs)))
                await s.execute(delete(Contact).where(Contact.id.in_([contact.id, *real_contacts])))
                await s.commit()
            print("cleaned up the test contact, conversation, runs and bookings")

    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} steps passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-id", type=uuid.UUID, required=True)
    return asyncio.run(run(parser.parse_args(argv).agent_id))


if __name__ == "__main__":
    raise SystemExit(main())
