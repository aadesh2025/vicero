"""Analytics routes under /v1/analytics (docs/04 §Analytics)."""

from __future__ import annotations

import datetime as dt
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.analytics import schemas, service
from app.modules.orgs.deps import OrgContext, current_org

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])

# Narrow to one channel. Not an enum: `conversations.channel` also holds legacy values
# (`web`, `api`) that predate the channel registry, and an unknown value should return an
# empty result rather than a 422.
_channel_q = Query(default=None, description="Filter to one channel, e.g. `instagram`.")


@router.get("/today", response_model=schemas.TodaySnapshot)
async def today(
    start: dt.datetime = Query(..., description="Start of the caller's 'today', as a UTC instant."),
    end: dt.datetime = Query(..., description="End of the caller's 'today' (usually now), as a UTC instant."),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.TodaySnapshot:
    """Dashboard "Today" gauge (docs/20, ADR-100): resolved/handed-off/unanswered counts for
    a caller-supplied window. Not date-only, because "today" means the *caller's* local day —
    the frontend sends its own midnight-to-now as UTC instants rather than this route guessing
    a timezone. Capped at 48h so a malformed range can't turn into a full-table scan."""
    if end <= start:
        raise HTTPException(status_code=422, detail="`end` must be after `start`.")
    if end - start > dt.timedelta(hours=48):
        raise HTTPException(status_code=422, detail="Range too wide — `today` means one day.")
    return await service.today_snapshot(session, ctx, start, end)


@router.get("/overview", response_model=schemas.Overview)
async def overview(
    agent_id: uuid.UUID | None = Query(default=None),
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.Overview:
    return await service.overview(session, ctx, agent_id, from_date, to_date, channel)


@router.get("/usage", response_model=list[schemas.UsageBucket])
async def usage(
    agent_id: uuid.UUID | None = Query(default=None),
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    group_by: str = Query(default="day", pattern="^(day|provider|model|channel)$"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.UsageBucket]:
    return await service.usage(session, ctx, agent_id, from_date, to_date, group_by, channel)


@router.get("/series", response_model=list[schemas.DayPoint])
async def series(
    agent_id: uuid.UUID | None = Query(default=None),
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.DayPoint]:
    """Daily activity for charting: one point per day in range, quiet days included."""
    return await service.series(session, ctx, agent_id, from_date, to_date, channel)


@router.get("/timeseries", response_model=schemas.TimeseriesResponse)
async def timeseries(
    metric: str = Query(pattern="^(conversations|messages|tokens|cost)$"),
    granularity: str = Query(pattern="^(day|week|month)$"),
    from_date: dt.date = Query(..., alias="from"),
    to_date: dt.date = Query(..., alias="to"),
    tz: str = Query(default="UTC", description="IANA zone, e.g. `America/New_York`."),
    agent_id: uuid.UUID | None = Query(default=None),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.TimeseriesResponse:
    """Dashboard "Activity" bar chart (docs/20 §9.3.2, ADR-101). The frontend sends the
    browser's own IANA zone (this app has no stored org timezone yet, same gap ADR-100
    flagged) so a bar labelled "Sep 28" buckets by *that* midnight, not UTC's."""
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="`to` must not be before `from`.")
    if (to_date - from_date).days > 1100:
        raise HTTPException(status_code=422, detail="Range too wide.")
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Unknown timezone: {tz}") from exc
    return await service.timeseries(session, ctx, metric, granularity, from_date, to_date, zone, agent_id, channel)


@router.get("/by-agent", response_model=list[schemas.AgentBucket])
async def by_agent(
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.AgentBucket]:
    """Per-*bot* traffic breakdown. `/agents` below is per-*teammate* — different report."""
    return await service.by_agent(session, ctx, from_date, to_date, channel)


@router.get("/agents", response_model=list[schemas.AgentPerformanceBucket])
async def agent_performance(
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.AgentPerformanceBucket]:
    """Per-teammate inbox workload. Reports on people, not channels."""
    return await service.agent_performance(session, ctx, from_date, to_date)


@router.get("/latency", response_model=schemas.LatencyStats)
async def latency(
    agent_id: uuid.UUID | None = Query(default=None),
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.LatencyStats:
    return await service.latency(session, ctx, agent_id, from_date, to_date, channel)


@router.get("/top-questions", response_model=list[schemas.QuestionCount])
async def top_questions(
    agent_id: uuid.UUID | None = Query(default=None),
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.QuestionCount]:
    return await service.top_questions(session, ctx, agent_id, from_date, to_date, channel=channel)


@router.get("/unanswered", response_model=list[schemas.QuestionCount])
async def unanswered(
    agent_id: uuid.UUID | None = Query(default=None),
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.QuestionCount]:
    return await service.unanswered(session, ctx, agent_id, from_date, to_date, channel=channel)


@router.get("/export")
async def export_csv(
    type: str = Query(default="usage", pattern="^(usage|conversations|channels)$"),
    agent_id: uuid.UUID | None = Query(default=None),
    from_date: dt.date | None = Query(default=None, alias="from"),
    to_date: dt.date | None = Query(default=None, alias="to"),
    channel: str | None = _channel_q,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> PlainTextResponse:
    csv = await service.export_csv(session, ctx, type, agent_id, from_date, to_date, channel)
    return PlainTextResponse(
        csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{type}.csv"'},
    )
