"""Channel adapter interface, secret handling, and the registry."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.crypto import decrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Channel

log = get_logger("channels")


@dataclass
class ContactProfile:
    """What a platform can tell us about the human sending a message.

    Every field is optional — platforms differ wildly in what they expose, and a
    partial profile is still better than a raw id in the inbox.
    """

    display_name: str | None = None
    avatar_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.display_name or self.avatar_url or self.extra)


@dataclass
class InboundMessage:
    external_user_id: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    #: Identity carried by the inbound payload itself, when the platform includes one.
    profile: ContactProfile | None = None


class BaseChannel:
    """One messaging channel type. Subclasses implement verify / parse_inbound / send."""

    type: str = ""
    #: config keys that hold secrets (encrypted at rest, masked in API responses).
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        # Tests set this to an httpx.MockTransport; production leaves it None.
        self.transport: httpx.AsyncBaseTransport | None = None

    # ── secrets ──────────────────────────────────────────────────────────────
    def secret(self, channel: Channel, field_name: str) -> str | None:
        raw = channel.config.get(field_name)
        if not raw:
            return None
        try:
            return decrypt(str(raw))
        except Exception:  # tolerate a plaintext value (dev convenience)
            return str(raw)

    def missing_secrets(self, channel: Channel) -> list[str]:
        return [f for f in self.secret_fields if not channel.config.get(f)]

    # ── inbound / outbound ───────────────────────────────────────────────────
    async def verify(
        self, channel: Channel, headers: Mapping[str, str], body: bytes, query: Mapping[str, str]
    ) -> bool:
        return True

    def parse_inbound(self, channel: Channel, payload: dict[str, Any]) -> InboundMessage | None:
        raise NotImplementedError

    async def send(self, channel: Channel, to: str, text: str) -> None:
        raise NotImplementedError

    def check_can_send(self, channel: Channel, *, last_inbound_at: dt.datetime | None) -> None:
        """Raise if the platform forbids a free-form send right now.

        Most platforms let you reply whenever; WhatsApp's 24-hour customer-service window
        does not. Keeping the rule on the adapter means callers stay channel-agnostic
        instead of growing `if channel == "whatsapp"` branches.
        """
        return None

    def ensure_active(self, channel: Channel) -> None:
        """Refuse to touch the provider for a channel that lost its connection.

        `needs_reconnect` / `disconnected` are set by token health checks, a disconnect, or Meta's
        deauthorize callback. Sending would only fail against a dead token, so say so plainly instead.
        """
        state = getattr(channel, "status", "active") or "active"
        if state == "needs_reconnect":
            raise AppError(
                "channel_needs_reconnect",
                "This channel lost its connection to Meta. Reconnect it from the agent's Channels tab.",
                409,
            )
        if state == "disconnected":
            raise AppError("channel_disconnected", "This channel is disconnected. Connect it again to send.", 409)

    async def fetch_profile(self, channel: Channel, external_id: str) -> ContactProfile | None:
        """Look up a sender's name/avatar out-of-band.

        Overridden by adapters whose platform exposes a profile API (Meta's Graph API
        does). Called only while a contact is still missing a name or avatar, so a
        chatty conversation doesn't hit the platform on every turn.
        """
        return None

    async def on_enable(self, channel: Channel, *, api_base: str) -> None:
        """Optional hook run when a channel is enabled (e.g. Telegram setWebhook)."""
        return None

    def _client(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=20.0, transport=self.transport, **kw)


CHANNELS: dict[str, BaseChannel] = {}


def register(channel: BaseChannel) -> BaseChannel:
    CHANNELS[channel.type] = channel
    return channel


def get_channel(channel_type: str) -> BaseChannel | None:
    return CHANNELS.get(channel_type)
