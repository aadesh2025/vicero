"""WhatsApp (Meta Cloud API) channel adapter (docs/07 §2).

Meta only allows free-form text within 24 hours of the customer's last inbound message
("the customer-service window"). Outside it, a text send is rejected with error 131047 and
the message simply never arrives — so the window is enforced here, before sending, and the
failure is surfaced rather than swallowed. Re-opening a conversation outside the window
requires a pre-approved *template*, which is a Meta-side registration Vicero can't do
for you: operators enter their approved template names on the channel config.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

import httpx

from app.channels.base import BaseChannel, ContactProfile, InboundMessage, register
from app.channels.meta_signature import verify_challenge, verify_signature
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("channels.whatsapp")

#: Meta's customer-service window. Free-form text is only deliverable inside it.
WINDOW = dt.timedelta(hours=24)

#: "Message failed to send because more than 24 hours have passed since the customer
#: last replied to this number." — the server-side counterpart to our own clock check.
WINDOW_CLOSED_CODE = 131047


class WhatsAppWindowClosed(AppError):
    """Free-form send attempted outside Meta's 24-hour customer-service window."""

    def __init__(self, closed_since: dt.datetime | None = None) -> None:
        super().__init__(
            "whatsapp_window_closed",
            "WhatsApp only delivers free-form messages within 24 hours of the customer's "
            "last message. Send an approved template to re-open the conversation.",
            409,
            details={"last_inbound_at": closed_since.isoformat() if closed_since else None},
        )


def window_closes_at(last_inbound_at: dt.datetime | None) -> dt.datetime | None:
    return last_inbound_at + WINDOW if last_inbound_at else None


def window_open(last_inbound_at: dt.datetime | None, *, now: dt.datetime | None = None) -> bool:
    """No inbound message ever ⇒ closed: we've no consent to open a conversation."""
    if last_inbound_at is None:
        return False
    return (now or dt.datetime.now(tz=dt.UTC)) - last_inbound_at < WINDOW


def configured_templates(channel: Any) -> list[str]:
    """Approved template names from the channel config.

    Accepts a list or a comma-separated string — the connect dialog collects plain text,
    and an operator pasting "a, b" shouldn't produce one absurd template named "a, b".
    """
    raw = channel.config.get("templates") if channel is not None else None
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


class WhatsAppChannel(BaseChannel):
    type = "whatsapp"
    secret_fields = ("access_token", "app_secret")

    async def verify(
        self, channel: Any, headers: Mapping[str, str], body: bytes, query: Mapping[str, str]
    ) -> bool:
        return verify_signature(self.secret(channel, "app_secret"), headers, body)

    def verify_challenge(self, channel: Any, query: Mapping[str, str]) -> str | None:
        """Meta's GET webhook verification handshake → return hub.challenge on success."""
        return verify_challenge(channel, query)

    def parse_inbound(self, channel: Any, payload: dict[str, Any]) -> InboundMessage | None:
        try:
            value = payload["entry"][0]["changes"][0]["value"]
            msg = value["messages"][0]
            sender = msg["from"]
            text = msg["text"]["body"]
        except (KeyError, IndexError, TypeError):
            return None
        return InboundMessage(
            external_user_id=str(sender),
            text=str(text),
            raw=payload,
            profile=self._profile(value, str(sender)),
        )

    @staticmethod
    def _profile(value: dict[str, Any], sender: str) -> ContactProfile:
        """WhatsApp ships the contact's profile name with the message.

        There is no photo endpoint on the Cloud API, so ``avatar_url`` stays null — a
        platform limitation, not a gap to fill in later.
        """
        name: str | None = None
        for contact in value.get("contacts") or []:
            if isinstance(contact, dict) and (contact.get("profile") or {}).get("name"):
                name = str(contact["profile"]["name"])
                break
        return ContactProfile(display_name=name, extra={"phone": sender})

    # ── sending ──────────────────────────────────────────────────────────────
    def check_can_send(self, channel: Any, *, last_inbound_at: dt.datetime | None) -> None:
        """Refuse a free-form send Meta would drop, so the operator finds out now."""
        if not window_open(last_inbound_at):
            raise WhatsAppWindowClosed(last_inbound_at)

    async def send(self, channel: Any, to: str, text: str) -> None:
        token = self.secret(channel, "access_token")
        phone_number_id = channel.config.get("phone_number_id")
        if not token or not phone_number_id:
            log.warning("whatsapp_send_skipped_missing_config", channel=str(channel.id))
            return
        await self._post(
            channel,
            token,
            phone_number_id,
            {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}},
        )

    async def send_template(
        self,
        channel: Any,
        to: str,
        template_name: str,
        params: list[str] | None = None,
        language: str = "en_US",
    ) -> None:
        """Send a pre-approved template — the only way to message outside the window.

        Positional body parameters only; that covers the common
        "Hi {{1}}, your order {{2}} shipped" shape without modelling Meta's full
        header/button component tree.
        """
        token = self.secret(channel, "access_token")
        phone_number_id = channel.config.get("phone_number_id")
        if not token or not phone_number_id:
            log.warning("whatsapp_template_skipped_missing_config", channel=str(channel.id))
            return
        template: dict[str, Any] = {"name": template_name, "language": {"code": language}}
        if params:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            ]
        await self._post(
            channel,
            token,
            phone_number_id,
            {"messaging_product": "whatsapp", "to": to, "type": "template", "template": template},
        )

    async def _post(self, channel: Any, token: str, phone_number_id: str, body: dict[str, Any]) -> None:
        async with self._client(base_url="https://graph.facebook.com/v19.0") as client:
            resp = await client.post(
                f"/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
        self._raise_for_meta_error(channel, resp)

    @staticmethod
    def _raise_for_meta_error(channel: Any, resp: httpx.Response) -> None:
        """Defence in depth: honour Meta's own verdict even if our clock disagreed.

        A drifting server clock, or an inbound we never recorded, can leave our check
        thinking the window is open when Meta knows better.
        """
        if resp.status_code < 400:
            return
        try:
            error = (resp.json() or {}).get("error") or {}
        except ValueError:
            error = {}
        code = error.get("code")
        if code == WINDOW_CLOSED_CODE:
            raise WhatsAppWindowClosed()
        log.warning(
            "whatsapp_send_rejected",
            channel=str(getattr(channel, "id", "")),
            status=resp.status_code,
            code=code,
            message=str(error.get("message", ""))[:200],
        )


register(WhatsAppChannel())
