"""Local filesystem storage. Fine for everything except Instagram publishing."""

from __future__ import annotations

import shutil
from pathlib import Path

from smauto.logging import get_logger
from smauto.storage.base import StorageError

log = get_logger(__name__)


class LocalStorage:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, path: Path, key: str, *, content_type: str | None = None) -> str:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
        return self.public_url(key)

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)

    def public_url(self, key: str) -> str:
        return (self.root / key).absolute().as_uri()

    def supports_public_urls(self) -> bool:
        return False

    def require_public(self) -> None:
        raise StorageError(
            "Local storage cannot serve Instagram. Set STORAGE_BACKEND=r2 and the R2_* variables."
        )


__all__ = ["LocalStorage"]
