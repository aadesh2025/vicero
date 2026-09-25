"""Message metering + the silent stop (docs/18 §7, §8, §13).

1 message = 1 visitor message OR 1 AI reply, so one question and its answer cost 2.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.billing import usage
from app.channels import get_channel
from app.chat.handoff import trigger_handoff
from app.core.config import settings
from app.llm.base import ProviderError
from app.llm.types import ChatRequest, StreamEvent
from app.models import (
    AuditLog,
    Channel,
    Conversation,
    Handoff,
    Message,
    Organization,
    OrgMessageUsage,
)
from tests.selfserve_helpers import chat, make_agent_public, set_trial_end, trial_org

pytestmark = pytest.mark.usefixtures("self_serve")

# Words that must never reach a visitor (docs/18 §8): no error text, no upsell, no quota talk.
_FORBIDDEN = ("trial", "upgrade", "quota", "limit", "plan", "expired", "error", "billing")


async def _used(client: AsyncClient, headers: dict[str, str], org_id: str) -> dict[str, object]:
    return (await client.get(f"/v1/orgs/{org_id}/plan", headers=headers)).json()  # type: ignore[no-any-return]


async def _set_used(db: AsyncSession, org_id: str, n: int) -> None:
    await db.execute(
        update(OrgMessageUsage).where(OrgMessageUsage.organization_id == uuid.UUID(org_id)).values(messages_used=n)
    )
    await db.flush()


async def _first_conversation(db: AsyncSession, org_id: str) -> Conversation:
    rows = await db.execute(select(Conversation).where(Conversation.organization_id == uuid.UUID(org_id)))
    conv = rows.scalars().first()
    assert conv is not None
    return conv


async def _setup(client: AsyncClient) -> tuple[dict[str, str], str, str]:
    _, headers, org_id = await trial_org(client)
    return headers, org_id, await make_agent_public(client, headers)


# ── counting ─────────────────────────────────────────────────────────────────
async def test_one_question_and_its_answer_cost_two(client: AsyncClient) -> None:
    headers, org_id, key = await _setup(client)
    resp = await chat(client, key, "hello there")
    assert resp.status_code == 200 and resp.json()["content"].strip()
    assert (await _used(client, headers, org_id))["messages_used"] == 2
    await chat(client, key, "and another")
    assert (await _used(client, headers, org_id))["messages_used"] == 4


async def test_it_stops_at_exactly_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id, key = await _setup(client)
    await _set_used(db_session, org_id, 496)

    assert (await chat(client, key, "one")).json()["content"].strip()  # 496 -> 498
    assert (await chat(client, key, "two")).json()["content"].strip()  # 498 -> 500
    silent = await chat(client, key, "three")  # would be 502
    assert silent.status_code == 200
    assert silent.json()["content"] == ""

    plan = await _used(client, headers, org_id)
    assert plan["messages_used"] == 500  # never over
    assert plan["status"] == "trial_expired" and plan["expired_reason"] == "messages"
    assert plan["unanswered_messages"] == 1


async def test_an_odd_remainder_cannot_strand_the_counter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await _set_used(db_session, org_id, 499)  # e.g. after a refund left it odd
    assert (await chat(client, key, "anyone there?")).json()["content"] == ""
    assert (await _used(client, headers, org_id))["messages_used"] == 499


async def test_playground_and_operator_replies_are_not_counted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await chat(client, key, "hi")  # 2 used; creates a conversation
    conv = await _first_conversation(db_session, org_id)
    await trigger_handoff(db_session, conv, requested_by="user", reason="keyword")
    assert (await client.post(f"/v1/inbox/conversations/{conv.id}/takeover", headers=headers)).status_code == 200
    reply = await client.post(
        f"/v1/inbox/conversations/{conv.id}/messages", json={"text": "A human here"}, headers=headers
    )
    assert reply.status_code == 200, reply.text
    assert (await _used(client, headers, org_id))["messages_used"] == 2


# ── the silent stop ──────────────────────────────────────────────────────────
async def test_after_the_trial_ends_the_bot_says_nothing_and_the_message_is_saved(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    assert (await chat(client, key, "before the end")).json()["content"].strip()

    # "Day 10": the clock passes the trial end. No job runs — the status is computed.
    await set_trial_end(db_session, org_id, dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1))

    resp = await chat(client, key, "are you still there?")
    assert resp.status_code == 200
    raw = resp.text.lower()
    assert resp.json()["content"] == ""
    assert not any(word in raw for word in _FORBIDDEN), raw

    # The visitor's message is in the inbox for the owner; no assistant message was written.
    msgs = (
        await db_session.execute(
            select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
                Conversation.organization_id == uuid.UUID(org_id)
            )
        )
    ).scalars().all()
    assert "are you still there?" in [m.content for m in msgs if m.role == "user"]
    assert [m.content for m in msgs if m.role == "assistant" and "still there" in m.content] == []

    plan = await _used(client, headers, org_id)
    assert plan["status"] == "trial_expired" and plan["expired_reason"] == "time"
    assert plan["unanswered_messages"] == 1


async def test_the_streaming_widget_path_emits_no_text_and_no_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, org_id, key = await _setup(client)
    await set_trial_end(db_session, org_id, dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1))

    resp = await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": "hello?", "stream": True, "visitor": {"id": "v-stream"}},
    )
    assert resp.status_code == 200
    events = [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")]
    types = {e["type"] for e in events}
    assert types <= {"conversation"}, types  # no token, no error, no message
    # Fields are null on every event (`"error": null`), so look at values, not raw text.
    assert all(not e.get("error") and not e.get("delta") for e in events), events
    assert not any(word in json.dumps([e.get("conversation_id") for e in events]).lower() for word in _FORBIDDEN)


async def test_a_reply_that_finishes_streaming_is_unaffected_before_the_end(
    client: AsyncClient,
) -> None:
    _, _, key = await _setup(client)
    resp = await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": "hello", "stream": True, "visitor": {"id": "v-live"}},
    )
    assert "token" in {json.loads(x[5:])["type"] for x in resp.text.splitlines() if x.startswith("data:")}


async def test_the_first_time_the_limit_is_hit_is_audited_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, org_id, key = await _setup(client)
    await set_trial_end(db_session, org_id, dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1))
    for text in ("a", "b", "c"):
        await chat(client, key, text)
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.organization_id == uuid.UUID(org_id), AuditLog.action == "plan.limit_hit"
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_a_conversation_handed_to_a_human_is_not_counted_as_unanswered(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await chat(client, key, "hi")
    conv = await _first_conversation(db_session, org_id)
    await trigger_handoff(db_session, conv, requested_by="user", reason="keyword")
    await client.post(f"/v1/inbox/conversations/{conv.id}/takeover", headers=headers)
    before = (await _used(client, headers, org_id))["messages_used"]
    await client.post(
        f"/v1/public/agents/{key}/chat",
        json={
            "message": "still waiting",
            "conversation_id": str(conv.id),
            "stream": False,
            "visitor": {"id": "v-1"},
        },
    )
    after = await _used(client, headers, org_id)
    assert after["messages_used"] == before and after["unanswered_messages"] == 0


# ── a provider failure gives the reply back ──────────────────────────────────
class _BrokenProvider:
    name = "broken"

    def supports_tools(self) -> bool:
        return False

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        raise ProviderError("provider returned 500")
        yield  # pragma: no cover

    async def chat(self, req: ChatRequest) -> object:  # pragma: no cover
        raise ProviderError("provider returned 500")


async def test_an_llm_failure_refunds_the_reserved_reply(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, org_id, key = await _setup(client)

    async def broken(*_a: object, **_k: object) -> _BrokenProvider:
        return _BrokenProvider()

    monkeypatch.setattr("app.modules.conversations.service._resolve_provider", broken)
    resp = await chat(client, key, "will this work?")
    assert resp.status_code == 200
    # The visitor's message still counts; the reply that was never generated does not.
    assert (await _used(client, headers, org_id))["messages_used"] == 1


# ── every channel goes through the same gate ─────────────────────────────────
def _telegram_capture(calls: list[dict[str, str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "body": request.content.decode() if request.content else ""})
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


async def test_a_messaging_channel_is_silenced_and_counted_like_the_widget(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    calls: list[dict[str, str]] = []
    get_channel("telegram").transport = _telegram_capture(calls)  # type: ignore[union-attr]
    try:
        headers, org_id, _ = await _setup(client)
        agent = (await client.get("/v1/agents", headers=headers)).json()[0]
        created = await client.post(
            "/v1/channels",
            json={"agent_id": agent["id"], "type": "telegram", "config": {"bot_token": "123:abc"}},
            headers=headers,
        )
        cid = created.json()["id"]
        channel = await db_session.get(Channel, uuid.UUID(cid))
        assert channel is not None
        channel.enabled = True  # enabling through the API needs a verified email; not the subject here
        await db_session.flush()
        hook = {"X-Telegram-Bot-Api-Secret-Token": channel.webhook_secret}
        update_body = {"message": {"chat": {"id": 55}, "text": "hello telegram"}}

        live = await client.post(f"/v1/channels/telegram/{cid}/webhook", json=update_body, headers=hook)
        assert live.status_code == 200
        assert [c for c in calls if "sendMessage" in c["url"]], "an active trial must reply"
        assert (await _used(client, headers, org_id))["messages_used"] == 2

        calls.clear()
        await set_trial_end(db_session, org_id, dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1))
        after = await client.post(f"/v1/channels/telegram/{cid}/webhook", json=update_body, headers=hook)
        assert after.status_code == 200
        assert [c for c in calls if "sendMessage" in c["url"]] == []  # nothing sent to the visitor
    finally:
        get_channel("telegram").transport = None  # type: ignore[union-attr]


# ── one bot cannot burn the whole trial in a burst ───────────────────────────
async def test_a_burst_is_dropped_quietly_and_not_charged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, org_id, key = await _setup(client)
    monkeypatch.setattr(settings, "org_chat_rate_limit", 2)
    contents = [(await chat(client, key, f"m{i}", visitor=f"v-{i}")).json()["content"] for i in range(4)]
    assert [bool(c.strip()) for c in contents] == [True, True, False, False]
    assert (await _used(client, headers, org_id))["messages_used"] == 4


# ── legacy orgs are never metered ────────────────────────────────────────────
async def test_a_legacy_org_is_not_counted_or_silenced(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    key = await make_agent_public(client, headers)
    org = await db_session.get(Organization, uuid.UUID(org_id))
    assert org is not None
    org.plan = "legacy"
    org.trial_ends_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=30)  # would be long expired
    await db_session.flush()
    assert (await chat(client, key, "still answered")).json()["content"].strip()
    assert (await _used(client, headers, org_id))["messages_used"] == 0


# ── concurrency: the atomic reservation ──────────────────────────────────────
async def test_concurrent_reservations_never_exceed_the_cap() -> None:
    """300 racing visitors against a 500 cap → exactly 250 pairs, never more.

    Real committed rows on separate connections: the shared-transaction test fixture cannot
    exhibit a race, because every statement there runs on one connection.
    """
    engine = create_async_engine(settings.database_url)
    org_id = uuid.uuid4()
    try:
        from sqlalchemy.ext.asyncio import AsyncSession as S

        async with S(engine) as s:
            s.add(Organization(id=org_id, name="Race", slug=f"race-{org_id.hex[:10]}", plan="trial"))
            await s.flush()
            s.add(OrgMessageUsage(organization_id=org_id))
            await s.commit()

        async def one() -> bool:
            async with S(engine) as s:
                return await usage.reserve(s, org_id, 500)

        results = await asyncio.gather(*[one() for _ in range(300)])
        assert sum(results) == 250

        async with S(engine) as s:
            assert await usage.messages_used(s, org_id) == 500
    finally:
        async with AsyncSession(engine) as s:
            org = await s.get(Organization, org_id)
            if org is not None:
                await s.delete(org)
                await s.commit()
        await engine.dispose()


async def test_a_refund_returns_exactly_one() -> None:
    engine = create_async_engine(settings.database_url)
    org_id = uuid.uuid4()
    try:
        async with AsyncSession(engine) as s:
            s.add(Organization(id=org_id, name="Refund", slug=f"refund-{org_id.hex[:10]}", plan="trial"))
            await s.flush()
            s.add(OrgMessageUsage(organization_id=org_id, messages_used=10))
            await s.commit()
        async with AsyncSession(engine) as s:
            assert await usage.reserve(s, org_id, 500)
            await usage.refund(s, org_id, 1)
            assert await usage.messages_used(s, org_id) == 11
    finally:
        async with AsyncSession(engine) as s:
            org = await s.get(Organization, org_id)
            if org is not None:
                await s.delete(org)
                await s.commit()
        await engine.dispose()


# ── a silenced conversation lands in the Inbox handoff queue (docs/18 §8) ────
async def _handoff_items(client: AsyncClient, headers: dict[str, str]) -> list[dict]:  # type: ignore[type-arg]
    resp = await client.get("/v1/inbox/conversations?status=handoff", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def _expire(db: AsyncSession, org_id: str) -> None:
    await set_trial_end(db, org_id, dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1))


async def test_an_unanswered_conversation_is_routed_to_the_inbox_as_a_plan_limit_handoff(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await _expire(db_session, org_id)

    silent = await chat(client, key, "anyone there?")
    assert silent.json()["content"] == ""  # still no reply, still no text to the visitor

    (item,) = await _handoff_items(client, headers)
    assert item["status"] == "handoff"
    assert item["handoff"]["reason"] == "plan_limit"
    assert item["handoff"]["requested_by"] == "system"
    assert item["handoff"]["status"] == "open"
    assert item["message_count"] == 1  # the visitor's message is saved


async def test_running_out_of_messages_routes_it_too(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await _set_used(db_session, org_id, 500)
    await chat(client, key, "no messages left")
    (item,) = await _handoff_items(client, headers)
    assert item["handoff"]["reason"] == "plan_limit"


async def test_a_plan_limit_handoff_reads_differently_from_a_request_for_a_person(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await chat(client, key, "hello")  # answered normally
    conv = await _first_conversation(db_session, org_id)
    await trigger_handoff(db_session, conv, requested_by="user", reason="keyword")
    await _expire(db_session, org_id)
    await chat(client, key, "second visitor", visitor="v-2")  # a different visitor, now silenced

    reasons = sorted(i["handoff"]["reason"] for i in await _handoff_items(client, headers))
    assert reasons == ["keyword", "plan_limit"]


async def test_more_messages_in_a_parked_conversation_are_counted_but_do_not_reopen_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await _expire(db_session, org_id)
    first = await chat(client, key, "one")
    cid = first.json()["conversation_id"]
    for text in ("two", "three"):
        again = await client.post(
            f"/v1/public/agents/{key}/chat",
            json={"message": text, "conversation_id": cid, "stream": False, "visitor": {"id": "v-1"}},
        )
        assert again.status_code == 200 and again.json()["content"] == ""

    items = await _handoff_items(client, headers)
    assert len(items) == 1 and items[0]["message_count"] == 3  # one conversation, three saved messages
    rows = (
        await db_session.execute(
            select(Handoff).where(Handoff.organization_id == uuid.UUID(org_id))
        )
    ).scalars().all()
    assert len(rows) == 1  # not a new handoff per message
    assert (await _used(client, headers, org_id))["unanswered_messages"] == 3  # but every message counts


async def test_once_the_owner_takes_over_their_replies_are_the_answer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org_id, key = await _setup(client)
    await _expire(db_session, org_id)
    cid = (await chat(client, key, "waiting")).json()["conversation_id"]

    took = await client.post(f"/v1/inbox/conversations/{cid}/takeover", headers=headers)
    assert took.status_code == 200, took.text
    reply = await client.post(
        f"/v1/inbox/conversations/{cid}/messages", json={"text": "Sorry for the wait"}, headers=headers
    )
    assert reply.status_code == 200, reply.text

    await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": "thanks", "conversation_id": cid, "stream": False, "visitor": {"id": "v-1"}},
    )
    # Assigned to a person now: that message is theirs to answer, not an unanswered one.
    assert (await _used(client, headers, org_id))["unanswered_messages"] == 1


async def test_a_burst_drop_is_not_a_plan_limit_and_is_not_routed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _, key = await _setup(client)
    monkeypatch.setattr(settings, "org_chat_rate_limit", 1)
    await chat(client, key, "first", visitor="v-a")
    await chat(client, key, "dropped", visitor="v-b")  # over the burst limit: silent
    assert await _handoff_items(client, headers) == []


async def test_a_working_org_never_gets_plan_limit_handoffs(client: AsyncClient) -> None:
    headers, _, key = await _setup(client)
    await chat(client, key, "hello")
    assert await _handoff_items(client, headers) == []


async def test_a_legacy_org_is_never_routed(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id, key = await _setup(client)
    org = await db_session.get(Organization, uuid.UUID(org_id))
    assert org is not None
    org.plan = "legacy"
    org.trial_ends_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=30)
    await db_session.flush()
    assert (await chat(client, key, "hi")).json()["content"].strip()
    assert await _handoff_items(client, headers) == []
