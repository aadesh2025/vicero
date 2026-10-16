"""M1 media foundation: store, sanitiser, signed URLs, routes, retention (docs/26)."""

from __future__ import annotations

import datetime as dt
import io
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.media import service, urls
from app.media.sanitize import sanitize_image
from app.media.store import LocalMediaStore
from app.models import MediaAsset, Organization


def _jpeg(size: tuple[int, int] = (64, 48), exif: bool = False) -> bytes:
    img = Image.new("RGB", size, (200, 30, 30))
    out = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x010F] = "SecretCam"
        e[0x8825] = {1: "N", 2: (12.0, 0.0, 0.0)}
        img.save(out, format="JPEG", exif=e)
    else:
        img.save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def _media_dir(tmp_path: Path) -> Iterator[None]:
    prev = settings.media_dir
    settings.media_dir = str(tmp_path / "media")
    yield
    settings.media_dir = prev


# ── store ────────────────────────────────────────────────────────────────────
async def test_store_roundtrip_and_key_safety(tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path)
    await store.put(b"abc", "org/x/2026/10/a.jpg")
    assert await store.open("org/x/2026/10/a.jpg") == b"abc"
    assert await store.size("org/x/2026/10/a.jpg") == 3
    await store.delete("org/x/2026/10/a.jpg")
    await store.delete("org/x/2026/10/a.jpg")  # idempotent
    for bad in ("../evil.jpg", "/abs.jpg", "a/../../b.jpg", "", "c:/x.jpg", "a\\b.jpg"):
        with pytest.raises(ValueError):
            await store.put(b"x", bad)


# ── sanitiser ────────────────────────────────────────────────────────────────
def test_sanitize_strips_exif_and_gps() -> None:
    raw = _jpeg(exif=True)
    assert b"SecretCam" in raw
    out = sanitize_image(raw)
    assert out.mime == "image/jpeg"
    assert b"SecretCam" not in out.data
    assert not Image.open(io.BytesIO(out.data)).getexif()


def test_sanitize_resizes_to_1600() -> None:
    out = sanitize_image(_jpeg((3200, 1600)))
    assert (out.width, out.height) == (1600, 800)


def test_sanitize_png_without_alpha_becomes_jpeg_with_alpha_stays_png() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    assert sanitize_image(buf.getvalue()).mime == "image/jpeg"
    buf = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buf, format="PNG")
    assert sanitize_image(buf.getvalue()).mime == "image/png"


def test_sanitize_gif_first_frame() -> None:
    frames = [Image.new("P", (8, 8), i) for i in (1, 2)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    assert sanitize_image(buf.getvalue()).mime == "image/jpeg"


@pytest.mark.parametrize(
    "raw",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"<html><script>x</script></html>",
        b"",
        b"GIF89a" + b"\x00" * 4,  # right magic, not an image
        b"\xff\xd8\xff" + b"not really a jpeg",
    ],
)
def test_sanitize_rejects_non_images(raw: bytes) -> None:
    with pytest.raises(AppError) as exc:
        sanitize_image(raw)
    assert exc.value.code == "media.invalid_image"


def test_sanitize_drops_appended_payload_by_reencoding() -> None:
    out = sanitize_image(_jpeg() + b"<?php system($_GET['c']); ?>")
    assert b"<?php" not in out.data


def test_sanitize_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "media_max_upload_bytes", 100)
    with pytest.raises(AppError) as exc:
        sanitize_image(_jpeg((200, 200)))
    assert exc.value.code == "media.too_large"


def test_sanitize_rejects_decompression_bomb() -> None:
    buf = io.BytesIO()
    Image.new("1", (9000, 9000)).save(buf, format="PNG")  # tiny file, 81 Mpx
    with pytest.raises(AppError) as exc:
        sanitize_image(buf.getvalue())
    assert exc.value.code == "media.invalid_image"


# ── signed URLs ──────────────────────────────────────────────────────────────
def test_signed_url_valid_expired_tampered_wrong_id() -> None:
    mid = uuid.uuid4()
    exp, sig = urls.sign(mid)
    assert urls.verify(mid, exp, sig)
    assert not urls.verify(mid, exp, sig[:-1] + ("0" if sig[-1] != "0" else "1"))
    assert not urls.verify(uuid.uuid4(), exp, sig)
    assert not urls.verify(mid, exp + 1, sig)
    old_exp, old_sig = urls.sign(mid, ttl=-1)
    assert old_exp < time.time()
    assert not urls.verify(mid, old_exp, old_sig)


# ── service + routes ─────────────────────────────────────────────────────────
async def _org_headers(client: AsyncClient, email: str) -> tuple[dict[str, str], uuid.UUID]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    o = await client.post("/v1/orgs", json={"name": "MediaOrg"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": o.json()["id"]}, uuid.UUID(o.json()["id"])


async def test_routes_auth_and_public_signed(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_id = await _org_headers(client, "media1@example.com")
    asset = await service.save_asset(db_session, org_id, "inbound", _jpeg(), source_channel="whatsapp")
    other_headers, _ = await _org_headers(client, "media2@example.com")

    ok = await client.get(f"/v1/media/{asset.id}", headers=headers)
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/jpeg"
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert "private" in ok.headers["cache-control"]
    assert (await client.get(f"/v1/media/{asset.id}", headers=other_headers)).status_code == 404
    assert (await client.get(f"/v1/media/{asset.id}")).status_code in (401, 403)

    exp, sig = urls.sign(asset.id)
    pub = await client.get(f"/v1/media/public/{asset.id}", params={"exp": exp, "sig": sig})
    assert pub.status_code == 200 and pub.content == ok.content
    bad = await client.get(f"/v1/media/public/{asset.id}", params={"exp": exp, "sig": "0" * 64})
    assert bad.status_code == 404
    old_exp, old_sig = urls.sign(asset.id, ttl=-5)
    expired = await client.get(f"/v1/media/public/{asset.id}", params={"exp": old_exp, "sig": old_sig})
    assert expired.status_code == 404

    await service.delete_asset(db_session, asset)
    await db_session.flush()
    exp, sig = urls.sign(asset.id)
    assert (await client.get(f"/v1/media/public/{asset.id}", params={"exp": exp, "sig": sig})).status_code == 404


async def test_save_asset_dedupes_inbound(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org_headers(client, "media3@example.com")
    raw = _jpeg()
    a = await service.save_asset(db_session, org_id, "inbound", raw)
    b = await service.save_asset(db_session, org_id, "inbound", raw)
    assert a.id == b.id
    assert a.expires_at is not None


async def test_retention_deletes_only_expired_inbound(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org_id = await _org_headers(client, "media4@example.com")
    org = await db_session.get(Organization, org_id)
    assert org is not None
    old = await service.save_asset(db_session, org_id, "inbound", _jpeg((10, 10)))
    fresh = await service.save_asset(db_session, org_id, "inbound", _jpeg((11, 11)))
    product = await service.save_asset(db_session, org, "product", _jpeg((12, 12)))
    old.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    product.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)  # must be ignored: not inbound
    await db_session.flush()
    old_key = old.storage_key

    assert await service.purge_expired(db_session) == 1
    await db_session.flush()
    ids = set((await db_session.execute(select(MediaAsset.id).where(MediaAsset.organization_id == org_id))).scalars())
    assert ids == {fresh.id, product.id}
    assert not (Path(settings.media_dir) / old_key).exists()
    assert (Path(settings.media_dir) / product.storage_key).exists()


async def test_product_assets_count_toward_storage_and_are_released(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.models import OrgStorageUsage

    _, org_id = await _org_headers(client, "media5@example.com")
    org = await db_session.get(Organization, org_id)
    assert org is not None
    inbound = await service.save_asset(db_session, org_id, "inbound", _jpeg((20, 20)))
    product = await service.save_asset(db_session, org, "product", _jpeg((21, 21)))
    used = await db_session.get(OrgStorageUsage, org_id)
    assert used is not None and used.bytes_used == product.bytes  # inbound is not billed
    await service.delete_asset(db_session, product)
    await db_session.flush()
    await db_session.refresh(used)
    assert used.bytes_used == 0
    assert inbound.bytes > 0


async def test_product_upload_blocked_when_storage_full(client: AsyncClient, db_session: AsyncSession) -> None:
    from app.models import OrgStorageUsage

    _, org_id = await _org_headers(client, "media6@example.com")
    org = await db_session.get(Organization, org_id)
    assert org is not None
    org.plan = "trial"
    db_session.add(OrgStorageUsage(organization_id=org_id, bytes_used=10**12, documents_count=0))
    await db_session.flush()
    with pytest.raises(AppError) as exc:
        await service.save_asset(db_session, org, "product", _jpeg())
    assert exc.value.status_code in (402, 413)
