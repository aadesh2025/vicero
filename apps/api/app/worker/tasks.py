"""Celery tasks. Each task owns its own committing DB session (separate from request scope)."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Coroutine
from typing import TypeVar

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.logging import get_logger
from app.rag.ingest import ingest_document
from app.worker.celery_app import celery_app
from app.worker.rollup import rollup_org

log = get_logger("worker.tasks")

_T = TypeVar("_T")

# Celery runs each task on a *fresh* ``asyncio.run`` event loop. A pooled asyncpg
# connection bound to a previous task's (now-closed) loop raises "Event loop is closed"
# / "'NoneType' object has no attribute 'send'" when reused. A dedicated worker engine
# with NullPool never reuses a connection across loops: every session opens and closes
# its own connection on the current loop. Kept separate from the API's pooled engine.
_worker_engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=False)
SessionFactory = async_sessionmaker(bind=_worker_engine, expire_on_commit=False, autoflush=False)


def _run(coro: Coroutine[object, object, _T]) -> _T:
    """Run one task coroutine on a fresh event loop (NullPool means no cross-loop reuse)."""
    return asyncio.run(coro)


async def _run_ingest(document_id: uuid.UUID) -> str:
    async with SessionFactory() as session:
        document = await ingest_document(session, document_id)
        await session.commit()
        return document.status


@celery_app.task(name="rag.ingest_document", bind=True, max_retries=2)  # type: ignore[untyped-decorator]
def ingest_document_task(self: object, document_id: str) -> str:
    """Parse/chunk/embed/store a document. Idempotent: re-running replaces its chunks."""
    log.info("ingest_task_start", document_id=document_id)
    return _run(_run_ingest(uuid.UUID(document_id)))


def enqueue_document_ingestion(document_id: uuid.UUID) -> None:
    """Enqueue ingestion. Indirection kept small so services/tests can stub it out."""
    ingest_document_task.delay(str(document_id))


async def _run_rollup(org_id: str, date_str: str) -> dict[str, int]:
    async with SessionFactory() as session:
        result = await rollup_org(session, uuid.UUID(org_id), dt.date.fromisoformat(date_str))
        await session.commit()
        return result


@celery_app.task(name="usage.rollup_org")  # type: ignore[untyped-decorator]
def rollup_org_task(org_id: str, date_str: str) -> dict[str, int]:
    """Roll up one org's usage for a date into usage_records + refresh its quota."""
    return _run(_run_rollup(org_id, date_str))


async def _run_delivery(delivery_id: str) -> bool:
    from app.webhooks.dispatch import deliver_delivery

    async with SessionFactory() as session:
        ok = await deliver_delivery(session, uuid.UUID(delivery_id))
        await session.commit()
        return ok


@celery_app.task(name="webhooks.deliver", bind=True, max_retries=5)  # type: ignore[untyped-decorator]
def deliver_webhook_task(self: object, delivery_id: str) -> bool:
    """Attempt one webhook delivery; Celery retries with backoff on failure."""
    ok = _run(_run_delivery(delivery_id))
    if not ok:
        raise self.retry(countdown=min(3600, 30), exc=RuntimeError("delivery not confirmed"))  # type: ignore[attr-defined]
    return ok


async def _run_send_email(to: str, subject: str, body: str, html_body: str | None) -> None:
    from app.core.email import EmailMessage, get_email_backend

    await get_email_backend().send(
        EmailMessage(to=to, subject=subject, body=body, html_body=html_body)
    )


@celery_app.task(name="email.send", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def send_email_task(
    self: object, to: str, subject: str, body: str, html_body: str | None = None
) -> None:
    """Deliver one email out-of-band so a slow relay never blocks an HTTP request.

    Retries with backoff: a relay that is briefly down shouldn't lose someone's invitation.
    """
    try:
        _run(_run_send_email(to, subject, body, html_body))
    except Exception as exc:
        log.warning("email_task_failed", to=to, subject=subject, error=str(exc))
        raise self.retry(countdown=60, exc=exc) from exc  # type: ignore[attr-defined]


async def _run_workflow_execution(run_id: str, resume_decision: str | None, resume: bool = False) -> str:
    from app.workflows.service import execute_queued_run

    async with SessionFactory() as session:
        status = await execute_queued_run(
            session, uuid.UUID(run_id), resume_decision=resume_decision, resume=resume
        )
        await session.commit()
        return status


@celery_app.task(name="workflows.run", bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def run_workflow_task(self: object, run_id: str) -> str:
    """Executes a workflow run from its start node (docs/17 Phase 2 item 2). No retry: a
    partial run's state already lives in `WorkflowRun`/`WorkflowStep`, so a bare retry would
    re-run completed side effects (a tool call, an agent turn) rather than resume past them —
    resuming is `resume_workflow_run_task`'s job, driven by an explicit approval decision, not
    an automatic retry policy guessing what to do with a half-finished graph."""
    log.info("workflow_run_task_start", run_id=run_id)
    return _run(_run_workflow_execution(run_id, resume_decision=None))


@celery_app.task(name="workflows.resume", bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def resume_workflow_run_task(self: object, run_id: str, decision: str) -> str:
    log.info("workflow_resume_task_start", run_id=run_id, decision=decision)
    return _run(_run_workflow_execution(run_id, resume_decision=decision))


@celery_app.task(name="workflows.resume_delay", bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def resume_delayed_workflow_task(self: object, run_id: str) -> str:
    """Fires at the `eta` a `delay` node computed (docs/17 Phase 2 gap-closure item 2,
    ADR-076) — scheduled via `apply_async(eta=...)`, never a blocking sleep on any worker.
    No retry, same reasoning as `run_workflow_task`: `execute_queued_run` itself guards against
    firing on a run that moved on (e.g. was cancelled) while it was waiting."""
    log.info("workflow_delay_resume_task_start", run_id=run_id)
    return _run(_run_workflow_execution(run_id, resume_decision=None, resume=True))


async def _run_finalize_turn(
    conv_data: dict[str, object], result_data: dict[str, object], latency_ms: int, first_text: str
) -> str:
    from app.chat.runtime import TurnResult
    from app.models import Conversation
    from app.modules.conversations.service import _finalize_turn, _persist_user_message

    async with SessionFactory() as session:
        conv = await session.get(Conversation, uuid.UUID(str(conv_data["id"])))
        if conv is None:
            # The request that generated this turn was cancelled before its OWN flush ever
            # became durable — not just the assistant reply, the conversation row and its
            # opening user message too (both flushed, never committed, in the same
            # now-abandoned transaction; see `finalize_turn_task`'s docstring). Recreate them
            # using the SAME id the request already picked: `UUIDPrimaryKey` assigns UUIDv7
            # client-side at construction, before any DB round trip, so `conv_data["id"]` is
            # stable regardless of whether the original row ever committed.
            conv = Conversation(
                id=uuid.UUID(str(conv_data["id"])),
                organization_id=uuid.UUID(str(conv_data["organization_id"])),
                agent_id=uuid.UUID(str(conv_data["agent_id"])),
                channel=str(conv_data["channel"]),
                channel_user_id=conv_data.get("channel_user_id"),
                contact_id=(
                    uuid.UUID(str(conv_data["contact_id"])) if conv_data.get("contact_id") else None
                ),
                external_id=conv_data.get("external_id"),
                status="active",
            )
            session.add(conv)
            await session.flush()
            await _persist_user_message(session, conv, first_text)
            await session.flush()
        result = TurnResult(**result_data)  # type: ignore[arg-type]
        if not result.content and not result.error:
            # Dropped before the model produced anything: keep the conversation and the
            # visitor's message (restored above if needed), but don't write an empty reply.
            await session.commit()
            return "no_reply"
        msg = await _finalize_turn(session, conv, result, latency_ms, first_text)
        await session.commit()
        return str(msg.id)


@celery_app.task(name="chat.finalize_turn", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def finalize_turn_task(
    self: object, conv_data: dict[str, object], result_data: dict[str, object], latency_ms: int, first_text: str
) -> str:
    """Persists a chat turn's assistant reply after the request that generated it is already
    gone (RISK-REGISTER R14) — a client disconnecting mid-stream (tab closed, navigated away,
    or simply reading only the first SSE event, all proven common by `infra/perf/load_test.py`)
    must not silently drop the reply from conversation history. If the disconnect landed EARLY
    enough that even the conversation/user-message flush from the same request never became
    durable either, this recreates both from `conv_data` before appending the reply — see
    `_run_finalize_turn`.

    Enqueued rather than persisted inline in the request's own cancellation handler, and this
    was not the first thing tried: an in-process retry using the request's own session, even
    wrapped in `asyncio.shield()`, kept failing — anyio's cancel scope re-raises
    `CancelledError` at EVERY subsequent checkpoint until the scope is actually exited, not
    just once, so every further `await` inside the same cancelled request (including a
    `session.rollback()` meant to recover from it) was cancelled again in turn. A worker task
    runs in a different process entirely, outside that cancel scope, with its own committing
    session (`SessionFactory`, this module's docstring) — exactly the same reason `queue_email`
    hands SMTP off to a task instead of sending inline from a request that might not still be
    there to wait for it.
    """
    try:
        return _run(_run_finalize_turn(conv_data, result_data, latency_ms, first_text))
    except Exception as exc:
        log.warning("finalize_turn_task_failed", conversation_id=conv_data.get("id"), error=str(exc))
        raise self.retry(countdown=5, exc=exc) from exc  # type: ignore[attr-defined]


async def _run_sweep() -> int:
    from app.webhooks.dispatch import sweep_due_deliveries

    async with SessionFactory() as session:
        n = await sweep_due_deliveries(session)
        await session.commit()
        return n


@celery_app.task(name="webhooks.sweep_pending")  # type: ignore[untyped-decorator]
def sweep_pending_webhooks_task() -> int:
    """Periodic (Celery beat) safety-net: re-enqueue due `pending` webhook deliveries."""
    return _run(_run_sweep())


@celery_app.task(name="disposable.refresh", bind=True, max_retries=2)  # type: ignore[untyped-decorator]
def refresh_disposable_list_task(self: object) -> int:
    """Weekly (Celery beat): refresh the maintained throwaway-domain list into Redis (ADR-091).

    A failed download leaves the previous copy in place and is retried, then left for next week.
    """
    from app.modules.auth import disposable

    try:
        return _run(disposable.refresh())
    except Exception as exc:
        log.warning("disposable_refresh_failed", error=str(exc))
        raise self.retry(countdown=900, exc=exc) from exc  # type: ignore[attr-defined]


async def _run_trial_sweep() -> dict[str, int]:
    from app.worker.trial import sweep

    async with SessionFactory() as session:
        sent = await sweep(session)
        await session.commit()
        return sent


@celery_app.task(name="trial.sweep")  # type: ignore[untyped-decorator]
def trial_sweep_task() -> dict[str, int]:
    """Hourly (Celery beat): send each free-trial lifecycle email that is due, once (docs/18 §8)."""
    return _run(_run_trial_sweep())
