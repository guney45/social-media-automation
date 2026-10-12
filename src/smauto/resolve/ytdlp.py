"""Resolve posts with yt-dlp.

Handles X, Instagram and TikTok video posts. X increasingly requires a logged-in
session, so `COOKIES_FILE` is honoured when set — and a cookie failure is
reported distinctly, because the fix (re-export cookies) is different from
every other failure mode.
"""

from __future__ import annotations

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

_COOKIE_HINTS = (
    "nsfw tweet requires authentication",
    "requested content is not available",
    "login required",
    "sign in to confirm",
    "rate-limit reached",
    "no longer available",
    "cookies",
)


class CookiesRequiredError(ResolveError):
    """yt-dlp needs a logged-in session. Surfaced separately so the fix is obvious."""


class YtDlpResolver:
    name = "ytdlp"

    def __init__(self, workdir: Path | None = None) -> None:
        self._workdir = workdir

    def supports(self, url: str) -> bool:
        return detect_platform(url) is not None

    def resolve(self, url: str) -> ResolvedPost:
        import yt_dlp  # imported lazily: it is slow and only needed on this path

        settings = get_settings()
        workdir = self._workdir or (settings.media_dir / "_ytdlp")
        workdir.mkdir(parents=True, exist_ok=True)

        opts: dict[str, Any] = {
            "outtmpl": str(workdir / "%(id)s.%(ext)s"),
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "socket_timeout": settings.http_timeout_seconds,
            "retries": 2,
        }
        if settings.cookies_file and Path(settings.cookies_file).exists():
            opts["cookiefile"] = str(settings.cookies_file)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "private" in lowered or "protected" in lowered:
                raise ProtectedContentError(message[:300]) from exc
            if any(hint in lowered for hint in _COOKIE_HINTS):
                raise CookiesRequiredError(
                    "yt-dlp needs a logged-in session — re-export your browser cookies "
                    f"and set COOKIES_FILE ({message[:200]})"
                ) from exc
            raise ResolveError(message[:400]) from exc

        if not isinstance(info, dict):
            raise ResolveError("yt-dlp returned no metadata")
        if info.get("entries"):
            entries = [e for e in info["entries"] if isinstance(e, dict)]
            if not entries:
                raise ResolveError("yt-dlp returned an empty playlist")
            info = entries[0]

        return _to_post(info, url)


def _to_post(info: dict[str, Any], url: str) -> ResolvedPost:
    platform = detect_platform(url) or "x"

    path = _downloaded_path(info)
    if path is None:
        raise ResolveError("yt-dlp reported no downloaded file")

    handle = info.get("uploader_id") or info.get("channel_id") or info.get("uploader")
    if isinstance(handle, str):
        handle = handle.lstrip("@") or None

    source_id = str(info.get("id") or "")
    if platform == "x":
        parsed = parse_x_url(url)
        if parsed:
            handle = handle or parsed[0]
            source_id = parsed[1]

    duration = info.get("duration")
    return ResolvedPost(
        platform=platform,
        source_id=source_id or path.stem,
        source_url=str(info.get("webpage_url") or url),
        author_handle=handle if isinstance(handle, str) else None,
        author_name=_str_or_none(info.get("uploader") or info.get("channel")),
        author_avatar_url=_str_or_none(info.get("uploader_thumbnail")),
        text=clean_text(_pick_text(info)),
        lang=_str_or_none(info.get("language")),
        created_at=_timestamp(info),
        media=[
            ResolvedMedia(
                kind="video",
                local_path=path,
                width=_int_or_none(info.get("width")),
                height=_int_or_none(info.get("height")),
                duration_ms=int(float(duration) * 1000) if duration else None,
                mime="video/mp4",
            )
        ],
        resolver="ytdlp",
        raw={k: v for k, v in info.items() if k in ("id", "extractor", "webpage_url")},
    )


def _pick_text(info: dict[str, Any]) -> str | None:
    """Prefer the real post text; yt-dlp falls back to a synthesised title."""
    description = info.get("description")
    if isinstance(description, str) and description.strip():
        return description
    title = info.get("title")
    if isinstance(title, str) and title.strip():
        # Twitter titles look like "Display Name - the actual tweet text".
        _, sep, remainder = title.partition(" - ")
        return remainder if sep and remainder.strip() else title
    return None


def _downloaded_path(info: dict[str, Any]) -> Path | None:
    requested = info.get("requested_downloads")
    if isinstance(requested, list):
        for entry in requested:
            if isinstance(entry, dict) and entry.get("filepath"):
                candidate = Path(str(entry["filepath"]))
                if candidate.exists():
                    return candidate
    for key in ("filepath", "_filename"):
        value = info.get(key)
        if value:
            candidate = Path(str(value))
            if candidate.exists():
                return candidate
    return None


def _timestamp(info: dict[str, Any]) -> datetime | None:
    ts = info.get("timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=UTC)
    upload_date = info.get("upload_date")
    if isinstance(upload_date, str) and len(upload_date) == 8:
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and value else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


__all__ = ["CookiesRequiredError", "YtDlpResolver"]
