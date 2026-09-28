"""Analytics schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel


class TodaySnapshot(BaseModel):
    """Conversations in a caller-supplied window, split into exactly three buckets
    (docs/20-UI-REDESIGN-DUAL-THEME.md's dashboard "Today" gauge; ADR-100).

    The window is the *caller's* local day, not the server's: the frontend computes
    `start`/`end` from the browser's own midnight-to-now (or an org timezone, once one is
    stored) and sends them as UTC instants, so this endpoint never has to guess a timezone.

    A conversation lands in exactly one bucket:
    - `resolved_by_ai` — no `Handoff` row at all; the bot handled it start to finish.
    - `handed_to_human` — a `Handoff` row exists and a human has engaged (`assigned_to` is
      set, or its status is `resolved`).
    - `unanswered` — a `Handoff` row exists but no human has picked it up yet.

    `conversations == resolved_by_ai + handed_to_human + unanswered`, always.
    """

    conversations: int
    resolved_by_ai: int
    handed_to_human: int
    unanswered: int


class ChannelBucket(BaseModel):
    """One channel's slice of the overview.

    Every channel the org has *connected* gets a bucket, including ones with no traffic
    yet — a freshly-connected WhatsApp should read as a real zero, not vanish from the
    breakdown. Rates are 0.0 on zero conversations (the UI renders that as an em dash,
    since 0% resolution would read as failure rather than "nothing happened yet").
    """

    channel: str
    conversations: int
    messages: int
    tokens_prompt: int
    tokens_completion: int
    cost_micros: int
    handoff_rate: float  # 0..1
    resolution_rate: float  # 0..1


class Overview(BaseModel):
    conversations: int
    messages: int
    users: int
    tokens_prompt: int
    tokens_completion: int
    cost_micros: int
    handoff_rate: float  # 0..1
    resolution_rate: float  # 0..1
    by_channel: list[ChannelBucket] = []


class UsageBucket(BaseModel):
    key: str  # day (ISO date) | provider | model | channel
    tokens_prompt: int
    tokens_completion: int
    requests: int
    cost_micros: int


class DayPoint(BaseModel):
    """One calendar day of activity.

    Distinct from `UsageBucket` because a chart and a usage table want different things.
    `UsageBucket` is sparse — it only has rows for days that saw traffic — which is correct
    for a table and wrong for a time series: plotting sparse points by array index spaces
    Jul 30, Aug 2 and Aug 6 evenly and draws a straight line through a month of silence.
    Every day in the range gets a point here, zeros included.

    `conversations` counts conversations *started* that day, which is what "conversations
    per day" means to an operator. The chart previously plotted assistant message count,
    since that was the only per-day number the API had.
    """

    date: dt.date
    conversations: int
    messages: int
    tokens_prompt: int
    tokens_completion: int
    cost_micros: int


class TimeseriesPoint(BaseModel):
    """One bucket of the dashboard "Activity" bar chart (docs/20 §9.3.2, ADR-101).

    `bucket_start` is the *local* calendar date the bucket starts on (the Monday of a week,
    the 1st of a month) — local to the `tz` the request was made with, not UTC, so a bar
    labelled "Sep 28" really is everything the caller's midnight-to-midnight Sep 28 contained.
    """

    bucket_start: dt.date
    value: float


class TimeseriesResponse(BaseModel):
    """`GET /v1/analytics/timeseries` (ADR-101): one metric, zero-filled, bucketed by the
    caller's timezone, plus the same-length prior period's total for the header's delta pill.
    """

    granularity: Literal["day", "week", "month"]
    metric: Literal["conversations", "messages", "tokens", "cost"]
    points: list[TimeseriesPoint]
    previous_period_total: float


class AgentBucket(BaseModel):
    """One agent's slice of the org's traffic.

    Named for agents-as-bots. Not to be confused with `AgentPerformanceBucket`, which is one
    human teammate's inbox workload — the two words collide in this product and the
    endpoints (`/by-agent` vs `/agents`) are deliberately spelled differently because of it.
    """

    agent_id: uuid.UUID
    name: str
    status: str
    #: A deleted agent still appears while it has traffic in range — its tokens were really
    #: spent, and dropping it stops the rows summing to the overview. Flagged so the UI can
    #: say why a name here isn't in the agent list.
    deleted: bool = False
    conversations: int
    messages: int
    tokens_prompt: int
    tokens_completion: int
    cost_micros: int
    handoff_rate: float  # 0..1
    resolution_rate: float  # 0..1
    #: None when the agent has never been messaged — not an epoch timestamp.
    last_active_at: dt.datetime | None


class LatencyStats(BaseModel):
    count: int
    avg_ms: float
    p50_ms: int
    p95_ms: int


class QuestionCount(BaseModel):
    question: str
    count: int


class AgentPerformanceBucket(BaseModel):
    """One human teammate's inbox workload.

    Distinct from the per-channel breakdown: this reports on *people*, not surfaces.
    Durations are None rather than 0 when there's nothing to average — a teammate who has
    never resolved anything hasn't achieved a 0ms resolution time.
    """

    user_id: uuid.UUID
    name: str
    handoffs: int
    avg_first_response_ms: int | None
    avg_resolution_ms: int | None
    closed_count: int
