"""Per-turn stage timings for channel inbound turns (event `meta_inbound_timing`).

A 28 s reply to "Hi" had no single log line that said where the time went; the answer had to be pieced
together from three containers. This keeps one `TurnTimings` in a context variable for the duration of a
turn, lets any stage report into it with `stage("name")` / `add("name", ms)`, and emits **one** log line at
the end with every stage and the total.

Outside a turn that called `begin()` (the web widget, tests) every call is a no-op, so call sites need no
`if timings:` guards. Stages that run concurrently (the L2/L3 guards overlap retrieval) each report their
own duration, so the stages can add up to more than `total_ms`: that is the point of overlapping them.

Only stage names and millisecond counts are logged. Never message text, ids of people or tokens.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from app.core.logging import get_logger

log = get_logger("chat.timing")


class TurnTimings:
    def __init__(self, received_at: float | None = None) -> None:
        self.started = time.perf_counter()
        self.stages: dict[str, int] = {}
        # Wall clock of the webhook's arrival (set by the API process); the queue wait is task start minus this.
        self.received_at = received_at
        if received_at is not None:
            self.stages["webhook_to_task_ms"] = max(0, int((time.time() - received_at) * 1000))

    def add(self, name: str, ms: float) -> None:
        key = f"{name}_ms"
        self.stages[key] = self.stages.get(key, 0) + int(ms)

    def total_ms(self) -> int:
        since_task = int((time.perf_counter() - self.started) * 1000)
        return since_task + self.stages.get("webhook_to_task_ms", 0)


_current: ContextVar[TurnTimings | None] = ContextVar("turn_timings", default=None)


def begin(received_at: float | None = None) -> tuple[TurnTimings, Token[TurnTimings | None]]:
    timings = TurnTimings(received_at)
    return timings, _current.set(timings)


def end(timings: TurnTimings, token: Token[TurnTimings | None], **fields: object) -> None:
    """Emit the one log line for the turn and detach it."""
    _current.reset(token)
    total = timings.total_ms()
    stages = dict(timings.stages)
    # The slowest *measured stage*, so an operator reads the answer off the line instead of computing it.
    # The queue wait counts: a stuck worker is a stage too.
    slowest = max(stages, key=lambda k: stages[k]) if stages else None
    log.info("meta_inbound_timing", total_ms=total, slowest=slowest, **stages, **fields)


def add(name: str, ms: float) -> None:
    timings = _current.get()
    if timings is not None:
        timings.add(name, ms)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time a block. Records even when the block raises, so a failing stage still shows its cost."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        add(name, (time.perf_counter() - t0) * 1000)
