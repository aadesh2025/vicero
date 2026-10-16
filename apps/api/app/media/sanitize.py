"""Every image is untrusted input: validate by magic bytes, then re-encode (docs/26 §8).

Re-encoding is the real defence — it drops EXIF/GPS, any appended payload and polyglot tricks. The
decompression-bomb guard is `Image.MAX_IMAGE_PIXELS`, set before anything is opened.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.core.errors import AppError

MAX_SIDE = 1600
MAX_PIXELS = 40_000_000  # ~ a 6000x6600 photo; far above any phone camera downscaled by Meta
JPEG_QUALITY = 85

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str


def _sniff(raw: bytes) -> str | None:
    if raw[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def _bad(message: str) -> AppError:
    return AppError("media.invalid_image", message, 422)


def sanitize_image(raw: bytes) -> SanitizedImage:
    if len(raw) > settings.media_max_upload_bytes:
        raise AppError("media.too_large", "That image is too large.", 413)
    if not raw or _sniff(raw) is None:
        raise _bad("Only JPEG, PNG, WebP and GIF images are accepted.")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            opened.seek(0)  # GIF: first frame only
            img = ImageOps.exif_transpose(opened) or opened
            img.load()
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            keep_png = _sniff(raw) == "png" and has_alpha
            img = img.convert("RGBA" if keep_png else "RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        raise _bad("That file could not be read as an image.") from exc

    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    if keep_png:
        img.save(out, format="PNG", optimize=True)
        mime = "image/png"
    else:
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        mime = "image/jpeg"
    data = out.getvalue()
    return SanitizedImage(
        data=data, mime=mime, width=img.width, height=img.height, sha256=hashlib.sha256(data).hexdigest()
    )
