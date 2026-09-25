"""Who is really on the other end of a request — without trusting a header anyone can send.

Behind a reverse proxy or the web app's BFF, the TCP peer is the *proxy*, so every visitor looks
like one address and every per-IP limit (signup, login, magic link, reset) becomes one shared
bucket. The proxy passes the real address in `X-Forwarded-For`. But that header is just text: a
client that reaches the API directly can write anything into it, and trusting it blindly lets
an attacker pick a fresh "IP" per request and walk straight past every limit.

So the rule (docs/SECURITY.md §11):

* The header is read **only when the TCP peer is a configured trusted proxy**
  (`TRUSTED_PROXIES`: IPs / CIDRs). From anyone else it is ignored.
* Even then the chain is read **from the right**, skipping our own proxies, and the first
  address that is not one of them is the client. Whatever a client prepends to the header sits
  to the *left* of what our proxies append, so it can never be picked.
* If there is no usable address — a trusted proxy that forwarded nothing (local dev, where the
  web app sees no proxy of its own), or a loopback peer — the client is **unknown**. Callers
  that count per-client (the signup cap) skip rather than lump everyone into one bucket;
  the general limiter still falls back to the peer address so nothing is left unlimited.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import Request

from app.core.config import settings

_MAX_HOPS = 20  # a longer chain is malformed or hostile; only the right-hand hops matter anyway

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True, slots=True)
class Client:
    ip: str
    #: False when the address identifies a proxy or this machine rather than a person.
    known: bool


@lru_cache(maxsize=8)
def _parse_networks(spec: str) -> tuple[IPNetwork, ...]:
    nets: list[IPNetwork] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if raw:
            nets.append(ipaddress.ip_network(raw, strict=False))
    return tuple(nets)


def _trusted() -> tuple[IPNetwork, ...]:
    return _parse_networks(settings.trusted_proxies)


def _parse(value: str) -> IPAddress | None:
    value = value.strip()
    # `[::1]:1234` / `1.2.3.4:5678` forms some proxies emit.
    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    elif value.count(":") == 1 and "." in value:
        value = value.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_trusted(addr: IPAddress) -> bool:
    return any(addr in net for net in _trusted() if net.version == addr.version)


def resolve_client(request: Request | Any) -> Client:
    conn = getattr(request, "client", None)
    peer_raw: str | None = conn.host if conn is not None else None
    peer = _parse(peer_raw) if peer_raw else None
    if peer is None:
        return Client(peer_raw or "unknown", known=False)

    if not _is_trusted(peer):
        # A direct caller: its own address is the truth, and any X-Forwarded-For it sent is noise.
        return Client(str(peer), known=not peer.is_loopback)

    header = request.headers.get("x-forwarded-for", "")
    hops = [h for h in header.split(",")[-_MAX_HOPS:] if h.strip()]
    for raw in reversed(hops):  # nearest hop first
        addr = _parse(raw)
        if addr is None:
            continue  # garbage entry: skip it, keep looking left
        if _is_trusted(addr):
            continue  # one of our own proxies
        return Client(str(addr), known=True)
    return Client(str(peer), known=False)
