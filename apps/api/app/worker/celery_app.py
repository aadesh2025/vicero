"""Celery application. Broker/result backend = Redis (docs/02)."""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_ready

from app.core.config import settings

celery_app = Celery(
    "vicero",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
    # Periodic jobs (run a beat process alongside the worker:
    #   celery -A app.worker.celery_app beat)
    beat_schedule={
        "webhook-retry-sweep": {
            # Re-enqueue `pending` webhook deliveries whose next_retry_at is due — the safety
            # net for retries lost while the worker/broker was down (see dispatch.sweep_due_deliveries).
            "task": "webhooks.sweep_pending",
            "schedule": settings.webhook_sweep_interval_seconds,
        },
        "disposable-list-refresh": {
            # The maintained throwaway-email domain list, weekly (modules/auth/disposable.py).
            "task": "disposable.refresh",
            "schedule": 7 * 86400.0,
        },
        "mcp-tools-refresh": {
            # MCP servers whose discovered tool list is over a day old (tools/mcp_service.stale_server_ids).
            "task": "mcp.refresh_stale",
            "schedule": 3600.0,
        },
        "inbound-hook-events-retention": {
            # Hook call logs older than 30 days are deleted (docs/28 T3.3).
            "task": "inbound_hooks.retention",
            "schedule": 86400.0,
        },
        "automation-runs-pull": {
            # Backup for n8n's signed push: fills gaps from n8n's execution list (registered workflows only).
            "task": "automations.pull",
            "schedule": 300.0,
        },
        "automation-runs-retention": {
            # Runs older than 30 days are deleted (docs/26).
            "task": "automations.retention",
            "schedule": 86400.0,
        },
        "media-retention": {
            # Customer photos older than INBOUND_MEDIA_RETENTION_DAYS are deleted (docs/26).
            "task": "media.retention",
            "schedule": 86400.0,
        },
        "meta-token-health": {
            # One-click Meta channels: is each stored token still valid? (channels/meta_health.py)
            "task": "channels.meta_health",
            "schedule": 86400.0,
        },
        "keep-warm": {
            # Embedding model, DB and the Groq/Meta connections stay loaded between customer messages (ADR-122).
            "task": "warmup.keep_warm",
            "schedule": settings.warmup_interval_seconds,
        },
        "trial-lifecycle-sweep": {
            # Free-trial emails (3 days / 1 day left, ended, 80% / 100% of messages). Idempotent:
            # each is claimed atomically per workspace, so running more often is harmless.
            "task": "trial.sweep",
            "schedule": 3600.0,
        },
    },
)


@worker_ready.connect  # type: ignore[untyped-decorator]
def _warm_on_start(**_: object) -> None:
    """Load the embedding model and open the connections before the first customer message arrives."""
    if settings.celery_task_always_eager or not settings.warmup_enabled:
        return
    celery_app.send_task("warmup.keep_warm")
