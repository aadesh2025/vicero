"""Download a customer's photo from Meta (docs/26 §7, §8).

The URL comes from a webhook payload or a Graph response, so it is attacker-influenced input: HTTPS only,
host allow-list on every hop (redirects are followed by hand), short timeout, and a byte cap enforced while
streaming so a hostile server cannot make us buffer gigabytes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.channels.base import BaseChannel
    from app.models import Channel

log = get_logger("media.fetch")

#: Hosts Meta serves customer media from. A suffix matches the host itself and any subdomain.
ALLOWED_HOST_SUFFIXES = ("fbcdn.net", "fbsbx.com", "cdninstagram.com", "graph.facebook.com")
_TIMEOUT_S = 10.0
_MAX_HOPS = 3


class MediaFetchError(Exception):
    """The photo could not be downloaded. The message is for logs, never for the customer."""


def graph_base() -> str:
    # Same URL as `channels.meta_graph.graph_base`, restated here so this package does not import `channels`
    # (which imports it - a package cycle).
    return f"https://graph.facebook.com/{settings.meta_graph_version}"


def host_allowed(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if parts.port not in (None, 443):
        return False
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


async def _get_bytes(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> bytes:
    cap = settings.media_max_upload_bytes
    for _ in range(_MAX_HOPS + 1):
        if not host_allowed(url):
            raise MediaFetchError("host not allowed")
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise MediaFetchError("redirect without location")
                url = str(httpx.URL(url).join(location))
                continue
            if resp.status_code != 200:
                raise MediaFetchError(f"status {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > cap:
                raise MediaFetchError("too large")
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > cap:
                    raise MediaFetchError("too large")
            return bytes(buf)
    raise MediaFetchError("too many redirects")


def _client(adapter: BaseChannel) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT_S, transport=adapter.transport, follow_redirects=False)


async def fetch_whatsapp(adapter: BaseChannel, channel: Channel, media_id: str) -> bytes:
    """`GET /{media-id}` for a short-lived URL, then `GET` that URL, both with the channel's token."""
    token = adapter.secret(channel, "access_token")
    if not token:
        raise MediaFetchError("channel has no access token")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with _client(adapter) as client:
            meta = await client.get(f"{graph_base()}/{media_id}", headers=headers)
            if meta.status_code != 200:
                raise MediaFetchError(f"media lookup status {meta.status_code}")
            url = (meta.json() or {}).get("url")
            if not isinstance(url, str):
                raise MediaFetchError("media lookup returned no url")
            return await _get_bytes(client, url, headers)
    except (httpx.HTTPError, ValueError) as exc:
        raise MediaFetchError(type(exc).__name__) from exc


async def fetch_url(adapter: BaseChannel, url: str) -> bytes:
    """Messenger / Instagram: the attachment URL is a public, expiring CDN link, so no token is sent."""
    try:
        async with _client(adapter) as client:
            return await _get_bytes(client, url, {})
    except httpx.HTTPError as exc:
        raise MediaFetchError(type(exc).__name__) from exc
