"""Storage backends.

Instagram only accepts media it can fetch over the public internet, so the
publish path needs a real object store. Everything before that works fine off
the local disk, which is why this is an interface rather than a hard R2
dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


class StorageError(RuntimeError):
    pass


@runtime_checkable
class Storage(Protocol):
    name: str

    def put(self, path: Path, key: str, *, content_type: str | None = None) -> str:
        """Upload `path` under `key`, returning a publicly reachable URL."""

    def delete(self, key: str) -> None: ...

    def public_url(self, key: str) -> str: ...

    def supports_public_urls(self) -> bool:
        """False for backends Instagram cannot fetch from."""


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def get_storage() -> Storage:
    from smauto.config import get_settings

    settings = get_settings()
    if settings.storage_backend == "r2":
        from smauto.storage.r2 import R2Storage

        return R2Storage()

    from smauto.storage.local import LocalStorage

    return LocalStorage(settings.data_dir / "public")


__all__ = ["Storage", "StorageError", "content_type_for", "get_storage"]
