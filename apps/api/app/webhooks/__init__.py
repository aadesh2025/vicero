"""Outbound webhooks: endpoints, signed delivery with retry, and the event catalog (Phase 15)."""

# The full event catalog emitted across the app (docs/04 §Webhooks).
EVENT_CATALOG = (
    "message.created",
    "conversation.created",
    "conversation.closed",
    "handoff.requested",
    "handoff.resolved",
    "document.ready",
    "document.failed",
    "tool.run",
    "tool.called",
    "variables.captured",
    "conversation.ended",
    "lead.captured",
    "usage.threshold",
    "channel.needs_reconnect",
)
