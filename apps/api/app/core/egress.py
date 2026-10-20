"""Egress guard v2 (docs/28 §7 step 9, ADR-132): every outbound call a tenant can steer goes through here.

`app.core.ssrf.is_blocked_host` checks a name and then lets the HTTP client resolve it *again*, so a
DNS answer that changes between the two (rebinding) walks straight past the check. `safe_request`
resolves once, rejects any non-public address, then connects to **that IP** and sends the original
Host header / TLS server name. Redirects are off unless asked for, and every hop is re-validated.
The body is streamed and cut at `max_bytes`, never buffered whole.

`allowed_hosts` narrows where a request may go (a Connection's allowlist); it can never widen it:
the address checks apply to an allowed host too.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

from app.core.logging import get_logger

log = get_logger("egress")

#: Headers the caller may not set: they would desync the pinned connection or smuggle a second request.
_FORBIDDEN_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "upgrade", "te"})
#: Credentials are dropped when a redirect leaves the original host.
_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS_HARD = 3


#: Test seam: when set, requests that pass no `transport` use this one (a `httpx.MockTransport`), so the
#: real call sites - tools, connection tests, token fetches - can be driven end to end with no network.
DEFAULT_TRANSPORT: httpx.AsyncBaseTransport | None = None


class EgressError(Exception):
    """The request could not be made or completed (timeout, connection failure, bad URL)."""


class EgressBlocked(EgressError):
    """The destination was refused by policy. `reason` is safe to show the tenant."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class EgressResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes
    truncated: bool
    url: str
    elapsed_ms: int
    redirects: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        return json.loads(self.body)


# ── Address policy ───────────────────────────────────────────────────────────────
def _normalize_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-mapped / 6to4 / Teredo-style IPv6 so `::ffff:127.0.0.1` is judged as 127.0.0.1."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip.teredo is not None:
            return ip.teredo[1]
    return ip


def ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for a globally routable unicast address.

    `is_global` already excludes private, loopback, link-local (incl. the 169.254.169.254 metadata
    address), CGNAT 100.64/10, IPv6 ULA fc00::/7, documentation and reserved ranges. The explicit
    checks are belt and braces for ranges Python versions disagree about.
    """
    ip = _normalize_ip(ip)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return bool(ip.is_global)


def literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IP literal, including the legacy forms a resolver would accept and a regex would miss
    (`2130706433`, `0x7f.1`, `0177.0.0.1`, `127.1`)."""
    bare = host.strip("[]")
    try:
        return ipaddress.ip_address(bare)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(bare))
    except (OSError, ValueError):
        return None


async def resolve_host(host: str, port: int) -> list[str]:
    """Every address `host` resolves to. A module-level seam so tests can stage DNS answers."""
    literal = literal_ip(host)
    if literal is not None:
        return [str(literal)]
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EgressBlocked("the host could not be resolved") from exc
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


def host_allowed(host: str, allowed_hosts: list[str] | None) -> bool:
    """`allowed_hosts` entries are exact hostnames or `*.suffix` wildcards. None/empty means no extra rule."""
    if not allowed_hosts:
        return True
    h = host.lower().rstrip(".")
    for entry in allowed_hosts:
        e = entry.strip().lower().rstrip(".")
        if not e:
            continue
        if e.startswith("*."):
            if h.endswith(e[1:]) and h != e[2:]:
                return True
        elif h == e:
            return True
    return False


def _split(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise EgressBlocked("only http(s) URLs are allowed")
    if not parts.hostname:
        raise EgressBlocked("only http(s) URLs are allowed")
    if parts.username or parts.password:
        raise EgressBlocked("credentials in the URL are not allowed")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise EgressBlocked("the URL has an invalid port") from exc
    return parts.scheme, parts.hostname.rstrip(".").lower(), port


async def check_destination(url: str, allowed_hosts: list[str] | None = None) -> tuple[str, str, int, str]:
    """Validate `url` and return (scheme, host, port, pinned_ip). Raises `EgressBlocked`."""
    scheme, host, port = _split(url)
    if host == "localhost" or host.endswith(".localhost"):
        raise EgressBlocked("refusing to call a private/loopback host")
    if not host_allowed(host, allowed_hosts):
        raise EgressBlocked("that host is not on this connection's allowed list")
    addrs = await resolve_host(host, port)
    if not addrs:
        raise EgressBlocked("the host could not be resolved")
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for a in addrs:
        try:
            parsed.append(ipaddress.ip_address(a))
        except ValueError:
            raise EgressBlocked("the host resolved to an invalid address") from None
    # Every answer must be public: one private record among several is the classic rebinding setup.
    if not all(ip_is_public(ip) for ip in parsed):
        raise EgressBlocked("refusing to call a private/loopback host")
    return scheme, host, port, str(parsed[0])


def _header_value(value: str) -> str:
    """CR/LF and other control characters dropped, non-ASCII percent-encoded: a header is bytes on the wire
    and a stray emoji in a model-supplied value must not turn into a crash or a second header."""
    kept = "".join(c for c in value if c == chr(9) or (c >= chr(32) and c != chr(127)))
    return "".join(c if c.isascii() else quote(c) for c in kept)


def _clean_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (headers or {}).items():
        name = str(k).replace("\r", "").replace("\n", "").strip()
        if not name or name.lower() in _FORBIDDEN_HEADERS:
            continue
        out[name] = _header_value(str(v))
    return out


def _pinned_url(url: str, scheme: str, ip: str, port: int) -> str:
    parts = urlsplit(url)
    host = f"[{ip}]" if ":" in ip else ip
    default = 443 if scheme == "https" else 80
    netloc = host if port == default else f"{host}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def _host_header(host: str, scheme: str, port: int) -> str:
    default = 443 if scheme == "https" else 80
    shown = f"[{host}]" if ":" in host else host
    return shown if port == default else f"{shown}:{port}"


async def safe_request(
    method: str,
    url: str,
    *,
    allowed_hosts: list[str] | None = None,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    content: bytes | str | None = None,
    json: Any = None,
    data: Mapping[str, str] | None = None,
    timeout_s: float = 15.0,
    max_bytes: int = 1_000_000,
    max_redirects: int = 0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EgressResponse:
    """One guarded request. Raises `EgressBlocked` (policy) or `EgressError` (network)."""
    method = method.upper()
    transport = transport or DEFAULT_TRANSPORT
    hops = min(max(max_redirects, 0), _MAX_REDIRECTS_HARD)
    clean = _clean_headers(headers)
    current = url
    first_host = _split(url)[1]
    redirects: list[str] = []
    started = time.perf_counter()
    body_kwargs: dict[str, Any] = {}
    if json is not None:
        body_kwargs["json"] = json
    elif data is not None:
        body_kwargs["data"] = dict(data)
    elif content is not None:
        body_kwargs["content"] = content

    for hop in range(hops + 1):
        scheme, host, port, ip = await check_destination(current, allowed_hosts)
        target = _pinned_url(current, scheme, ip, port)
        send_headers = dict(clean)
        if host != first_host:
            send_headers = {k: v for k, v in send_headers.items() if k.lower() not in _CREDENTIAL_HEADERS}
        send_headers["Host"] = _host_header(host, scheme, port)
        extensions = {"sni_hostname": host} if scheme == "https" else {}
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s, follow_redirects=False, trust_env=False, transport=transport
            ) as client:
                request = client.build_request(
                    method,
                    target,
                    headers=send_headers,
                    params=dict(params) if params and hop == 0 else None,
                    extensions=extensions,
                    **body_kwargs,
                )
                resp = await client.send(request, stream=True)
                try:
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    async for chunk in resp.aiter_bytes():
                        room = max_bytes - size
                        if len(chunk) > room:
                            chunks.append(chunk[: max(room, 0)])
                            truncated = True
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    status, resp_headers = resp.status_code, resp.headers
                finally:
                    await resp.aclose()
        except httpx.TimeoutException as exc:
            raise EgressError(f"request timed out after {timeout_s:g}s") from exc
        except httpx.HTTPError as exc:
            raise EgressError(f"request failed: {exc.__class__.__name__}") from exc

        if status in _REDIRECT_CODES and hop < hops and (loc := resp_headers.get("location")):
            current = urljoin(current, loc)
            redirects.append(current)
            if status == 303 or (status in (301, 302) and method == "POST"):
                method = "GET"
                body_kwargs = {}
            continue
        return EgressResponse(
            status_code=status,
            headers=resp_headers,
            body=b"".join(chunks),
            truncated=truncated,
            url=current,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            redirects=redirects,
        )
    raise EgressError("too many redirects")  # pragma: no cover - the loop always returns or raises

