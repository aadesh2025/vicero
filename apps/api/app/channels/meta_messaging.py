"""Shared implementation for Meta's unified Messenger Platform.

Facebook Messenger and Instagram Messaging are the same product behind the same Graph
API: identical webhook envelope (``entry[].messaging[]``), identical Send API
(``POST /me/messages``), identical signature handshake. Only the webhook ``object``
type, the token, and the profile field names differ — so the differences live in the
two thin subclasses and everything else lives here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.channels.base import BaseChannel, ContactProfile, InboundMessage
from app.channels.meta_graph import graph_base
from app.channels.meta_signature import verify_challenge, verify_signature
from app.core.logging import get_logger

log = get_logger("channels.meta")

GRAPH_BASE = "https://graph.facebook.com/v19.0"


class MetaMessagingChannel(BaseChannel):
    """Base for Facebook Messenger / Instagram DM adapters."""

    secret_fields = ("page_access_token", "app_secret")
    #: The ``object`` value Meta stamps on this surface's webhook deliveries.
    webhook_object: str = ""
    #: Graph fields to request when resolving a sender's profile.
    profile_fields: tuple[str, ...] = ()

    # ── inbound ──────────────────────────────────────────────────────────────
    async def verify(
        self, channel: Any, headers: Mapping[str, str], body: bytes, query: Mapping[str, str]
    ) -> bool:
        return verify_signature(self.secret(channel, "app_secret"), headers, body)

    def verify_challenge(self, channel: Any, query: Mapping[str, str]) -> str | None:
        return verify_challenge(channel, query)

    def parse_inbound(self, channel: Any, payload: dict[str, Any]) -> InboundMessage | None:
        if payload.get("object") not in (self.webhook_object, None):
            return None  # a delivery for a different Meta surface on the same app
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for event in entry.get("messaging") or []:
                if not isinstance(event, dict):
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue  # read receipt, delivery confirmation, postback, …
                if message.get("is_echo"):
                    continue  # our own outbound, echoed back to us
                text = message.get("text")
                sender = (event.get("sender") or {}).get("id")
                if not text or sender is None:
                    continue
                return InboundMessage(external_user_id=str(sender), text=str(text), raw=payload)
        return None

    # ── outbound ─────────────────────────────────────────────────────────────
    def check_can_send(self, channel: Any, *, last_inbound_at: Any) -> None:
        self.ensure_active(channel)

    async def send(self, channel: Any, to: str, text: str) -> None:
        self.ensure_active(channel)
        token = self.secret(channel, "page_access_token")
        if not token:
            log.warning("meta_send_skipped_no_token", channel_type=self.type, channel=str(channel.id))
            return
        async with self._client(base_url=graph_base()) as client:
            await client.post(
                "/me/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "recipient": {"id": to},
                    "messaging_type": "RESPONSE",
                    "message": {"text": text},
                },
            )

    # ── identity ─────────────────────────────────────────────────────────────
    async def fetch_profile(self, channel: Any, external_id: str) -> ContactProfile | None:
        """Resolve a sender's name + photo through the Graph API.

        Meta only exposes profiles for people who have messaged the page, and only with
        the right permission granted — a 400 here is normal, not an error worth raising.
        """
        token = self.secret(channel, "page_access_token")
        if not token:
            log.warning(
                "meta_profile_skipped_no_token", channel_type=self.type, channel=str(channel.id)
            )
            return None
        async with self._client(base_url=graph_base()) as client:
            resp = await client.get(
                f"/{external_id}",
                params={"fields": ",".join(self.profile_fields)},
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            log.info(
                "meta_profile_unavailable",
                channel_type=self.type,
                status=resp.status_code,
            )
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return self.profile_from_graph(data)

    def profile_from_graph(self, data: dict[str, Any]) -> ContactProfile:
        raise NotImplementedError
