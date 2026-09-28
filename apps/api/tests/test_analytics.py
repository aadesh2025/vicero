"""Phase 14 tests: analytics aggregation + usage rollups (real numbers from the messages table)."""

from __future__ import annotations

import datetime as dt
import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, UsageRecord
from app.worker.rollup import refresh_quota, rollup_usage


async def _headers(client: AsyncClient, email: str = "an@example.com") -> tuple[dict[str, str], str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "AnOrg"}, headers={"Authorization": f"Bearer {token}"})
    org_id = org.json()["id"]
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org_id}, org_id


async def _fake_agent(client: AsyncClient, headers: dict[str, str], handoff: bool = False) -> str:
    agent = await client.post("/v1/agents", json={"name": "Metrics Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {"tools_enabled": False, "memory_enabled": True, "handoff_enabled": handoff},
        },
        headers=headers,
    )
    return aid


async def test_overview_matches_messages(client: AsyncClient) -> None:
    headers, _ = await _headers(client)
    aid = await _fake_agent(client, headers)
    r1 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "hello", "stream": False}, headers=headers)
    cid = r1.json()["conversation_id"]
    await client.post(
        f"/v1/agents/{aid}/chat", json={"message": "again", "conversation_id": cid, "stream": False}, headers=headers
    )

    ov = await client.get("/v1/analytics/overview", headers=headers)
    assert ov.status_code == 200, ov.text
    data = ov.json()
    assert data["conversations"] == 1
    assert data["messages"] == 4  # 2 user + 2 assistant
    assert data["tokens_prompt"] == 20  # fake provider: 10 prompt tokens per assistant turn
    assert data["handoff_rate"] == 0.0
    assert data["resolution_rate"] == 1.0


async def test_usage_grouping(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "an2@example.com")
    aid = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    by_provider = await client.get("/v1/analytics/usage?group_by=provider", headers=headers)
    buckets = by_provider.json()
    assert buckets and buckets[0]["key"] == "fake"
    assert buckets[0]["requests"] == 1
    assert buckets[0]["tokens_prompt"] == 10

    by_day = await client.get("/v1/analytics/usage?group_by=day", headers=headers)
    # Analytics groups by UTC date (matches how timestamps are stored) — compare in UTC, not local.
    assert by_day.json()[0]["key"] == dt.datetime.now(dt.UTC).date().isoformat()


async def test_latency_and_top_questions(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "an3@example.com")
    aid = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "repeat me", "stream": False}, headers=headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "repeat me", "stream": False}, headers=headers)

    lat = await client.get("/v1/analytics/latency", headers=headers)
    assert lat.json()["count"] == 2

    top = await client.get("/v1/analytics/top-questions", headers=headers)
    q = next(x for x in top.json() if x["question"] == "repeat me")
    assert q["count"] == 2


async def test_unanswered_from_handoffs(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "an4@example.com")
    agent = await client.post("/v1/agents", json={"name": "HO Bot"}, headers=headers)
    aid = agent.json()["id"]
    key = agent.json()["public_key"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {"tools_enabled": False, "memory_enabled": True, "handoff_enabled": True},
        },
        headers=headers,
    )
    # Keyword handoff fires on the widget/public inbound path.
    await client.post(f"/v1/public/agents/{key}/chat", json={"message": "I want a human agent", "stream": False})
    un = await client.get("/v1/analytics/unanswered", headers=headers)
    assert any("human agent" in x["question"] for x in un.json())


async def test_csv_export(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "an5@example.com")
    aid = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "x", "stream": False}, headers=headers)
    csv = await client.get("/v1/analytics/export?type=usage", headers=headers)
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
    assert csv.text.splitlines()[0] == "date,provider,model,tokens_prompt,tokens_completion,requests,cost_micros"


async def test_rollup_matches_live_totals(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id = await _headers(client, "an6@example.com")
    aid = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "one", "stream": False}, headers=headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "two", "stream": False}, headers=headers)

    written = await rollup_usage(db_session, uuid.UUID(org_id), dt.datetime.now(dt.UTC).date())
    assert written == 1  # one (agent, provider, model) bucket

    total = (
        await db_session.execute(
            select(
                func.sum(UsageRecord.tokens_prompt),
                func.sum(UsageRecord.requests),
            ).where(UsageRecord.organization_id == uuid.UUID(org_id))
        )
    ).one()
    assert int(total[0]) == 20  # 2 assistant turns x 10 prompt tokens
    assert int(total[1]) == 2

    tokens_used, crossed = await refresh_quota(db_session, uuid.UUID(org_id))
    assert tokens_used >= 20
    assert crossed is False  # no token_limit set


# ── Per-channel breakdown ───────────────────────────────────────────────────────────
async def _connect_channel(client: AsyncClient, headers: dict[str, str], aid: str, ctype: str) -> str:
    """Connect + enable a channel. Config is irrelevant here — these tests never deliver
    through it, they only care that an *enabled* channel exists to report on."""
    ch = await client.post(
        "/v1/channels", json={"agent_id": aid, "type": ctype, "config": {}}, headers=headers
    )
    assert ch.status_code == 201, ch.text
    cid = ch.json()["id"]
    await client.post(f"/v1/channels/{cid}/enable", headers=headers)
    return cid


async def test_overview_by_channel_includes_connected_but_empty(client: AsyncClient) -> None:
    """The case this breakdown exists for: WhatsApp gets connected with real credentials
    days before anyone messages it. It must read as a zero row, not disappear."""
    headers, _ = await _headers(client, "ch-empty@example.com")
    aid = await _fake_agent(client, headers)
    await _connect_channel(client, headers, aid, "whatsapp")

    # Traffic on one channel only (dashboard chat → the `web` conversation channel).
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    ov = (await client.get("/v1/analytics/overview", headers=headers)).json()
    by_channel = {b["channel"]: b for b in ov["by_channel"]}

    # Connected-but-silent → a real zero row, with no divide-by-zero on the rates.
    assert "whatsapp" in by_channel, "an enabled channel must appear even with no traffic"
    empty = by_channel["whatsapp"]
    assert empty["conversations"] == 0
    assert empty["messages"] == 0
    assert empty["cost_micros"] == 0
    assert empty["handoff_rate"] == 0.0
    assert empty["resolution_rate"] == 0.0

    # The widget is inherent to every agent, so it's always reportable too.
    assert "widget" in by_channel

    # And the channel that actually has traffic carries the real numbers.
    populated = next(b for b in ov["by_channel"] if b["conversations"] > 0)
    assert populated["messages"] == 2  # 1 user + 1 assistant
    assert populated["tokens_prompt"] == 10
    assert populated["resolution_rate"] == 1.0

    # Per-channel conversations reconcile with the flat total.
    assert sum(b["conversations"] for b in ov["by_channel"]) == ov["conversations"]


async def test_by_channel_omits_disabled_channels(client: AsyncClient) -> None:
    """A channel that was connected and then switched off isn't somewhere traffic can
    arrive, so it shouldn't sit in the breakdown implying it's live."""
    headers, _ = await _headers(client, "ch-disabled@example.com")
    aid = await _fake_agent(client, headers)
    cid = await _connect_channel(client, headers, aid, "telegram")
    await client.post(f"/v1/channels/{cid}/disable", headers=headers)

    ov = (await client.get("/v1/analytics/overview", headers=headers)).json()
    assert "telegram" not in {b["channel"] for b in ov["by_channel"]}


async def test_usage_group_by_channel(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "ch-usage@example.com")
    aid = await _fake_agent(client, headers)
    await _connect_channel(client, headers, aid, "instagram")
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    buckets = (await client.get("/v1/analytics/usage?group_by=channel", headers=headers)).json()
    by_key = {b["key"]: b for b in buckets}

    populated = next(b for b in buckets if b["requests"] > 0)
    assert populated["tokens_prompt"] == 10

    # Zero-filled the same way the overview breakdown is.
    assert by_key["instagram"]["requests"] == 0
    assert by_key["instagram"]["tokens_prompt"] == 0
    assert by_key["instagram"]["cost_micros"] == 0


async def test_channel_filter_narrows_results(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "ch-filter@example.com")
    aid = await _fake_agent(client, headers)
    await _connect_channel(client, headers, aid, "instagram")
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    # An enabled channel with no traffic: real response, all zeros, no error.
    scoped = (await client.get("/v1/analytics/overview?channel=instagram", headers=headers)).json()
    assert scoped["conversations"] == 0
    assert scoped["messages"] == 0
    assert [b["channel"] for b in scoped["by_channel"]] == ["instagram"]

    # An unknown channel is an empty result, not a 422 — `channel` is free text because
    # conversations still carry legacy values (`web`, `api`).
    unknown = await client.get("/v1/analytics/overview?channel=nope", headers=headers)
    assert unknown.status_code == 200
    assert unknown.json()["conversations"] == 0
    assert unknown.json()["by_channel"] == []


async def test_csv_channel_export(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "ch-csv@example.com")
    aid = await _fake_agent(client, headers)
    await _connect_channel(client, headers, aid, "facebook")
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "x", "stream": False}, headers=headers)

    csv = await client.get("/v1/analytics/export?type=channels", headers=headers)
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
    lines = csv.text.splitlines()
    assert lines[0] == (
        "channel,conversations,messages,tokens_prompt,tokens_completion,cost_micros,"
        "handoff_rate,resolution_rate"
    )
    # The connected-but-empty channel is exported as a zero row, not dropped.
    fb = next(line for line in lines[1:] if line.startswith("facebook,"))
    assert fb == "facebook,0,0,0,0,0,0.0,0.0"


async def test_overview_counts_dashboard_conversations_as_users(client: AsyncClient) -> None:
    """`users` counted distinct channel_user_id, which only channel/widget conversations
    carry — so an org whose traffic is all dashboard reported 0 users next to N
    conversations. An untracked conversation is one person."""
    headers, _ = await _headers(client, "users-metric@example.com")
    aid = await _fake_agent(client, headers)

    for text in ("first question", "second question"):
        r = await client.post(
            f"/v1/agents/{aid}/chat", json={"message": text, "stream": False}, headers=headers
        )
        assert r.status_code == 200, r.text

    ov = (await client.get("/v1/analytics/overview", headers=headers)).json()
    assert ov["conversations"] == 2
    assert ov["users"] == ov["conversations"]


# ── Daily series: the chart's data source ─────────────────────────────────────
async def test_series_fills_quiet_days_with_zeros(client: AsyncClient) -> None:
    """The straight-line bug.

    `usage(group_by="day")` returns only days that saw traffic, so a caller plotting them by
    index draws three scattered days as three evenly-spaced points — a straight line through
    a month of silence. Every day in the range has to be a real point.
    """
    headers, _ = await _headers(client, "series@example.com")
    aid = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    today = dt.datetime.now(tz=dt.UTC).date()
    start = today - dt.timedelta(days=6)
    r = await client.get(
        f"/v1/analytics/series?from={start.isoformat()}&to={today.isoformat()}", headers=headers
    )
    assert r.status_code == 200, r.text
    points = r.json()

    assert len(points) == 7, "one point per calendar day, not one per day with traffic"
    assert [p["date"] for p in points] == [
        (start + dt.timedelta(days=i)).isoformat() for i in range(7)
    ]
    assert points[-1]["conversations"] == 1
    assert all(p["conversations"] == 0 for p in points[:-1])


async def test_series_counts_conversations_not_assistant_messages(client: AsyncClient) -> None:
    """The chart's `conversations` axis plotted `usage.requests`, which is the assistant
    message count. Three replies in one thread is one conversation, not three."""
    headers, _ = await _headers(client, "series2@example.com")
    aid = await _fake_agent(client, headers)
    first = await client.post(
        f"/v1/agents/{aid}/chat", json={"message": "one", "stream": False}, headers=headers
    )
    cid = first.json()["conversation_id"]
    for text in ("two", "three"):
        await client.post(
            f"/v1/agents/{aid}/chat",
            json={"message": text, "conversation_id": cid, "stream": False},
            headers=headers,
        )

    points = (await client.get("/v1/analytics/series", headers=headers)).json()
    today = next(p for p in points if p["date"] == dt.datetime.now(tz=dt.UTC).date().isoformat())
    assert today["conversations"] == 1
    assert today["messages"] == 6  # 3 user + 3 assistant


# ── Per-agent breakdown ───────────────────────────────────────────────────────
async def test_by_agent_splits_traffic_and_keeps_silent_agents(client: AsyncClient) -> None:
    """A published agent nobody has messaged is a fact worth surfacing (it usually means the
    embed was never installed), so it stays in the list as a real zero."""
    headers, _ = await _headers(client, "byagent@example.com")
    busy = await _fake_agent(client, headers)
    quiet = await _fake_agent(client, headers)

    for text in ("a", "b"):
        await client.post(
            f"/v1/agents/{busy}/chat", json={"message": text, "stream": False}, headers=headers
        )

    rows = (await client.get("/v1/analytics/by-agent", headers=headers)).json()
    by_id = {r["agent_id"]: r for r in rows}
    assert set(by_id) == {busy, quiet}

    assert by_id[busy]["conversations"] == 2
    assert by_id[busy]["messages"] == 4
    assert by_id[busy]["tokens_prompt"] == 20
    assert by_id[busy]["last_active_at"] is not None

    assert by_id[quiet]["conversations"] == 0
    assert by_id[quiet]["last_active_at"] is None, "never messaged is None, not an epoch"

    assert rows[0]["agent_id"] == busy, "busiest agent sorts first"


async def test_by_agent_totals_reconcile_with_the_overview(client: AsyncClient) -> None:
    """The per-agent view and the combined view are two readings of one dataset. If they
    disagree, one of them is lying and the operator has no way to tell which."""
    headers, _ = await _headers(client, "reconcile@example.com")
    one = await _fake_agent(client, headers)
    two = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{one}/chat", json={"message": "x", "stream": False}, headers=headers)
    await client.post(f"/v1/agents/{two}/chat", json={"message": "y", "stream": False}, headers=headers)

    overview = (await client.get("/v1/analytics/overview", headers=headers)).json()
    rows = (await client.get("/v1/analytics/by-agent", headers=headers)).json()

    assert sum(r["conversations"] for r in rows) == overview["conversations"]
    assert sum(r["messages"] for r in rows) == overview["messages"]
    assert sum(r["tokens_prompt"] for r in rows) == overview["tokens_prompt"]
    assert sum(r["cost_micros"] for r in rows) == overview["cost_micros"]


async def test_series_totals_reconcile_with_the_overview(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "reconcile2@example.com")
    aid = await _fake_agent(client, headers)
    for text in ("p", "q"):
        await client.post(
            f"/v1/agents/{aid}/chat", json={"message": text, "stream": False}, headers=headers
        )

    overview = (await client.get("/v1/analytics/overview", headers=headers)).json()
    points = (await client.get("/v1/analytics/series", headers=headers)).json()

    assert sum(p["conversations"] for p in points) == overview["conversations"]
    assert sum(p["messages"] for p in points) == overview["messages"]
    assert sum(p["cost_micros"] for p in points) == overview["cost_micros"]


async def test_series_can_be_scoped_to_one_agent(client: AsyncClient) -> None:
    """What the per-agent analytics tab renders."""
    headers, _ = await _headers(client, "series-agent@example.com")
    mine = await _fake_agent(client, headers)
    other = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{mine}/chat", json={"message": "m", "stream": False}, headers=headers)
    await client.post(f"/v1/agents/{other}/chat", json={"message": "o", "stream": False}, headers=headers)

    points = (await client.get(f"/v1/analytics/series?agent_id={mine}", headers=headers)).json()
    assert sum(p["conversations"] for p in points) == 1


async def test_by_agent_still_accounts_for_a_deleted_agents_traffic(client: AsyncClient) -> None:
    """Found against live data, not invented.

    The `aurozenai` org's entire history — all 9 conversations — belongs to a soft-deleted
    agent. Filtering the breakdown on `deleted_at IS NULL` made the overview say 9 while
    every per-agent row said 0, which is exactly the "my dashboard is fake" symptom. Deleting
    an agent does not un-spend its tokens.
    """
    headers, _ = await _headers(client, "deleted-agent@example.com")
    aid = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    gone = await client.delete(f"/v1/agents/{aid}", headers=headers)
    assert gone.status_code == 204

    overview = (await client.get("/v1/analytics/overview", headers=headers)).json()
    rows = (await client.get("/v1/analytics/by-agent", headers=headers)).json()

    assert overview["conversations"] == 1, "deleting an agent must not erase its history"
    assert sum(r["conversations"] for r in rows) == overview["conversations"]

    row = next(r for r in rows if r["agent_id"] == aid)
    assert row["deleted"] is True, "flagged, so the UI can explain a name that isn't in the list"


async def test_by_agent_omits_a_deleted_agent_that_never_had_traffic(client: AsyncClient) -> None:
    """The other half of the rule: nothing to attribute means nothing to show."""
    headers, _ = await _headers(client, "deleted-empty@example.com")
    kept = await _fake_agent(client, headers)
    scrapped = await _fake_agent(client, headers)
    await client.delete(f"/v1/agents/{scrapped}", headers=headers)

    rows = (await client.get("/v1/analytics/by-agent", headers=headers)).json()
    ids = {r["agent_id"] for r in rows}
    assert kept in ids, "a live agent with no traffic is a real zero, not a hidden row"
    assert scrapped not in ids


# ── /today (ADR-100, docs/20 dashboard gauge) ───────────────────────────────────────
def _window() -> tuple[str, str]:
    """A wide-enough window (start of today UTC to now) to catch conversations this test
    file creates moments ago, expressed the way the frontend calls this endpoint."""
    now = dt.datetime.now(dt.UTC)
    start = dt.datetime.combine(now.date(), dt.time.min, tzinfo=dt.UTC)
    return start.isoformat(), now.isoformat()


async def test_today_splits_resolved_handed_off_and_unanswered(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "today1@example.com")

    # Resolved by AI: no handoff at all.
    resolved_aid = await _fake_agent(client, headers)
    await client.post(f"/v1/agents/{resolved_aid}/chat", json={"message": "hi", "stream": False}, headers=headers)

    # Handed to human: a handoff that's been taken over.
    handoff_agent = await client.post("/v1/agents", json={"name": "HO Bot"}, headers=headers)
    haid, hkey = handoff_agent.json()["id"], handoff_agent.json()["public_key"]
    await client.patch(
        f"/v1/agents/{haid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "features": {"tools_enabled": False, "memory_enabled": True, "handoff_enabled": True},
        },
        headers=headers,
    )
    assigned = await client.post(
        f"/v1/public/agents/{hkey}/chat", json={"message": "I want a human agent", "stream": False}
    )
    assigned_cid = assigned.json()["conversation_id"]
    await client.post(f"/v1/inbox/conversations/{assigned_cid}/takeover", headers=headers)

    # Unanswered: a handoff nobody has picked up yet.
    waiting = await client.post(
        f"/v1/public/agents/{hkey}/chat", json={"message": "I want a human agent", "stream": False}
    )

    start, end = _window()
    res = await client.get("/v1/analytics/today", params={"start": start, "end": end}, headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["resolved_by_ai"] == 1
    assert data["handed_to_human"] == 1
    assert data["unanswered"] == 1
    assert data["conversations"] == 3
    assert data["conversations"] == data["resolved_by_ai"] + data["handed_to_human"] + data["unanswered"]
    assert waiting.json()["conversation_id"]  # sanity: the fixture actually created a 3rd conversation


async def test_today_excludes_conversations_outside_the_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The timezone-boundary case: a conversation one second before `start` must not count,
    and one exactly at `start` must — this is the whole reason the endpoint takes instants
    from the caller instead of computing a UTC calendar day itself."""
    headers, _ = await _headers(client, "today2@example.com")
    aid = await _fake_agent(client, headers)

    before = await client.post(
        f"/v1/agents/{aid}/chat", json={"message": "yesterday", "stream": False}, headers=headers
    )
    inside = await client.post(
        f"/v1/agents/{aid}/chat", json={"message": "today", "stream": False}, headers=headers
    )

    start = dt.datetime.now(dt.UTC).replace(microsecond=0)
    end = start + dt.timedelta(hours=1)

    before_conv = await db_session.get(Conversation, uuid.UUID(before.json()["conversation_id"]))
    before_conv.created_at = start - dt.timedelta(seconds=1)
    inside_conv = await db_session.get(Conversation, uuid.UUID(inside.json()["conversation_id"]))
    inside_conv.created_at = start  # exactly on the boundary — inclusive
    await db_session.commit()

    res = await client.get(
        "/v1/analytics/today",
        params={"start": start.isoformat(), "end": end.isoformat()},
        headers=headers,
    )
    assert res.json()["conversations"] == 1


async def test_today_rejects_an_inverted_or_oversized_range(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "today3@example.com")
    now = dt.datetime.now(dt.UTC)

    inverted = await client.get(
        "/v1/analytics/today",
        params={"start": now.isoformat(), "end": (now - dt.timedelta(hours=1)).isoformat()},
        headers=headers,
    )
    assert inverted.status_code == 422

    too_wide = await client.get(
        "/v1/analytics/today",
        params={"start": (now - dt.timedelta(days=5)).isoformat(), "end": now.isoformat()},
        headers=headers,
    )
    assert too_wide.status_code == 422


# ── /timeseries (ADR-101, docs/20 §9.3.2 dashboard bar chart) ─────────────────────────
async def _set_created_at(db_session: AsyncSession, conversation_id: str, when: dt.datetime) -> None:
    conv = await db_session.get(Conversation, uuid.UUID(conversation_id))
    assert conv is not None
    conv.created_at = when
    await db_session.commit()


async def test_timeseries_zero_fills_daily_gaps_and_counts_exactly(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A 5-day UTC range with conversations on day 1 and day 3 only: every day must appear,
    quiet days as real zeros, not missing entries."""
    headers, _ = await _headers(client, "ts1@example.com")
    aid = await _fake_agent(client, headers)
    base = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)

    c1 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "d1a", "stream": False}, headers=headers)
    c2 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "d1b", "stream": False}, headers=headers)
    c3 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "d3", "stream": False}, headers=headers)
    await _set_created_at(db_session, c1.json()["conversation_id"], base)
    await _set_created_at(db_session, c2.json()["conversation_id"], base + dt.timedelta(hours=2))
    await _set_created_at(db_session, c3.json()["conversation_id"], base + dt.timedelta(days=2))

    res = await client.get(
        "/v1/analytics/timeseries",
        params={
            "metric": "conversations",
            "granularity": "day",
            "from": "2026-03-01",
            "to": "2026-03-05",
            "tz": "UTC",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    points = res.json()["points"]
    assert [p["bucket_start"] for p in points] == [
        "2026-03-01",
        "2026-03-02",
        "2026-03-03",
        "2026-03-04",
        "2026-03-05",
    ]
    assert [p["value"] for p in points] == [2, 0, 1, 0, 0]


async def test_timeseries_buckets_by_the_caller_timezone_not_utc(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """02:00 UTC on Mar 2 is still 21:00 on Mar 1 in America/New_York (UTC-5, no DST in
    March before the spring-forward date used here) — the whole reason this endpoint takes
    a `tz` instead of grouping on the stored UTC instant like the old `/series` did."""
    headers, _ = await _headers(client, "ts2@example.com")
    aid = await _fake_agent(client, headers)
    c1 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "boundary", "stream": False}, headers=headers)
    await _set_created_at(db_session, c1.json()["conversation_id"], dt.datetime(2026, 3, 2, 2, 0, tzinfo=dt.UTC))

    utc = await client.get(
        "/v1/analytics/timeseries",
        params={"metric": "conversations", "granularity": "day", "from": "2026-03-01", "to": "2026-03-02", "tz": "UTC"},
        headers=headers,
    )
    utc_points = {p["bucket_start"]: p["value"] for p in utc.json()["points"]}
    assert utc_points["2026-03-01"] == 0
    assert utc_points["2026-03-02"] == 1

    ny = await client.get(
        "/v1/analytics/timeseries",
        params={
            "metric": "conversations",
            "granularity": "day",
            "from": "2026-03-01",
            "to": "2026-03-02",
            "tz": "America/New_York",
        },
        headers=headers,
    )
    ny_points = {p["bucket_start"]: p["value"] for p in ny.json()["points"]}
    assert ny_points["2026-03-01"] == 1
    assert ny_points["2026-03-02"] == 0


async def test_timeseries_weekly_buckets_are_monday_start_and_sum_their_days(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, _ = await _headers(client, "ts3@example.com")
    aid = await _fake_agent(client, headers)
    # 2026-03-02 is a Monday. Two conversations that week, one the following Monday.
    c1 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "w1a", "stream": False}, headers=headers)
    c2 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "w1b", "stream": False}, headers=headers)
    c3 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "w2", "stream": False}, headers=headers)
    await _set_created_at(db_session, c1.json()["conversation_id"], dt.datetime(2026, 3, 2, 10, tzinfo=dt.UTC))
    await _set_created_at(db_session, c2.json()["conversation_id"], dt.datetime(2026, 3, 4, 10, tzinfo=dt.UTC))
    await _set_created_at(db_session, c3.json()["conversation_id"], dt.datetime(2026, 3, 9, 10, tzinfo=dt.UTC))

    res = await client.get(
        "/v1/analytics/timeseries",
        params={
            "metric": "conversations",
            "granularity": "week",
            "from": "2026-03-02",
            "to": "2026-03-15",
            "tz": "UTC",
        },
        headers=headers,
    )
    points = {p["bucket_start"]: p["value"] for p in res.json()["points"]}
    assert points == {"2026-03-02": 2, "2026-03-09": 1}


async def test_timeseries_monthly_buckets_sum_their_month(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, _ = await _headers(client, "ts4@example.com")
    aid = await _fake_agent(client, headers)
    c1 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "jan", "stream": False}, headers=headers)
    c2 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "feb-a", "stream": False}, headers=headers)
    c3 = await client.post(f"/v1/agents/{aid}/chat", json={"message": "feb-b", "stream": False}, headers=headers)
    await _set_created_at(db_session, c1.json()["conversation_id"], dt.datetime(2026, 1, 15, tzinfo=dt.UTC))
    await _set_created_at(db_session, c2.json()["conversation_id"], dt.datetime(2026, 2, 3, tzinfo=dt.UTC))
    await _set_created_at(db_session, c3.json()["conversation_id"], dt.datetime(2026, 2, 20, tzinfo=dt.UTC))

    res = await client.get(
        "/v1/analytics/timeseries",
        params={
            "metric": "conversations",
            "granularity": "month",
            "from": "2026-01-01",
            "to": "2026-02-28",
            "tz": "UTC",
        },
        headers=headers,
    )
    points = {p["bucket_start"]: p["value"] for p in res.json()["points"]}
    assert points == {"2026-01-01": 1, "2026-02-01": 2}


async def test_timeseries_previous_period_total_is_the_equal_length_prior_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, _ = await _headers(client, "ts5@example.com")
    aid = await _fake_agent(client, headers)
    current = await client.post(f"/v1/agents/{aid}/chat", json={"message": "now", "stream": False}, headers=headers)
    prev_a = await client.post(f"/v1/agents/{aid}/chat", json={"message": "prev-a", "stream": False}, headers=headers)
    prev_b = await client.post(f"/v1/agents/{aid}/chat", json={"message": "prev-b", "stream": False}, headers=headers)
    # Current window: 2026-04-08..2026-04-14 (7 days). Previous window is the 7 days before it.
    await _set_created_at(db_session, current.json()["conversation_id"], dt.datetime(2026, 4, 10, tzinfo=dt.UTC))
    await _set_created_at(db_session, prev_a.json()["conversation_id"], dt.datetime(2026, 4, 1, tzinfo=dt.UTC))
    await _set_created_at(db_session, prev_b.json()["conversation_id"], dt.datetime(2026, 4, 5, tzinfo=dt.UTC))

    res = await client.get(
        "/v1/analytics/timeseries",
        params={
            "metric": "conversations",
            "granularity": "day",
            "from": "2026-04-08",
            "to": "2026-04-14",
            "tz": "UTC",
        },
        headers=headers,
    )
    body = res.json()
    assert sum(p["value"] for p in body["points"]) == 1
    assert body["previous_period_total"] == 2  # the two backdated to 2026-04-01..04-07


async def test_timeseries_messages_and_tokens_metrics_match_the_messages_table(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, _ = await _headers(client, "ts6@example.com")
    aid = await _fake_agent(client, headers)
    conv = await client.post(f"/v1/agents/{aid}/chat", json={"message": "hi", "stream": False}, headers=headers)
    when = dt.datetime(2026, 5, 5, 12, tzinfo=dt.UTC)
    await _set_created_at(db_session, conv.json()["conversation_id"], when)
    await db_session.execute(
        Message.__table__.update()
        .where(Message.conversation_id == uuid.UUID(conv.json()["conversation_id"]))
        .values(created_at=when)
    )
    await db_session.commit()

    params = {"from": "2026-05-05", "to": "2026-05-05", "tz": "UTC", "granularity": "day"}
    messages = await client.get(
        "/v1/analytics/timeseries", params={**params, "metric": "messages"}, headers=headers
    )
    tokens = await client.get("/v1/analytics/timeseries", params={**params, "metric": "tokens"}, headers=headers)

    # 1 user + 1 assistant turn. Cross-checked against /overview for the same window rather
    # than hand-computed, since the fake provider's exact token split isn't this test's concern.
    overview = await client.get(
        "/v1/analytics/overview", params={"from": "2026-05-05", "to": "2026-05-05"}, headers=headers
    )
    expected_tokens = overview.json()["tokens_prompt"] + overview.json()["tokens_completion"]
    assert messages.json()["points"][0]["value"] == 2
    assert tokens.json()["points"][0]["value"] == expected_tokens
    assert expected_tokens > 0


async def test_timeseries_rejects_bad_range_and_unknown_timezone(client: AsyncClient) -> None:
    headers, _ = await _headers(client, "ts7@example.com")

    backwards = await client.get(
        "/v1/analytics/timeseries",
        params={"metric": "conversations", "granularity": "day", "from": "2026-03-05", "to": "2026-03-01"},
        headers=headers,
    )
    assert backwards.status_code == 422

    bad_tz = await client.get(
        "/v1/analytics/timeseries",
        params={
            "metric": "conversations",
            "granularity": "day",
            "from": "2026-03-01",
            "to": "2026-03-01",
            "tz": "Not/AZone",
        },
        headers=headers,
    )
    assert bad_tz.status_code == 422

    bad_metric = await client.get(
        "/v1/analytics/timeseries",
        params={"metric": "bogus", "granularity": "day", "from": "2026-03-01", "to": "2026-03-01"},
        headers=headers,
    )
    assert bad_metric.status_code == 422
