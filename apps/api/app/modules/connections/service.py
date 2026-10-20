"""Connections: CRUD + connectivity test (docs/28 §5.1, T1.3).

Every method is org-scoped and gated on `TOOLS_MANAGE`; creating one also needs the plan to include tool
calling and a free `max_connections` slot (0 on trial/Starter, so the 402 is the locked state). Secrets are
Fernet-encrypted at rest (`app.tools.connections`), returned masked, and every change is audited.
"""

from __future__ import annotations

import re
import time
import uuid
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.core import rbac
from app.core.audit import write_audit
from app.core.egress import EgressBlocked, EgressError, ip_is_public, literal_ip, safe_request
from app.core.errors import AppError
from app.models import Connection, MCPServer, Tool
from app.modules.connections import schemas
from app.modules.orgs.deps import OrgContext
from app.tools import connections as conn_auth

_HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_MAX_ALLOWED_HOSTS = 10


# ── Validation ───────────────────────────────────────────────────────────────────
def _clean_base_url(raw: str) -> str:
    url = raw.strip()
    if "{{" in url:
        raise AppError("connections.bad_url", "A connection's base URL cannot contain placeholders.", 400)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AppError("connections.bad_url", "The base URL must start with http:// or https://.", 400)
    if parts.username or parts.password:
        raise AppError("connections.bad_url", "Put credentials in the auth settings, not in the URL.", 400)
    if parts.query or parts.fragment:
        raise AppError("connections.bad_url", "The base URL cannot have a query string or fragment.", 400)
    ip = literal_ip(parts.hostname)
    if (ip is not None and not ip_is_public(ip)) or parts.hostname.rstrip(".").lower() == "localhost":
        raise AppError("connections.bad_url", "That address is private or local and cannot be called.", 400)
    return url.rstrip("/")


def _clean_allowed_hosts(hosts: list[str] | None, base_url: str) -> list[str]:
    if not hosts:
        return [(urlsplit(base_url).hostname or "").lower().rstrip(".")]
    cleaned: list[str] = []
    for h in hosts:
        host = h.strip().lower().rstrip(".")
        if not host:
            continue
        if not _HOST_RE.match(host):
            raise AppError("connections.bad_allowed_host", f"'{h}' is not a valid hostname.", 400)
        if host not in cleaned:
            cleaned.append(host)
    if not cleaned:
        return [(urlsplit(base_url).hostname or "").lower().rstrip(".")]
    if len(cleaned) > _MAX_ALLOWED_HOSTS:
        raise AppError("connections.too_many_hosts", f"At most {_MAX_ALLOWED_HOSTS} allowed hosts.", 400)
    return cleaned


def _clean_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        name = k.strip()
        if not name or any(c in name + v for c in "\r\n"):
            raise AppError("connections.bad_header", "Header names and values cannot contain line breaks.", 400)
        out[name] = v
    return out


# ── Helpers ──────────────────────────────────────────────────────────────────────
async def _get(session: AsyncSession, ctx: OrgContext, connection_id: uuid.UUID) -> Connection:
    conn = await session.get(Connection, connection_id)
    if conn is None or conn.organization_id != ctx.org.id:
        raise AppError("connections.not_found", "Connection not found.", 404)
    return conn


async def _tool_names(session: AsyncSession, connection_id: uuid.UUID) -> list[str]:
    rows = await session.execute(select(Tool.name).where(Tool.connection_id == connection_id).order_by(Tool.name))
    return [r[0] for r in rows.all()]


async def _mcp_server_names(session: AsyncSession, connection_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(MCPServer.name).where(MCPServer.connection_id == connection_id).order_by(MCPServer.name)
    )
    return [r[0] for r in rows.all()]


async def _tools_count(session: AsyncSession, connection_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(select(func.count()).select_from(Tool).where(Tool.connection_id == connection_id))
        ).scalar_one()
    )


async def _out(session: AsyncSession, conn: Connection) -> schemas.ConnectionOut:
    resolved = conn_auth.resolve(conn)
    return schemas.ConnectionOut(
        id=conn.id,
        name=conn.name,
        base_url=conn.base_url,
        auth_type=conn.auth_type,
        auth=conn_auth.mask_auth(conn.auth_type, resolved.secrets),
        default_headers=resolved.default_headers,
        allowed_hosts=resolved.allowed_hosts,
        timeout_s=conn.timeout_s,
        health_path=conn.health_path,
        enabled=conn.enabled,
        tools_count=await _tools_count(session, conn.id),
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


async def _require_unique_name(
    session: AsyncSession, org_id: uuid.UUID, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = select(Connection.id).where(Connection.organization_id == org_id, Connection.name == name)
    found = (await session.execute(stmt)).scalars().first()
    if found is not None and found != except_id:
        raise AppError("connections.name_taken", f"A connection named '{name}' already exists.", 409)


# ── CRUD ─────────────────────────────────────────────────────────────────────────
async def list_connections(session: AsyncSession, ctx: OrgContext) -> list[schemas.ConnectionOut]:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    stmt = select(Connection).where(Connection.organization_id == ctx.org.id).order_by(Connection.name)
    return [await _out(session, c) for c in (await session.execute(stmt)).scalars().all()]


async def get_connection(session: AsyncSession, ctx: OrgContext, connection_id: uuid.UUID) -> schemas.ConnectionOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    return await _out(session, await _get(session, ctx, connection_id))


async def create_connection(
    session: AsyncSession, ctx: OrgContext, data: schemas.CreateConnectionRequest
) -> schemas.ConnectionOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    await usage.require_connection_slot(session, ctx.org)
    base_url = _clean_base_url(data.base_url)
    secrets = conn_auth.normalize_auth(data.auth_type, data.auth)
    name = data.name.strip()
    await _require_unique_name(session, ctx.org.id, name)
    conn = Connection(
        organization_id=ctx.org.id,
        name=name,
        base_url=base_url,
        auth_type=data.auth_type,
        secrets_enc=conn_auth.encrypt_auth(secrets),
        default_headers=_clean_headers(data.default_headers),
        allowed_hosts=_clean_allowed_hosts(data.allowed_hosts, base_url),
        timeout_s=data.timeout_s,
        health_path=_clean_health_path(data.health_path),
        enabled=data.enabled,
        created_by=ctx.user.id,
    )
    session.add(conn)
    await session.flush()
    await write_audit(
        session, ctx.org.id, ctx.user.id, "connection.created",
        target_type="connection", target_id=str(conn.id), meta={"name": name, "auth_type": data.auth_type},
    )
    return await _out(session, conn)


def _clean_health_path(path: str | None) -> str | None:
    if path is None or not path.strip():
        return None
    if "{{" in path:
        raise AppError("connections.bad_health_path", "The health path cannot contain placeholders.", 400)
    return "/" + path.strip().lstrip("/")


async def update_connection(
    session: AsyncSession, ctx: OrgContext, connection_id: uuid.UUID, data: schemas.UpdateConnectionRequest
) -> schemas.ConnectionOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    conn = await _get(session, ctx, connection_id)
    changed: list[str] = []
    existing = conn_auth.resolve(conn).secrets

    if data.name is not None and data.name.strip() != conn.name:
        await _require_unique_name(session, ctx.org.id, data.name.strip(), except_id=conn.id)
        conn.name = data.name.strip()
        changed.append("name")
    if data.base_url is not None:
        new_url = _clean_base_url(data.base_url)
        if new_url != conn.base_url:
            conn.base_url = new_url
            changed.append("base_url")
            if data.allowed_hosts is None:
                # The old default allowlist (just the old host) would now block the new base URL.
                conn.allowed_hosts = _clean_allowed_hosts(None, new_url)
    if data.allowed_hosts is not None:
        conn.allowed_hosts = _clean_allowed_hosts(data.allowed_hosts, conn.base_url)
        changed.append("allowed_hosts")
    if data.auth_type is not None or data.auth is not None:
        new_type = data.auth_type or conn.auth_type
        same_type = new_type == conn.auth_type
        auth_in = dict(data.auth or {})
        if same_type:
            # Non-secret fields not re-sent keep their stored value; secrets are handled by normalize_auth.
            keep = {
                k: v
                for k, v in existing.items()
                if not conn_auth.AUTH_FIELDS[new_type].get(k, False) and k not in auth_in
            }
            auth_in = {**keep, **auth_in}
        secrets = conn_auth.normalize_auth(new_type, auth_in, existing=existing if same_type else None)
        conn.auth_type = new_type
        conn.secrets_enc = conn_auth.encrypt_auth(secrets)
        changed.append("auth")
    if data.default_headers is not None:
        conn.default_headers = _clean_headers(data.default_headers)
        changed.append("default_headers")
    if data.timeout_s is not None:
        conn.timeout_s = data.timeout_s
        changed.append("timeout_s")
    if "health_path" in data.model_fields_set:
        conn.health_path = _clean_health_path(data.health_path)
        changed.append("health_path")
    if data.enabled is not None and data.enabled != conn.enabled:
        conn.enabled = data.enabled
        changed.append("enabled")

    await session.flush()
    if changed:
        await write_audit(
            session, ctx.org.id, ctx.user.id, "connection.updated",
            target_type="connection", target_id=str(conn.id), meta={"fields": changed},
        )
    return await _out(session, conn)


async def delete_connection(session: AsyncSession, ctx: OrgContext, connection_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    conn = await _get(session, ctx, connection_id)
    names = await _tool_names(session, conn.id)
    servers = await _mcp_server_names(session, conn.id)
    if names or servers:
        raise AppError(
            "connections.in_use",
            "This connection is used by tools or MCP servers. Delete or repoint them first.",
            409,
            details={"tools": names, "mcp_servers": servers},
        )
    await write_audit(
        session, ctx.org.id, ctx.user.id, "connection.deleted",
        target_type="connection", target_id=str(conn.id), meta={"name": conn.name},
    )
    await session.delete(conn)
    await session.flush()


# ── Test ─────────────────────────────────────────────────────────────────────────
async def test_connection(
    session: AsyncSession, ctx: OrgContext, connection_id: uuid.UUID
) -> schemas.ConnectionTestResponse:
    """GET `health_path` (or the base URL) with the connection's auth. Reports status and latency only -
    the response body is read and thrown away, so a test can never be used to browse the API."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await usage.require_feature(session, ctx.org, "tool_calling")
    conn = conn_auth.resolve(await _get(session, ctx, connection_id))
    url = conn.base_url + (conn.health_path or "")
    started = time.perf_counter()
    secrets = conn_auth.secret_values(conn.auth_type, conn.secrets)
    try:
        inj = await conn_auth.build_auth(conn)
        resp = await safe_request(
            "GET",
            url,
            allowed_hosts=conn.allowed_hosts or None,
            headers={**conn.default_headers, **inj.headers},
            params=inj.query or None,
            timeout_s=float(conn.timeout_s),
            max_bytes=2048,
        )
    except conn_auth.ConnectionAuthError as exc:
        return schemas.ConnectionTestResponse(ok=False, error=f"authentication failed: {exc}")
    except EgressBlocked as exc:
        return schemas.ConnectionTestResponse(ok=False, error=exc.reason)
    except EgressError as exc:
        return schemas.ConnectionTestResponse(ok=False, error=str(conn_auth.scrub(str(exc), secrets)))
    return schemas.ConnectionTestResponse(
        ok=resp.status_code < 400,
        status_code=resp.status_code,
        latency_ms=int((time.perf_counter() - started) * 1000),
        error=None if resp.status_code < 400 else f"HTTP {resp.status_code}",
    )
