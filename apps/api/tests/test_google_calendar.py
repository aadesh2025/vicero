"""Native Google Calendar (ADR-121): signed state, connect/callback/status/disconnect, token refresh, booking mirror.

Google is mocked at the HTTP layer; no real token, code or customer detail appears in an assertion message or log.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.models import Booking, GoogleCalendarConnection
from app.modules.bookings import schemas as booking_schemas
from app.modules.bookings import service as bookings
from app.modules.integrations import google_calendar as gcal
from tests.test_bookings import BOOK, World, _book_args, _next, _world

REFRESH = "1//refresh-token-secret-value"
ACCESS = "ya29.access-token-secret-value"


@pytest.fixture(autouse=True)
def _google(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "google_client_id", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "client-secret-value")
    monkeypatch.setattr(settings, "api_base_url", "https://api.example.test")
    monkeypatch.setattr(settings, "web_base_url", "https://app.example.test")
    monkeypatch.setattr(settings, "automation_report_secret", "automation-report-secret-for-tests")
    yield
    gcal.transport = None


class FakeGoogle:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.token_status = 200
        self.token_body: dict[str, Any] = {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600}
        self.refresh_body: dict[str, Any] = {"access_token": "ya29.refreshed", "expires_in": 3600}
        self.event_status = 200
        self.events: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        form = (
            {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if request.content and "json" not in request.headers.get("content-type", "")
            else {}
        )
        self.calls.append(
            {"url": url, "method": request.method, "auth": request.headers.get("authorization", ""), "form": form}
        )
        if url.startswith(gcal.TOKEN_URL):
            if form.get("grant_type") == "refresh_token":
                return httpx.Response(
                    self.token_status,
                    json=self.refresh_body if self.token_status == 200 else {"error": "invalid_grant"},
                )
            return httpx.Response(
                self.token_status, json=self.token_body if self.token_status == 200 else {"error": "invalid_grant"}
            )
        if url.startswith(gcal.USERINFO_URL):
            return httpx.Response(200, json={"email": "owner@example.com"})
        if url.startswith(gcal.REVOKE_URL):
            return httpx.Response(200, json={})
        if "/calendar/v3/calendars/" in url:
            body = json.loads(request.content)
            if self.event_status == 200:
                if any(e["id"] == body["id"] for e in self.events):
                    return httpx.Response(409, json={"error": {"code": 409}})
                self.events.append(body)
                return httpx.Response(200, json={"id": body["id"]})
            return httpx.Response(self.event_status, json={"error": {"code": self.event_status}})
        return httpx.Response(404, json={})


@pytest.fixture
def google() -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    gcal.transport = httpx.MockTransport(fake.handler)
    yield fake
    gcal.transport = None


async def _auth(client: AsyncClient, email: str) -> tuple[dict[str, str], uuid.UUID, uuid.UUID]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "Zq8-lunar-Kettle-41"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "GcalOrg"}, headers={"Authorization": f"Bearer {token}"})
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    me = await client.get("/v1/auth/me", headers=headers)
    return headers, uuid.UUID(org.json()["id"]), uuid.UUID(me.json()["user"]["id"])


# ── state and URL ─────────────────────────────────────────────────────────────────────


def test_state_round_trips_and_is_bound_to_org_and_user() -> None:
    org, user, agent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    data = gcal.read_state(gcal.make_state(org, user, agent))
    assert (data["o"], data["u"], data["a"]) == (str(org), str(user), str(agent))


def test_a_forged_tampered_or_expired_state_is_refused() -> None:
    org, user = uuid.uuid4(), uuid.uuid4()
    good = gcal.make_state(org, user)
    payload, mac = good.split(".")
    other_org = gcal.make_state(uuid.uuid4(), user).split(".")[0]
    for bad in ("", "garbage", good + "x", f"{other_org}.{mac}", f"{payload}.", f"{payload}.{'A' * len(mac)}"):
        with pytest.raises(Exception) as err:
            gcal.read_state(bad)
        assert getattr(err.value, "code", "") == "google.invalid_state", bad
    with pytest.raises(Exception) as expired:
        gcal.read_state(gcal.make_state(org, user, now=time.time() - gcal.STATE_TTL_SECONDS - 5))
    assert expired.value.code == "google.invalid_state"  # type: ignore[attr-defined]


async def test_connect_returns_the_google_url_with_offline_access_and_signed_state(client: AsyncClient) -> None:
    headers, org_id, user_id = await _auth(client, "gc_url@example.com")
    r = await client.get("/v1/integrations/google-calendar/connect", headers=headers)
    assert r.status_code == 200
    url = urlparse(r.json()["url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == gcal.AUTH_URL
    assert q["client_id"] == settings.google_client_id
    assert q["redirect_uri"] == "https://api.example.test/api/google/callback"
    assert q["response_type"] == "code" and q["access_type"] == "offline" and q["prompt"] == "consent"
    assert "https://www.googleapis.com/auth/calendar.events" in q["scope"].split()
    state = gcal.read_state(q["state"])
    assert state["o"] == str(org_id) and state["u"] == str(user_id)
    assert settings.google_client_secret not in r.text


async def test_connect_is_501_when_google_is_not_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _, _ = await _auth(client, "gc_noconf@example.com")
    monkeypatch.setattr(settings, "google_client_id", None)
    r = await client.get("/v1/integrations/google-calendar/connect", headers=headers)
    assert r.status_code == 501 and r.json()["error"]["code"] == "google.not_configured"
    status = (await client.get("/v1/integrations/google-calendar", headers=headers)).json()
    assert status["configured"] is False and status["connected"] is False


async def test_connect_needs_a_login_and_the_tools_permission(client: AsyncClient) -> None:
    assert (await client.get("/v1/integrations/google-calendar/connect")).status_code in (401, 403)
    assert (await client.get("/v1/integrations/google-calendar")).status_code in (401, 403)


# ── callback ──────────────────────────────────────────────────────────────────────────


async def _callback(client: AsyncClient, **params: str) -> httpx.Response:
    return await client.get("/api/google/callback", params=params, follow_redirects=False)


async def test_the_callback_stores_the_refresh_token_encrypted_and_goes_back_to_the_dashboard(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    headers, org_id, user_id = await _auth(client, "gc_cb@example.com")
    agent_id = uuid.uuid4()
    r = await _callback(client, code="auth-code-123", state=gcal.make_state(org_id, user_id, agent_id))
    assert r.status_code == 303
    target = urlparse(r.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"https://app.example.test/agents/{agent_id}"
    assert parse_qs(target.query) == {"tab": ["tools"], "google": ["connected"]}
    assert "auth-code" not in r.headers["location"]

    exchange = next(c for c in google.calls if c["url"].startswith(gcal.TOKEN_URL))
    assert exchange["form"]["grant_type"] == "authorization_code" and exchange["form"]["code"] == "auth-code-123"
    assert exchange["form"]["redirect_uri"] == "https://api.example.test/api/google/callback"

    conn = (await db_session.execute(select(GoogleCalendarConnection))).scalars().all()[-1]
    assert conn.organization_id == org_id and conn.google_email == "owner@example.com"
    assert conn.refresh_token_encrypted != REFRESH and decrypt(conn.refresh_token_encrypted) == REFRESH
    assert conn.access_token_encrypted != ACCESS and ACCESS not in str(conn.__dict__)

    status = (await client.get("/v1/integrations/google-calendar", headers=headers)).json()
    assert status["connected"] is True and status["email"] == "owner@example.com"
    assert status["redirect_uri"] == "https://api.example.test/api/google/callback"
    assert REFRESH not in json.dumps(status) and ACCESS not in json.dumps(status)


async def test_reconnecting_replaces_the_stored_grant(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    _, org_id, user_id = await _auth(client, "gc_re@example.com")
    await _callback(client, code="c1", state=gcal.make_state(org_id, user_id))
    google.token_body = {"access_token": "ya29.second", "refresh_token": "1//second", "expires_in": 3600}
    await _callback(client, code="c2", state=gcal.make_state(org_id, user_id))
    rows = (
        (
            await db_session.execute(
                select(GoogleCalendarConnection).where(GoogleCalendarConnection.organization_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and decrypt(rows[0].refresh_token_encrypted) == "1//second" and rows[0].status == "active"


async def test_the_callback_refuses_bad_state_and_never_calls_google(client: AsyncClient, google: FakeGoogle) -> None:
    r = await _callback(client, code="x", state="forged.state")
    assert r.status_code == 303 and parse_qs(urlparse(r.headers["location"]).query)["google"] == ["error"]
    assert not [c for c in google.calls if c["url"].startswith(gcal.TOKEN_URL)]


async def test_cancelling_at_google_returns_a_friendly_result(client: AsyncClient, google: FakeGoogle) -> None:
    r = await _callback(client, error="access_denied", state=gcal.make_state(uuid.uuid4(), uuid.uuid4()))
    assert r.status_code == 303 and parse_qs(urlparse(r.headers["location"]).query)["google"] == ["cancelled"]


async def test_a_user_who_left_the_workspace_cannot_complete_the_connection(
    client: AsyncClient, google: FakeGoogle
) -> None:
    _, org_id, _ = await _auth(client, "gc_member@example.com")
    r = await _callback(client, code="x", state=gcal.make_state(org_id, uuid.uuid4()))  # a user with no membership
    assert parse_qs(urlparse(r.headers["location"]).query)["google"] == ["error"]
    assert not [c for c in google.calls if c["url"].startswith(gcal.TOKEN_URL)]


async def test_no_refresh_token_means_no_connection(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    _, org_id, user_id = await _auth(client, "gc_norefresh@example.com")
    google.token_body = {"access_token": ACCESS, "expires_in": 3600}  # Google omits it without consent
    r = await _callback(client, code="x", state=gcal.make_state(org_id, user_id))
    assert parse_qs(urlparse(r.headers["location"]).query)["reason"] == ["google.no_refresh_token"]
    assert await gcal.get_connection(db_session, org_id) is None


async def test_google_rejecting_the_code_is_a_clean_error(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    _, org_id, user_id = await _auth(client, "gc_badcode@example.com")
    google.token_status = 400
    r = await _callback(client, code="x", state=gcal.make_state(org_id, user_id))
    assert parse_qs(urlparse(r.headers["location"]).query)["google"] == ["error"]
    assert await gcal.get_connection(db_session, org_id) is None


async def test_disconnect_forgets_the_grant_and_revokes_it(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    headers, org_id, user_id = await _auth(client, "gc_disc@example.com")
    await _callback(client, code="x", state=gcal.make_state(org_id, user_id))
    assert (await client.delete("/v1/integrations/google-calendar", headers=headers)).status_code == 204
    assert await gcal.get_connection(db_session, org_id) is None
    revoke = [c for c in google.calls if c["url"].startswith(gcal.REVOKE_URL)]
    assert len(revoke) == 1 and revoke[0]["form"]["token"] == REFRESH
    assert (await client.get("/v1/integrations/google-calendar", headers=headers)).json()["connected"] is False
    assert (await client.delete("/v1/integrations/google-calendar", headers=headers)).status_code == 204  # idempotent


async def test_connections_are_per_organisation(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    a_headers, a_org, a_user = await _auth(client, "gc_iso_a@example.com")
    b_headers, _, _ = await _auth(client, "gc_iso_b@example.com")
    await _callback(client, code="x", state=gcal.make_state(a_org, a_user))
    assert (await client.get("/v1/integrations/google-calendar", headers=a_headers)).json()["connected"] is True
    assert (await client.get("/v1/integrations/google-calendar", headers=b_headers)).json()["connected"] is False


# ── booking mirror ────────────────────────────────────────────────────────────────────


async def _connect(
    db: AsyncSession, org_id: uuid.UUID, *, expired: bool = False, status: str = "active"
) -> GoogleCalendarConnection:
    conn = GoogleCalendarConnection(
        organization_id=org_id,
        refresh_token_encrypted=encrypt(REFRESH),
        access_token_encrypted=encrypt(ACCESS),
        access_token_expires_at=dt.datetime.now(tz=dt.UTC)
        + (dt.timedelta(seconds=-5) if expired else dt.timedelta(hours=1)),
        google_email="owner@example.com",
        status=status,
    )
    db.add(conn)
    await db.flush()
    return conn


async def _book(w: World, start: dt.datetime) -> dict[str, Any]:
    r = await w.call("book", _book_args(start))
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_a_connected_org_gets_a_calendar_event_for_each_booking(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    w = await _world(client, db_session, "gc_book@example.com")
    await _connect(db_session, w.org_id)
    start = _next(1, 15, 0)
    out = await _book(w, start)

    assert out["ok"] and out["calendar_event"] is True
    assert len(google.events) == 1
    event = google.events[0]
    assert event["id"] == uuid.UUID(out["booking_id"]).hex
    assert event["summary"] == "Appointment: Asha K"
    assert (
        event["start"]["dateTime"].startswith(start.isoformat()[:16]) and event["start"]["timeZone"] == "Asia/Kolkata"
    )
    assert event["end"]["dateTime"] != event["start"]["dateTime"]
    assert "asha@example.com" in event["description"] and out["reference"] in event["description"]
    call = next(c for c in google.calls if "/calendar/v3/calendars/primary/events" in c["url"])
    assert call["auth"] == f"Bearer {ACCESS}"
    assert (await w.bookings())[0].external_event_id == event["id"]


async def test_an_expired_access_token_is_refreshed_and_stored(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    w = await _world(client, db_session, "gc_refresh@example.com")
    conn = await _connect(db_session, w.org_id, expired=True)
    out = await _book(w, _next(1, 11, 0))
    assert out["calendar_event"] is True
    refresh = [c for c in google.calls if c["form"].get("grant_type") == "refresh_token"]
    assert len(refresh) == 1 and refresh[0]["form"]["refresh_token"] == REFRESH
    event_call = next(c for c in google.calls if "/calendar/v3/" in c["url"])
    assert event_call["auth"] == "Bearer ya29.refreshed"
    assert decrypt(conn.access_token_encrypted or "") == "ya29.refreshed"


async def test_not_connected_means_the_booking_is_saved_and_nothing_calls_google(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    w = await _world(client, db_session, "gc_none@example.com")
    out = await _book(w, _next(1, 12, 0))
    assert out["ok"] and out["calendar_event"] is False
    assert google.calls == [] and len(await w.bookings()) == 1


@pytest.mark.parametrize("failure", ["http_500", "forbidden", "revoked", "network", "slow"])
async def test_google_failing_never_fails_the_booking(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    w = await _world(client, db_session, f"gc_fail_{failure}@example.com")
    conn = await _connect(db_session, w.org_id, expired=failure == "revoked")
    if failure == "http_500":
        google.event_status = 500
    elif failure == "forbidden":
        google.event_status = 403
    elif failure == "revoked":
        google.token_status = 400  # refresh answers invalid_grant
    elif failure == "network":
        gcal.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    elif failure == "slow":
        monkeypatch.setattr(gcal, "EVENT_BUDGET_SECONDS", 0.05)

        async def hang(*_a: Any, **_k: Any) -> str | None:
            import asyncio

            await asyncio.sleep(5)
            return "late"

        monkeypatch.setattr(gcal, "_create_event", hang)

    with structlog.testing.capture_logs() as logs:
        out = await _book(w, _next(2, 14, 0))
    assert out["ok"] is True and out["calendar_event"] is False and out["status"] == "confirmed"
    rows = await w.bookings()
    assert len(rows) == 1 and rows[0].external_event_id is None
    if failure == "revoked":
        assert conn.status == "needs_reconnect"
    blob = json.dumps([{k: str(v) for k, v in e.items()} for e in logs])
    assert REFRESH not in blob and ACCESS not in blob and "asha@example.com" not in blob


async def test_a_needs_reconnect_connection_is_skipped(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    w = await _world(client, db_session, "gc_skip@example.com")
    await _connect(db_session, w.org_id, status="needs_reconnect")
    out = await _book(w, _next(2, 15, 0))
    assert out["ok"] and out["calendar_event"] is False and google.calls == []
    status = gcal.status_out(await gcal.get_connection(db_session, w.org_id))
    assert status["connected"] is False and status["needs_reconnect"] is True


async def test_a_retry_after_a_google_outage_still_creates_exactly_one_event(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    w = await _world(client, db_session, "gc_retry@example.com")
    await _connect(db_session, w.org_id)
    run = await w.run()
    start = _next(3, 10, 30)
    google.event_status = 500
    first = (await w.call("book", _book_args(start), run=run)).json()
    assert first["ok"] and first["calendar_event"] is False
    google.event_status = 200
    retry = (await w.call("book", _book_args(start), run=run)).json()  # n8n retried the same run
    again = (await w.call("book", _book_args(start), run=run)).json()
    assert retry["duplicate"] is True and retry["calendar_event"] is True and again["calendar_event"] is True
    assert len(google.events) == 1 and len(await w.bookings()) == 1


async def test_google_saying_the_event_exists_is_success(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    w = await _world(client, db_session, "gc_409@example.com")
    await _connect(db_session, w.org_id)
    out = await _book(w, _next(3, 11, 0))
    booking = (await w.bookings())[0]
    booking.external_event_id = None  # as if our write was lost after Google accepted it
    await db_session.flush()
    assert await gcal.mirror_booking(db_session, booking) == uuid.UUID(out["booking_id"]).hex  # 409 -> same id
    assert len(google.events) == 1


async def test_one_orgs_connection_is_never_used_for_another_orgs_booking(
    client: AsyncClient, db_session: AsyncSession, google: FakeGoogle
) -> None:
    a = await _world(client, db_session, "gc_x_a@example.com")
    b = await _world(client, db_session, "gc_x_b@example.com")
    await _connect(db_session, a.org_id)
    out = await _book(b, _next(3, 12, 0))
    assert out["ok"] and out["calendar_event"] is False and google.calls == []


def test_the_documented_redirect_uri_is_what_the_code_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "api_base_url", "https://viceroai-api.duckdns.org/")
    assert gcal.redirect_uri() == "https://viceroai-api.duckdns.org/api/google/callback"


async def test_the_booking_flow_still_works_with_the_unused_imports(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(client, db_session, "gc_sanity@example.com")
    assert BOOK["name"] and booking_schemas.TOOL_SPECS and bookings.SLOT_MINUTES == 30
    out = await _book(w, _next(4, 10, 0))
    assert out["ok"] and isinstance((await w.bookings())[0], Booking)
