"""Celery application. Broker/result backend = Redis (docs/02)."""

from __future__ import annotations

from celery import Celery

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
        "trial-lifecycle-sweep": {
            # Free-trial emails (3 days / 1 day left, ended, 80% / 100% of messages). Idempotent:
            # each is claimed atomically per workspace, so running more often is harmless.
            "task": "trial.sweep",
            "schedule": 3600.0,
        },
    },
)
