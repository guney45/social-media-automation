"""Resolve image posts with gallery-dl.

yt-dlp is the video specialist; gallery-dl is the one that reliably pulls the
full-resolution photo set off a post.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smauto.config import get_settings
from smauto.logging import get_logger
from smauto.resolve.base import (
    ProtectedContentError,
    ResolvedMedia,
    ResolvedPost,
    ResolveError,
    clean_text,
    detect_platform,
    parse_x_url,
)

log = get_logger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class GalleryDlResolver:
    name = "gallerydl"

    def __init__(self, workdir: Path | None = None) -> None:
        self._workdir = workdir

    def supports(self, url: str) -> bool:
        return detect_platform(url) is not None and shutil.which("gallery-dl") is not None

    def resolve(self, url: str) -> ResolvedPost:
        binary = shutil.which("gallery-dl")
        if binary is None:
            raise ResolveError("gallery-dl is not installed")

        settings = get_settings()
        workdir = self._workdir or (settings.media_dir / "_gallerydl" / _safe_stem(url))
        workdir.mkdir(parents=True, exist_ok=True)

        metadata = self._dump_metadata(binary, url, settings.http_timeout_seconds)
        files = self._download(binary, url, workdir, settings.http_timeout_seconds)
        if not files:
            raise ResolveError("gallery-dl downloaded no files")

        return _to_post(metadata, files, url)

    def _dump_metadata(self, binary: str, url: str, timeout: int) -> dict[str, Any]:
        cmd = [binary, "--simulate", "--dump-json", url]
        cmd = _with_cookies(cmd)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 2, check=False)
        if proc.returncode != 0:
            _raise_for_output(proc.stderr)
            raise ResolveError(f"gallery-dl metadata failed: {proc.stderr.strip()[:300]}")
        return _first_metadata(proc.stdout)

    def _download(self, binary: str, url: str, workdir: Path, timeout: int) -> list[Path]:
        cmd = [binary, "--dest", str(workdir), "--no-mtime", "-o", "directory=[]", url]
        cmd = _with_cookies(cmd)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 4, check=False)
        if proc.returncode != 0:
            _raise_for_output(proc.stderr)
            raise ResolveError(f"gallery-dl download failed: {proc.stderr.strip()[:300]}")
        return sorted(
            p for p in workdir.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
        )


def _with_cookies(cmd: list[str]) -> list[str]:
    cookies = get_settings().cookies_file
    if cookies and Path(cookies).exists():
        return [*cmd, "--cookies", str(cookies)]
    return cmd


def _raise_for_output(stderr: str) -> None:
    lowered = (stderr or "").lower()
    if "private" in lowered or "protected" in lowered or "not authorized" in lowered:
        raise ProtectedContentError(stderr.strip()[:300])


def _first_metadata(stdout: str) -> dict[str, Any]:
    """gallery-dl emits one JSON document (or a stream of them); take the first object."""
    stdout = (stdout or "").strip()
    if not stdout:
        return {}
    try:
        loaded = json.loads(stdout)
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return {}

    return _find_dict(loaded) or {}


def _find_dict(node: Any, depth: int = 0) -> dict[str, Any] | None:
    if depth > 4:
        return None
    if isinstance(node, dict):
        return node
    if isinstance(node, list):
        for entry in node:
            found = _find_dict(entry, depth + 1)
            if found:
                return found
    return None


def _to_post(meta: dict[str, Any], files: list[Path], url: str) -> ResolvedPost:
    platform = detect_platform(url) or "x"
    author = _as_dict(meta.get("author"))
    user = _as_dict(meta.get("user"))

    handle = (
        author.get("nick") or author.get("name") or user.get("username") or meta.get("username")
    )
    name = author.get("name") or user.get("full_name") or meta.get("fullname")

    source_id = str(meta.get("tweet_id") or meta.get("post_id") or meta.get("id") or "")
    if platform == "x":
        parsed = parse_x_url(url)
        if parsed:
            handle = handle or parsed[0]
            source_id = parsed[1]

    return ResolvedPost(
        platform=platform,
        source_id=source_id or files[0].stem,
        source_url=url,
        author_handle=str(handle).lstrip("@") if handle else None,
        author_name=str(name) if name else None,
        author_avatar_url=_str_or_none(author.get("profile_image") or user.get("profile_pic_url")),
        text=clean_text(_str_or_none(meta.get("content") or meta.get("description"))),
        lang=_str_or_none(meta.get("lang")),
        created_at=_parse_date(meta.get("date")),
        media=[ResolvedMedia(kind="image", local_path=p) for p in files],
        resolver="gallerydl",
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_date(raw: Any) -> datetime | None:
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def _safe_stem(url: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in url)[-60:]


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


__all__ = ["GalleryDlResolver"]
