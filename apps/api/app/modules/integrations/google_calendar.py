"""Native Google Calendar connection (ADR-121): connect once per organisation, then Vicero creates the events itself.

No n8n credential is involved. The flow is the standard server-side OAuth code flow with offline access:

* `authorize_url` builds the Google consent URL. The `state` is **signed, not stored** (HMAC with `SECRET_KEY`,
  10 minutes, carries the organisation, the user and a nonce), so it works across API workers and cannot be forged
  to attach someone else's Google account to another organisation.
* `complete_connection` runs from Google's redirect: verifies the state, re-checks that the user may still manage
  tools in that organisation, trades the code for tokens and stores the **refresh token encrypted**.
* `mirror_booking` creates the calendar event for a booking with a stored token, refreshing it when it has expired.
  It never raises: a Google outage, a revoked grant or a missing connection leaves the booking exactly as it was.

Tokens, codes and customer details are never logged; only error classes and ids.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rbac
from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Booking, GoogleCalendarConnection, Membership, User

log = get_logger("google_calendar")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openid.googleapis.com/v1/userinfo"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"
#: `calendar.events` is the narrowest scope that can create events; `openid email` only lets the dashboard show
#: which Google account is connected.
SCOPES = "https://www.googleapis.com/auth/calendar.events openid email"
STATE_TTL_SECONDS = 600
#: A booking must not wait on Google for long: the customer is in the chat.
EVENT_BUDGET_SECONDS = 8.0
HTTP_TIMEOUT_SECONDS = 6.0

#: Tests inject a mock transport here.
transport: httpx.AsyncBaseTransport | None = None


def redirect_uri() -> str:
    """The exact URI to register in Google Cloud (Authorized redirect URIs)."""
    return f"{settings.api_base_url.rstrip('/')}/api/google/callback"


def configured() -> bool:
    return bool((settings.google_client_id or "").strip() and (settings.google_client_secret or "").strip())


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=transport)


# ── signed state ──────────────────────────────────────────────────────────────────────


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(payload: str) -> str:
    return _b64(hmac.new(settings.secret_key.encode(), f"google-calendar.{payload}".encode(), hashlib.sha256).digest())


def make_state(
    org_id: uuid.UUID, user_id: uuid.UUID, agent_id: uuid.UUID | None = None, *, now: float | None = None
) -> str:
    body = {
        "o": str(org_id),
        "u": str(user_id),
        "a": str(agent_id) if agent_id else None,
        "e": int((now if now is not None else time.time()) + STATE_TTL_SECONDS),
        "n": secrets.token_urlsafe(8),
    }
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_mac(payload)}"


def read_state(state: str, *, now: float | None = None) -> dict[str, Any]:
    """The verified state payload, or a 400. The same message for every failure: nothing to probe."""
    bad = AppError("google.invalid_state", "This connection link is invalid or has expired. Try again.", 400)
    try:
        payload, mac = state.split(".", 1)
        if not hmac.compare_digest(_mac(payload), mac):
            raise bad
        body = json.loads(_unb64(payload))
        if int(body["e"]) < (now if now is not None else time.time()):
            raise bad
        uuid.UUID(body["o"])
        uuid.UUID(body["u"])
    except AppError:
        raise
    except Exception as exc:
        raise bad from exc
    return dict(body)


def authorize_url(org_id: uuid.UUID, user_id: uuid.UUID, agent_id: uuid.UUID | None = None) -> str:
    if not configured():
        raise AppError("google.not_configured", "Google Calendar is not set up on this server.", 501)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",  # we need a refresh token: the customer is not at the dashboard when they book
        "prompt": "consent",  # Google only returns a refresh token on consent
        "include_granted_scopes": "true",
        "state": make_state(org_id, user_id, agent_id),
    }
    return f"{AUTH_URL}?{urlencode(params)}"


# ── connect / status / disconnect ─────────────────────────────────────────────────────


async def get_connection(session: AsyncSession, org_id: uuid.UUID) -> GoogleCalendarConnection | None:
    return (
        await session.execute(
            select(GoogleCalendarConnection).where(GoogleCalendarConnection.organization_id == org_id)
        )
    ).scalar_one_or_none()


def status_out(conn: GoogleCalendarConnection | None) -> dict[str, Any]:
    return {
        "configured": configured(),
        "connected": conn is not None and conn.status == "active",
        "needs_reconnect": conn is not None and conn.status != "active",
        "email": conn.google_email if conn else None,
        "redirect_uri": redirect_uri(),
    }


async def _post_token(form: dict[str, str]) -> dict[str, Any]:
    async with _client() as client:
        resp = await client.post(TOKEN_URL, data=form)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or not isinstance(body, dict):
        error = str(body.get("error", "")) if isinstance(body, dict) else ""
        raise AppError(
            "google.token_error", "Google did not accept the request.", 502, details={"google_error": error[:60]}
        )
    return dict(body)


def _expiry(body: dict[str, Any]) -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=int(body.get("expires_in") or 3600))


async def complete_connection(session: AsyncSession, *, code: str, state: str) -> uuid.UUID | None:
    """Finish the flow from Google's redirect. Returns the agent id the user started from (for the dashboard
    redirect), or None. Raises `AppError` for anything wrong; the caller turns that into a friendly redirect."""
    data = read_state(state)
    org_id, user_id = uuid.UUID(data["o"]), uuid.UUID(data["u"])
    membership = (
        await session.execute(
            select(Membership).where(Membership.organization_id == org_id, Membership.user_id == user_id)
        )
    ).scalar_one_or_none()
    user = await session.get(User, user_id)
    if membership is None or user is None:
        raise AppError("google.forbidden", "You are no longer a member of this workspace.", 403)
    rbac.require_permission(membership.role, rbac.TOOLS_MANAGE)
    if not configured():
        raise AppError("google.not_configured", "Google Calendar is not set up on this server.", 501)

    tokens = await _post_token(
        {
            "code": code,
            "client_id": str(settings.google_client_id),
            "client_secret": str(settings.google_client_secret),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        }
    )
    refresh = str(tokens.get("refresh_token") or "")
    access = str(tokens.get("access_token") or "")
    if not refresh or not access:
        raise AppError("google.no_refresh_token", "Google did not return offline access. Try connecting again.", 400)

    email: str | None = None
    try:
        async with _client() as client:
            info = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
        if info.status_code < 300:
            email = str(info.json().get("email") or "")[:320] or None
    except (httpx.HTTPError, ValueError):
        email = None  # only a label; the connection is valid without it

    conn = await get_connection(session, org_id)
    if conn is None:
        conn = GoogleCalendarConnection(organization_id=org_id, refresh_token_encrypted=encrypt(refresh))
        session.add(conn)
    conn.refresh_token_encrypted = encrypt(refresh)
    conn.access_token_encrypted = encrypt(access)
    conn.access_token_expires_at = _expiry(tokens)
    conn.google_email = email
    conn.connected_by = user_id
    conn.status = "active"
    await session.flush()
    log.info("google_calendar_connected", org=str(org_id))
    return uuid.UUID(data["a"]) if data.get("a") else None


async def disconnect(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """Forget the connection and ask Google to revoke the grant (best effort). True if there was one."""
    conn = await get_connection(session, org_id)
    if conn is None:
        return False
    refresh = decrypt(conn.refresh_token_encrypted)
    await session.delete(conn)
    await session.flush()
    try:
        async with _client() as client:
            await client.post(REVOKE_URL, data={"token": refresh})
    except httpx.HTTPError:
        log.warning("google_calendar_revoke_failed", org=str(org_id))
    log.info("google_calendar_disconnected", org=str(org_id))
    return True


# ── events ────────────────────────────────────────────────────────────────────────────


async def _access_token(conn: GoogleCalendarConnection) -> str | None:
    """A valid access token, refreshing when it is missing or about to expire. None if Google refuses the refresh
    (the grant was revoked or expired): the connection is then marked `needs_reconnect`."""
    now = dt.datetime.now(tz=dt.UTC)
    if (
        conn.access_token_encrypted
        and conn.access_token_expires_at
        and conn.access_token_expires_at > now + dt.timedelta(seconds=60)
    ):
        return decrypt(conn.access_token_encrypted)
    try:
        tokens = await _post_token(
            {
                "client_id": str(settings.google_client_id),
                "client_secret": str(settings.google_client_secret),
                "refresh_token": decrypt(conn.refresh_token_encrypted),
                "grant_type": "refresh_token",
            }
        )
    except AppError as exc:
        if (exc.details or {}).get("google_error") == "invalid_grant":
            conn.status = "needs_reconnect"
            log.warning("google_calendar_needs_reconnect", org=str(conn.organization_id))
        return None
    access = str(tokens.get("access_token") or "")
    if not access:
        return None
    conn.access_token_encrypted = encrypt(access)
    conn.access_token_expires_at = _expiry(tokens)
    return access


def _event_body(booking: Booking) -> dict[str, Any]:
    start = booking.start_at
    end = start + dt.timedelta(minutes=booking.duration_minutes)
    zone = settings.booking_timezone
    lines = [f"Customer: {booking.customer_name}", f"Contact: {booking.customer_contact}"]
    if booking.notes:
        lines.append(f"Notes: {booking.notes}")
    lines.append(f"Reference: {str(booking.id).split('-')[0].upper()} (booked by the Vicero assistant)")
    return {
        # A fixed id makes the insert idempotent: a retry gets 409 "already exists", not a second event.
        "id": booking.id.hex,
        "summary": f"Appointment: {booking.customer_name}",
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": zone},
        "end": {"dateTime": end.isoformat(), "timeZone": zone},
        "reminders": {"useDefault": True},
    }


async def _create_event(conn: GoogleCalendarConnection, booking: Booking) -> str | None:
    token = await _access_token(conn)
    if token is None:
        return None
    url = EVENTS_URL.format(calendar=conn.calendar_id)
    async with _client() as client:
        resp = await client.post(url, json=_event_body(booking), headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 401:  # token revoked early: one forced refresh
            conn.access_token_expires_at = None
            token = await _access_token(conn)
            if token is None:
                return None
            resp = await client.post(url, json=_event_body(booking), headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 409:  # same booking id already inserted
        return booking.id.hex
    if resp.status_code >= 300:
        log.warning("google_calendar_event_rejected", status=resp.status_code, booking=str(booking.id))
        return None
    try:
        return str(resp.json().get("id") or booking.id.hex)
    except ValueError:
        return booking.id.hex


async def mirror_booking(session: AsyncSession, booking: Booking) -> str | None:
    """Create the calendar event for `booking` if its organisation is connected. Returns the event id or None.

    Never raises and never delays the booking beyond `EVENT_BUDGET_SECONDS`: the booking is already saved, and the
    calendar is a convenience copy."""
    if booking.external_event_id:
        return booking.external_event_id
    try:
        conn = await get_connection(session, booking.organization_id)
        if conn is None or conn.status != "active" or not configured():
            return None
        event_id = await asyncio.wait_for(_create_event(conn, booking), timeout=EVENT_BUDGET_SECONDS)
        if event_id:
            booking.external_event_id = event_id
        return event_id
    except Exception as exc:
        log.warning("google_calendar_mirror_failed", booking=str(booking.id), error=type(exc).__name__)
        return None
