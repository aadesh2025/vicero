"""Async pub/sub for WebSocket fan-out — local queues bridged over Redis for multi-node.

Callers use the same three methods regardless of transport: ``subscribe`` / ``unsubscribe``
(synchronous, return/take a local ``asyncio.Queue``) and ``await publish(topic, event)``.

Delivery model (ADR-028):
- Every ``publish`` is delivered to **local** subscribers immediately (single-node correctness,
  and it works even before/without Redis).
- When Redis is connected, ``publish`` also forwards the event to Redis tagged with this
  process's node id. A background reader delivers events from **other** nodes to local queues,
  skipping our own (so a message is never delivered twice on the publishing node).

So on one node it behaves exactly like the old in-process hub; across N API replicas an operator
reply on node A reaches a widget socket on node B. If Redis is unavailable the hub degrades to
single-node in-process delivery (with a logged warning) rather than failing.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from app.core.logging import get_logger

log = get_logger("realtime.hub")

# One Redis channel carries all realtime events; the topic travels inside the payload.
REDIS_CHANNEL = "vicero:realtime"


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._node = uuid.uuid4().hex
        self._redis: Any | None = None
        self._pubsub: Any | None = None
        self._reader: asyncio.Task[None] | None = None

    # ── Subscription (local, synchronous — unchanged interface) ──────────────────────
    def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subs.setdefault(topic, set()).add(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subs.get(topic)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subs.pop(topic, None)

    def _deliver_local(self, topic: str, event: dict[str, Any]) -> None:
        for queue in list(self._subs.get(topic, set())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a slow subscriber shouldn't block the publisher

    # ── Publish (local now, plus Redis fan-out to other nodes) ───────────────────────
    async def publish(self, topic: str, event: dict[str, Any]) -> None:
        self._deliver_local(topic, event)
        if self._redis is not None:
            try:
                payload = json.dumps({"node": self._node, "topic": topic, "event": event}, default=str)
                await self._redis.publish(REDIS_CHANNEL, payload)
            except Exception as exc:  # best-effort cross-node fan-out
                log.warning("hub_redis_publish_failed", error=str(exc))

    # ── Lifecycle (wired into the app lifespan) ──────────────────────────────────────
    async def connect(self, redis_url: str) -> None:
        try:
            from redis.asyncio import from_url

            self._redis = from_url(redis_url)
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(REDIS_CHANNEL)
            self._reader = asyncio.create_task(self._read_loop())
            log.info("hub_redis_connected", node=self._node)
        except Exception as exc:
            self._redis = None
            self._pubsub = None
            log.warning("hub_redis_unavailable", error=str(exc), effect="single-node in-process only")

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
            self._reader = None
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(REDIS_CHANNEL)
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    async def _read_loop(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                except (ValueError, TypeError, KeyError):
                    continue
                if data.get("node") == self._node:
                    continue  # our own publish — already delivered locally
                topic = data.get("topic")
                event = data.get("event")
                if isinstance(topic, str) and isinstance(event, dict):
                    self._deliver_local(topic, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep the process alive if the reader dies
            log.warning("hub_read_loop_error", error=str(exc))


hub = Hub()


def conv_topic(conversation_id: uuid.UUID | str) -> str:
    return f"conv:{conversation_id}"


def inbox_topic(org_id: uuid.UUID | str) -> str:
    return f"inbox:{org_id}"
