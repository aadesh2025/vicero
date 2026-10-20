"""Connection secrets and request auth, shared by the Connections module and the API tool executor.

A Connection stores its credentials as one Fernet-encrypted JSON object (`secrets_enc`). Its shape is
fixed per `auth_type` (`AUTH_FIELDS`). The plaintext exists only inside a `ResolvedConnection`, built at
execution time on the server; the API shows `mask_auth()` output and the model never sees any of it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.egress import EgressBlocked, EgressError, safe_request
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Connection

log = get_logger("tools.connections")

AUTH_TYPES = ("none", "api_key", "bearer", "basic", "custom_headers", "oauth2_client_credentials")

#: auth_type -> {field: is_secret}. `custom_headers` is special-cased (a dict of header -> value).
AUTH_FIELDS: dict[str, dict[str, bool]] = {
    "none": {},
    "api_key": {"name": False, "in": False, "value": True},
    "bearer": {"token": True},
    "basic": {"username": False, "password": True},
    "custom_headers": {"headers": True},
    "oauth2_client_credentials": {"token_url": False, "client_id": False, "client_secret": True, "scope": False},
}
_REQUIRED: dict[str, tuple[str, ...]] = {
    "none": (),
    "api_key": ("name", "value"),
    "bearer": ("token",),
    "basic": ("username", "password"),
    "custom_headers": ("headers",),
    "oauth2_client_credentials": ("token_url", "client_id", "client_secret"),
}

MASK = "••••"


class ConnectionAuthError(RuntimeError):
    """A token could not be obtained. The message is safe to show the tenant (it carries no secret)."""


def mask_value(value: str) -> str:
    return f"{MASK}{value[-4:]}" if len(value) >= 8 else MASK


# ── Validation and masking ───────────────────────────────────────────────────────
def normalize_auth(auth_type: str, auth: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate `auth` for `auth_type` and return the object to encrypt.

    `existing` is the stored plaintext when the type is unchanged: a secret field left blank (or
    sent as the masked value the API returned) keeps the stored one, so an edit form never has to
    re-enter a secret to change a name.
    """
    if auth_type not in AUTH_TYPES:
        raise AppError("connections.bad_auth_type", f"Unknown auth type '{auth_type}'.", 400)
    fields = AUTH_FIELDS[auth_type]
    unknown = set(auth) - set(fields)
    if unknown:
        raise AppError(
            "connections.bad_auth_field",
            f"Unexpected auth field(s) for '{auth_type}': {', '.join(sorted(unknown))}.",
            400,
        )
    out: dict[str, Any] = {}
    for name, is_secret in fields.items():
        value = auth.get(name)
        if auth_type == "custom_headers":
            value = _merge_headers(value, (existing or {}).get("headers"))
        elif is_secret and (value is None or value == "" or (isinstance(value, str) and value.startswith(MASK))):
            value = (existing or {}).get(name)
        if value in (None, "", {}):
            continue
        if name == "in" and value not in ("header", "query"):
            raise AppError("connections.bad_auth_field", "api_key 'in' must be 'header' or 'query'.", 400)
        if not isinstance(value, str | dict):
            raise AppError("connections.bad_auth_field", f"Auth field '{name}' must be text.", 400)
        out[name] = value
    if auth_type == "api_key":
        out.setdefault("in", "header")
    for name in _REQUIRED[auth_type]:
        if name not in out:
            raise AppError("connections.auth_incomplete", f"Auth type '{auth_type}' needs '{name}'.", 400)
    if auth_type == "oauth2_client_credentials":
        parts = urlsplit(str(out["token_url"]))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise AppError("connections.bad_auth_field", "token_url must be an http(s) URL.", 400)
    return out


def _merge_headers(new: Any, old: Any) -> dict[str, str] | None:
    if new is None:
        return old if isinstance(old, dict) else None
    if not isinstance(new, dict):
        raise AppError("connections.bad_auth_field", "Auth headers must be an object.", 400)
    merged: dict[str, str] = {}
    for k, v in new.items():
        if not isinstance(v, str):
            raise AppError("connections.bad_auth_field", "Auth header values must be text.", 400)
        if v == "" or v.startswith(MASK):
            if isinstance(old, dict) and k in old:
                merged[str(k)] = old[k]
            continue
        merged[str(k)] = v
    return merged


def mask_auth(auth_type: str, secrets: dict[str, Any]) -> dict[str, Any]:
    """What the API may return: non-secret fields as they are, secrets as `••••last4`."""
    out: dict[str, Any] = {}
    for name, is_secret in AUTH_FIELDS.get(auth_type, {}).items():
        if name not in secrets:
            continue
        value = secrets[name]
        if auth_type == "custom_headers":
            out[name] = {k: mask_value(str(v)) for k, v in value.items()}
        elif is_secret:
            out[name] = mask_value(str(value))
        else:
            out[name] = value
    return out


def secret_values(auth_type: str, secrets: dict[str, Any]) -> list[str]:
    """Every plaintext secret, for scrubbing a response or log line that might echo one."""
    found: list[str] = []
    for name, is_secret in AUTH_FIELDS.get(auth_type, {}).items():
        value = secrets.get(name)
        if not is_secret or not value:
            continue
        if isinstance(value, dict):
            found.extend(str(v) for v in value.values() if v)
        else:
            found.append(str(value))
    return [v for v in found if len(v) >= 4]


def scrub(obj: Any, secrets: list[str]) -> Any:
    """Replace any occurrence of a secret inside `obj` (recursively through dicts, lists, strings)."""
    if not secrets:
        return obj
    if isinstance(obj, str):
        for s in secrets:
            obj = obj.replace(s, MASK)
        return obj
    if isinstance(obj, dict):
        return {k: scrub(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, secrets) for v in obj]
    return obj


def encrypt_auth(secrets: dict[str, Any]) -> str | None:
    return encrypt(json.dumps(secrets)) if secrets else None


# ── Resolved connection ───────────────────────────────────────────────────────────
@dataclass(slots=True)
class ResolvedConnection:
    id: uuid.UUID
    name: str
    base_url: str
    auth_type: str
    secrets: dict[str, Any]
    default_headers: dict[str, str]
    allowed_hosts: list[str]
    timeout_s: int
    health_path: str | None = None
    enabled: bool = True


@dataclass(slots=True)
class AuthInjection:
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)


def resolve(conn: Connection) -> ResolvedConnection:
    secrets: dict[str, Any] = {}
    if conn.secrets_enc:
        try:
            secrets = json.loads(decrypt(conn.secrets_enc))
        except Exception:  # SECRET_KEY rotated, or the column was edited by hand
            log.error("connection_secrets_unreadable", connection_id=str(conn.id))
            raise AppError(
                "connections.secrets_unreadable",
                "This connection's credentials can no longer be read. Re-enter them.",
                409,
            ) from None
    return ResolvedConnection(
        id=conn.id,
        name=conn.name,
        base_url=conn.base_url,
        auth_type=conn.auth_type,
        secrets=secrets,
        default_headers={str(k): str(v) for k, v in (conn.default_headers or {}).items()},
        allowed_hosts=list(conn.allowed_hosts or []),
        timeout_s=conn.timeout_s,
        health_path=conn.health_path,
        enabled=conn.enabled,
    )


async def load_connection(
    session: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID
) -> ResolvedConnection | None:
    """The org's connection, or None. Never returns another org's row."""
    conn = await session.get(Connection, connection_id)
    if conn is None or conn.organization_id != org_id:
        return None
    return resolve(conn)


# ── Auth injection ────────────────────────────────────────────────────────────────
_mem_tokens: dict[str, tuple[str, float]] = {}


def _token_key(conn: ResolvedConnection) -> str:
    s = conn.secrets
    material = "|".join(str(s.get(k)) for k in ("token_url", "client_id", "client_secret", "scope"))
    digest = hashlib.sha256(material.encode())
    return f"vicero:conn_token:{conn.id}:{digest.hexdigest()[:16]}"


async def _cache_get(key: str) -> str | None:
    now = time.monotonic()
    hit = _mem_tokens.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            raw = await client.get(key)
        finally:
            await client.aclose()
        if not raw:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception:
        return None


async def _cache_set(key: str, token: str, ttl: int) -> None:
    _mem_tokens[key] = (token, time.monotonic() + ttl)
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            await client.set(key, token, ex=ttl)
        finally:
            await client.aclose()
    except Exception:
        pass  # in-process cache above still holds it


async def oauth_token(conn: ResolvedConnection, *, transport: Any = None) -> str:
    """Client-credentials access token, cached until shortly before it expires."""
    key = _token_key(conn)
    cached = await _cache_get(key)
    if cached:
        return cached
    s = conn.secrets
    form = {
        "grant_type": "client_credentials",
        "client_id": str(s["client_id"]),
        "client_secret": str(s["client_secret"]),
    }
    if s.get("scope"):
        form["scope"] = str(s["scope"])
    token_host = urlsplit(str(s["token_url"])).hostname or ""
    allowed = [*conn.allowed_hosts, token_host] if conn.allowed_hosts else None
    try:
        resp = await safe_request(
            "POST",
            str(s["token_url"]),
            allowed_hosts=allowed,
            data=form,
            headers={"Accept": "application/json"},
            timeout_s=float(conn.timeout_s),
            max_bytes=64_000,
            transport=transport,
        )
    except EgressBlocked as exc:
        raise ConnectionAuthError(exc.reason) from exc
    except EgressError as exc:
        raise ConnectionAuthError(f"token request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise ConnectionAuthError(f"token endpoint answered HTTP {resp.status_code}")
    try:
        body = resp.json()
        token = str(body["access_token"])
    except (ValueError, KeyError, TypeError):
        raise ConnectionAuthError("token endpoint returned no access_token") from None
    try:
        ttl = max(int(body.get("expires_in", 3600)) - 30, 30)
    except (TypeError, ValueError):
        ttl = 300
    await _cache_set(key, token, ttl)
    return token


async def build_auth(conn: ResolvedConnection, *, transport: Any = None) -> AuthInjection:
    s = conn.secrets
    inj = AuthInjection()
    if conn.auth_type == "api_key":
        target = inj.query if s.get("in") == "query" else inj.headers
        target[str(s["name"])] = str(s["value"])
    elif conn.auth_type == "bearer":
        inj.headers["Authorization"] = f"Bearer {s['token']}"
    elif conn.auth_type == "basic":
        raw = f"{s['username']}:{s['password']}".encode()
        inj.headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    elif conn.auth_type == "custom_headers":
        inj.headers.update({str(k): str(v) for k, v in (s.get("headers") or {}).items()})
    elif conn.auth_type == "oauth2_client_credentials":
        inj.headers["Authorization"] = f"Bearer {await oauth_token(conn, transport=transport)}"
    return inj
