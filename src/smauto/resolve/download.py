"""Fetch remote media to local files."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx

from smauto.config import get_settings
from smauto.logging import get_logger
from smauto.resolve.base import ResolvedMedia, ResolveError

log = get_logger(__name__)

MAX_BYTES = 200 * 1024 * 1024  # a 200 MB source is already far past anything Instagram takes

_EXT_BY_MIME = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def download_media(media: ResolvedMedia, dest_dir: Path, *, stem: str) -> Path:
    """Download `media` into `dest_dir`, returning the local path.

    A resolver that already produced a local file (yt-dlp, manual upload) is
    passed straight through.
    """
    if media.local_path is not None:
        return media.local_path
    if media.url is None:  # pragma: no cover - guarded by ResolvedMedia.__post_init__
        raise ResolveError("media has neither url nor local_path")

    dest_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    with (
        httpx.Client(
            timeout=settings.http_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; smauto/0.1)"},
        ) as client,
        client.stream("GET", media.url) as resp,
    ):
        if resp.status_code != 200:
            raise ResolveError(f"download failed: HTTP {resp.status_code} for {media.url}")

        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip()
        suffix = _suffix_for(media.url, content_type)
        path = dest_dir / f"{stem}{suffix}"

        written = 0
        with path.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                written += len(chunk)
                if written > MAX_BYTES:
                    fh.close()
                    path.unlink(missing_ok=True)
                    raise ResolveError(f"media exceeds {MAX_BYTES // (1024 * 1024)} MB")
                fh.write(chunk)

    if not path.exists() or path.stat().st_size == 0:
        raise ResolveError(f"downloaded an empty file from {media.url}")

    media.local_path = path
    media.mime = media.mime or content_type or mimetypes.guess_type(path.name)[0]
    log.debug("downloaded", url=media.url, path=str(path), bytes=path.stat().st_size)
    return path


def _suffix_for(url: str, content_type: str) -> str:
    if content_type in _EXT_BY_MIME:
        return _EXT_BY_MIME[content_type]
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    if guessed:
        return guessed
    url_suffix = Path(urlparse(url).path).suffix.lower()
    if url_suffix in {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return url_suffix
    return ".bin"


__all__ = ["MAX_BYTES", "download_media"]
