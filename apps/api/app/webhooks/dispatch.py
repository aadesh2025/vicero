"""Webhook event emission + signed, retryable delivery (docs/07 §4)."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import time
import uuid
from typing import Any

import httpx

from app.core.egress import EgressBlocked, EgressError, safe_request
from app.core.logging import get_logger
from app.models import WebhookDelivery, WebhookEndpoint

log = get_logger("webhooks")

MAX_ATTEMPTS = 5


def sign(secret: str, body: bytes) -> tuple[str, str]:
    """Return (timestamp, hex HMAC-SHA256 of `timestamp.body`) for outbound webhook signing."""
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256)
    return ts, mac.hexdigest()


def _backoff(attempts: int) -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=min(3600, 2 ** (attempts + 3)))


_SECRET_KEY = re.compile(r"authorization|cookie|token|secret|password|api[-_]?key|bearer", re.IGNORECASE)
_MAX_VALUE_CHARS = 300


def sanitize_payload(value: Any, depth: int = 0) -> Any:
    """What may leave the platform in an event: values under secret-looking keys are masked and long text is
    cut. Tool arguments and results are customer data at best and credentials at worst, so events carry a
    redacted view of them, never the raw object (docs/28 section 9)."""
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {
            str(k): ("••••" if _SECRET_KEY.search(str(k)) else sanitize_payload(v, depth + 1)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(v, depth + 1) for v in value[:50]]
    if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
        return value[:_MAX_VALUE_CHARS] + "..."
    return value


async def emit_event(
    session: Any, org_id: uuid.UUID, event: str, data: dict[str, Any], *, agent_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    """Create pending deliveries for every enabled endpoint subscribed to `event`; enqueue them.

    An endpoint with an `agent_id` only receives that agent's events; an org-wide endpoint (NULL) receives
    every event, whatever agent it came from. `agent_id` is also read from `data["agent_id"]` when not given.
    Best-effort: never raises into the calling request path.
    """
    from sqlalchemy import select

    created: list[uuid.UUID] = []
    try:
        stmt = select(WebhookEndpoint).where(
            WebhookEndpoint.organization_id == org_id, WebhookEndpoint.enabled.is_(True)
        )
        endpoints = (await session.execute(stmt)).scalars().all()
        if agent_id is None and data.get("agent_id"):
            try:
                agent_id = uuid.UUID(str(data["agent_id"]))
            except ValueError:
                agent_id = None
        for ep in endpoints:
            if event not in ep.events and "*" not in ep.events:
                continue
            if ep.agent_id is not None and ep.agent_id != agent_id:
                continue
            delivery = WebhookDelivery(
                webhook_endpoint_id=ep.id,
                event=event,
                payload={"event": event, "org_id": str(org_id), "data": data},
                status="pending",
            )
            session.add(delivery)
            await session.flush()
            created.append(delivery.id)
            _enqueue(delivery.id)
    except Exception as exc:  # webhooks must never break the app
        log.warning("emit_event_failed", event_name=event, error=str(exc))
    return created


def _enqueue(delivery_id: uuid.UUID) -> None:
    try:
        from app.worker.tasks import deliver_webhook_task

        deliver_webhook_task.delay(str(delivery_id))
    except Exception:  # broker down → delivery stays pending for the retry sweep
        pass


async def sweep_due_deliveries(session: Any, *, limit: int = 200) -> int:
    """Re-enqueue every `pending` delivery whose `next_retry_at` is due.

    The per-task Celery retry handles the common case; this periodic sweep (Celery beat) is the
    safety net for retries that were lost because the worker/broker was down at retry time, so a
    failed webhook never gets stuck `pending` forever.
    """
    from sqlalchemy import select

    now = dt.datetime.now(tz=dt.UTC)
    stmt = (
        select(WebhookDelivery.id)
        .where(
            WebhookDelivery.status == "pending",
            WebhookDelivery.next_retry_at.is_not(None),
            WebhookDelivery.next_retry_at <= now,
        )
        .order_by(WebhookDelivery.next_retry_at)
        .limit(limit)
    )
    ids = list((await session.execute(stmt)).scalars().all())
    for delivery_id in ids:
        _enqueue(delivery_id)
    if ids:
        log.info("webhook_sweep", requeued=len(ids))
    return len(ids)


async def deliver_delivery(
    session: Any, delivery_id: uuid.UUID, *, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    """One delivery attempt: sign + POST, update the delivery row. Returns True on 2xx/3xx."""
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        return False
    endpoint = await session.get(WebhookEndpoint, delivery.webhook_endpoint_id)
    if endpoint is None or not endpoint.enabled:
        delivery.status = "failed"
        return False

    body = json.dumps(delivery.payload).encode()
    headers = {"Content-Type": "application/json", "X-Vicero-Event": delivery.event}
    if endpoint.secret:
        ts, signature = sign(endpoint.secret, body)
        headers["X-Vicero-Signature"] = signature
        headers["X-Vicero-Timestamp"] = ts

    delivery.attempts += 1
    try:
        resp = await safe_request(
            "POST",
            endpoint.url,
            headers=headers,
            content=body,
            timeout_s=15.0,
            max_bytes=64_000,
            transport=transport,
        )
        delivery.response_status = resp.status_code
        if resp.status_code < 400:
            delivery.status = "delivered"
            delivery.next_retry_at = None
            return True
    except EgressBlocked as exc:
        # SSRF guard / bad URL / a name that now resolves inside the network: permanent, not retried.
        log.warning("webhook_destination_refused", delivery=str(delivery_id), reason=exc.reason)
        delivery.status = "failed"
        delivery.next_retry_at = None
        return False
    except EgressError as exc:
        log.warning("webhook_delivery_error", delivery=str(delivery_id), error=str(exc))

    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
        delivery.next_retry_at = None
    else:
        delivery.status = "pending"
        delivery.next_retry_at = _backoff(delivery.attempts)
    return False
