"""The admin surface is invisible to self-serve users (docs/18 §4, §13).

Every `/v1/admin` route must sit behind `require_staff`. This walks the real route table instead
of listing endpoints by hand, so a route added next month is covered without anyone remembering
to extend a test.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models import User
from app.modules.admin.deps import require_staff
from app.modules.admin.router import router as admin_router
from tests.selfserve_helpers import bearer, signup

pytestmark = pytest.mark.usefixtures("self_serve")


def _admin_routes() -> list[APIRoute]:
    """Every route on the admin router — read from the router itself: newer FastAPI wraps
    included routers in a lazy container, so `app.routes` no longer lists them flat."""
    return [r for r in admin_router.routes if isinstance(r, APIRoute)]


def test_the_admin_router_is_mounted_where_these_tests_look() -> None:
    assert {r.path for r in _admin_routes()} >= {"/v1/admin/orgs", "/v1/admin/users"}
    assert all(r.path.startswith("/v1/admin") for r in _admin_routes())
    schema_paths = create_app().openapi()["paths"]
    assert all(r.path in schema_paths for r in _admin_routes())  # and the app really serves them


def _requires_staff(route: APIRoute) -> bool:
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is require_staff:
            return True
        stack.extend(dep.dependencies)
    return False


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def test_there_are_admin_routes_to_check() -> None:
    assert len(_admin_routes()) >= 7  # a refactor that empties this list must not pass silently


def test_every_admin_route_is_behind_require_staff() -> None:
    unguarded = [f"{sorted(r.methods)} {r.path}" for r in _admin_routes() if not _requires_staff(r)]
    assert unguarded == []


async def test_a_normal_user_gets_403_on_every_admin_endpoint(client: AsyncClient) -> None:
    auth = await signup(client)
    seen = 0
    for route in _admin_routes():
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            body = {"enabled": True} if method in {"PUT", "POST", "PATCH"} else None
            resp = await client.request(method, _concrete(route.path), json=body, headers=bearer(auth))
            assert resp.status_code == 403, f"{method} {route.path} -> {resp.status_code} {resp.text}"
            assert resp.json()["error"]["code"] == "admin.forbidden"
            seen += 1
    assert seen >= 7


async def test_an_anonymous_caller_gets_401_on_every_admin_endpoint(client: AsyncClient) -> None:
    for route in _admin_routes():
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            body = {"enabled": True} if method in {"PUT", "POST", "PATCH"} else None
            resp = await client.request(method, _concrete(route.path), json=body)
            assert resp.status_code == 401, f"{method} {route.path} -> {resp.status_code}"


async def test_staff_still_get_in(client: AsyncClient, db_session: AsyncSession) -> None:
    """The control: the 403s above are the gate, not a broken route."""
    import uuid

    auth = await signup(client)
    user = await db_session.get(User, uuid.UUID(auth["user"]["id"]))
    assert user is not None
    user.is_staff = True
    await db_session.flush()
    assert (await client.get("/v1/admin/orgs", headers=bearer(auth))).status_code == 200
