"""M3: the product catalog - CRUD, photos, search, tenant isolation (docs/26)."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import MediaAsset, OrgStorageUsage, Product
from app.modules.products import service


@pytest.fixture(autouse=True)
def _media_dir(tmp_path: Path) -> Iterator[None]:
    prev = settings.media_dir
    settings.media_dir = str(tmp_path / "media")
    yield
    settings.media_dir = prev


def _png(color: tuple[int, int, int] = (200, 10, 10), size: int = 32) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (size, size), color).save(out, format="PNG")
    return out.getvalue()


async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "ShopOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _make(client: AsyncClient, headers: dict[str, str], name: str, **extra: object) -> dict:
    r = await client.post("/v1/products", json={"name": name, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_crud_roundtrip(client: AsyncClient) -> None:
    h = await _headers(client, "prod1@example.com")
    p = await _make(
        client,
        h,
        "Black Kurta",
        sku="KUR-001",
        price_minor=129900,
        currency="inr",
        description="Cotton kurta, regular fit.",
        attributes={"size": "M", "colour": "black"},
        url="https://shop.example/kurta",
    )
    assert p["currency"] == "INR" and p["in_stock"] is True and p["images"] == []

    got = await client.get(f"/v1/products/{p['id']}", headers=h)
    assert got.json()["attributes"] == {"size": "M", "colour": "black"}

    upd = await client.patch(f"/v1/products/{p['id']}", json={"price_minor": 99900, "in_stock": False}, headers=h)
    assert upd.json()["price_minor"] == 99900 and upd.json()["in_stock"] is False
    assert upd.json()["name"] == "Black Kurta"  # untouched fields stay

    listed = await client.get("/v1/products", params={"query": "kurta"}, headers=h)
    assert listed.json()["total"] == 1

    assert (await client.delete(f"/v1/products/{p['id']}", headers=h)).status_code == 204
    assert (await client.get("/v1/products", headers=h)).json()["total"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"currency": "RUPEES"},
        {"price_minor": -5},
        {"url": "javascript:alert(1)"},
        {"attributes": {"k": "v" * 500}},
        {"attributes": {f"k{i}": "v" for i in range(30)}},
    ],
)
async def test_validation_rejects_bad_input(client: AsyncClient, bad: dict) -> None:
    h = await _headers(client, f"prod2{abs(hash(str(bad))) % 10**6}@example.com")
    r = await client.post("/v1/products", json={"name": "X", **bad}, headers=h)
    assert r.status_code == 422


async def test_sku_unique_per_org_not_global(client: AsyncClient) -> None:
    h1 = await _headers(client, "prod3a@example.com")
    h2 = await _headers(client, "prod3b@example.com")
    await _make(client, h1, "A", sku="SAME")
    dup = await client.post("/v1/products", json={"name": "B", "sku": "SAME"}, headers=h1)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "products.sku_exists"
    await _make(client, h2, "C", sku="SAME")  # another tenant may reuse it
    other = await _make(client, h1, "D", sku="OTHER")
    clash = await client.patch(f"/v1/products/{other['id']}", json={"sku": "SAME"}, headers=h1)
    assert clash.status_code == 409


async def test_tenant_isolation(client: AsyncClient) -> None:
    h1 = await _headers(client, "prod4a@example.com")
    h2 = await _headers(client, "prod4b@example.com")
    p = await _make(client, h1, "Secret Item")
    assert (await client.get(f"/v1/products/{p['id']}", headers=h2)).status_code == 404
    assert (await client.patch(f"/v1/products/{p['id']}", json={"name": "x"}, headers=h2)).status_code == 404
    assert (await client.delete(f"/v1/products/{p['id']}", headers=h2)).status_code == 404
    up = await client.post(f"/v1/products/{p['id']}/images", files={"file": ("a.png", _png(), "image/png")}, headers=h2)
    assert up.status_code == 404
    assert (await client.get("/v1/products", headers=h2)).json()["total"] == 0


async def test_image_upload_limit_delete_and_reorder(client: AsyncClient, db_session: AsyncSession) -> None:
    h = await _headers(client, "prod5@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    p = await _make(client, h, "Photo Product")

    for i in range(5):
        r = await client.post(
            f"/v1/products/{p['id']}/images",
            files={"file": (f"{i}.png", _png((i * 40, 10, 10), 30 + i), "image/png")},
            data={"alt_text": f"view {i}"},
            headers=h,
        )
        assert r.status_code == 201, r.text
    images = r.json()["images"]
    assert [im["position"] for im in images] == [0, 1, 2, 3, 4]
    assert images[0]["url"].startswith(settings.api_base_url) and "sig=" in images[0]["url"]

    sixth = await client.post(
        f"/v1/products/{p['id']}/images", files={"file": ("6.png", _png((9, 9, 9), 50), "image/png")}, headers=h
    )
    assert sixth.status_code == 400 and sixth.json()["error"]["code"] == "products.too_many_images"

    used = await db_session.get(OrgStorageUsage, org_id)
    assert used is not None and used.bytes_used > 0

    order = [im["id"] for im in reversed(images)]
    reordered = await client.put(f"/v1/products/{p['id']}/images/order", json={"image_ids": order}, headers=h)
    assert [im["id"] for im in reordered.json()["images"]] == order
    bad = await client.put(f"/v1/products/{p['id']}/images/order", json={"image_ids": order[:2]}, headers=h)
    assert bad.status_code == 400

    gone = await client.delete(f"/v1/products/{p['id']}/images/{order[0]}", headers=h)
    assert len(gone.json()["images"]) == 4
    await db_session.refresh(used)
    after_one = used.bytes_used
    for image_id in order[1:]:
        await client.delete(f"/v1/products/{p['id']}/images/{image_id}", headers=h)
    await db_session.refresh(used)
    assert used.bytes_used == 0 < after_one
    remaining = (await db_session.execute(select(MediaAsset).where(MediaAsset.organization_id == org_id))).all()
    assert remaining == []


async def test_non_image_upload_rejected(client: AsyncClient) -> None:
    h = await _headers(client, "prod6@example.com")
    p = await _make(client, h, "Thing")
    for name, body, ctype in (
        ("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", "image/svg+xml"),
        ("x.png", b"not an image at all", "image/png"),
    ):
        r = await client.post(f"/v1/products/{p['id']}/images", files={"file": (name, body, ctype)}, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "media.invalid_image"


async def test_upload_too_large_is_rejected(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    h = await _headers(client, "prod7@example.com")
    p = await _make(client, h, "Thing")
    monkeypatch.setattr(settings, "media_max_upload_bytes", 500)
    r = await client.post(
        f"/v1/products/{p['id']}/images", files={"file": ("big.png", _png(size=300), "image/png")}, headers=h
    )
    assert r.status_code == 413


# ── search ───────────────────────────────────────────────────────────────────
async def _seed(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Product]:
    rows = {
        "kurta": Product(organization_id=org_id, name="Black Cotton Kurta", sku="KUR-001", description="Regular fit"),
        "shoe": Product(
            organization_id=org_id, name="Running Shoes", sku="SHO-042", description="Light shoes for black tracks"
        ),
        "bag": Product(organization_id=org_id, name="Leather Bag", sku="BAG-7", description="Brown leather"),
        "gone": Product(organization_id=org_id, name="Black Retired Kurta", is_active=False),
    }
    session.add_all(rows.values())
    await session.flush()
    await session.refresh(rows["kurta"])  # pulls the generated search_vector
    return rows


async def test_search_ranks_name_over_description_and_sku_first(client: AsyncClient, db_session: AsyncSession) -> None:
    h = await _headers(client, "prod8@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    rows = await _seed(db_session, org_id)

    hits = await service.search_products(db_session, org_id, "black kurta", use_vectors=False)
    names = [p.name for p in hits]
    assert names[0] == "Black Cotton Kurta"
    assert "Black Retired Kurta" not in names  # soft-deleted
    assert "Leather Bag" not in names

    sku = await service.search_products(db_session, org_id, "sho-042", use_vectors=False)
    assert sku and sku[0].id == rows["shoe"].id  # exact SKU, case-insensitive

    prefix = await service.search_products(db_session, org_id, "leath", use_vectors=False)
    assert [p.name for p in prefix] == ["Leather Bag"]

    assert await service.search_products(db_session, org_id, "   ", use_vectors=False) == []
    assert await service.search_products(db_session, org_id, "';drop table products;--", use_vectors=False) == []


async def test_search_never_crosses_orgs(client: AsyncClient, db_session: AsyncSession) -> None:
    a = uuid.UUID((await _headers(client, "prod9a@example.com"))["X-Org-Id"])
    b = uuid.UUID((await _headers(client, "prod9b@example.com"))["X-Org-Id"])
    await _seed(db_session, a)
    assert await service.search_products(db_session, b, "kurta", use_vectors=False) == []


async def test_vector_half_works_with_the_fake_embedder(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_force_fake", True)  # deterministic keyless embeddings
    h = await _headers(client, "prod10@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    rows = await _seed(db_session, org_id)
    for p in rows.values():
        assert await service.embed_product(db_session, p.id) is True
    await db_session.flush()
    assert rows["kurta"].embedding is not None and len(rows["kurta"].embedding) == 768

    hits = await service.search_products(db_session, org_id, "Black Cotton Kurta", use_vectors=True)
    assert hits and hits[0].id == rows["kurta"].id
    assert all(p.is_active for p in hits)


async def test_embedding_failure_degrades_to_keyword_search(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("ollama down")

    monkeypatch.setattr("app.modules.products.service.build_embedding_provider", boom)
    h = await _headers(client, "prod11@example.com")
    org_id = uuid.UUID(h["X-Org-Id"])
    await _seed(db_session, org_id)
    hits = await service.search_products(db_session, org_id, "kurta")
    assert hits and hits[0].name == "Black Cotton Kurta"
