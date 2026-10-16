"""Where image bytes live. A protocol so S3/R2 can replace the local disk without touching callers."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.config import settings


class MediaStore(Protocol):
    async def put(self, data: bytes, key: str) -> None: ...
    async def open(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def size(self, key: str) -> int: ...


def make_key(org_id: uuid.UUID, now_year: int, now_month: int, ext: str = "jpg") -> str:
    return f"org/{org_id}/{now_year:04d}/{now_month:02d}/{uuid.uuid4()}.{ext}"


class LocalMediaStore:
    """Files under `root`. Keys are validated so a key can never escape it."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        pure = PurePosixPath(key)
        if not key or pure.is_absolute() or ".." in pure.parts or "\\" in key or ":" in key:
            raise ValueError(f"invalid media key: {key!r}")
        path = (self.root / pure).resolve()
        if self.root not in path.parents:
            raise ValueError(f"invalid media key: {key!r}")
        return path

    async def put(self, data: bytes, key: str) -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)

    async def open(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))

    async def size(self, key: str) -> int:
        return (await asyncio.to_thread(self._path(key).stat)).st_size


def get_store() -> MediaStore:
    return LocalMediaStore(settings.media_dir)
