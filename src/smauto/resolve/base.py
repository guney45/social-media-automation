"""The contract every media resolver implements."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

MediaKind = Literal["image", "video", "gif"]
Platform = Literal["x", "instagram", "tiktok", "manual"]


class ResolveError(RuntimeError):
    """A resolver could not handle this URL. The chain moves on to the next one."""


class ProtectedContentError(ResolveError):
    """Source is private/protected. The item is rejected, not retried."""


@dataclass(slots=True)
class ResolvedMedia:
    kind: MediaKind
    url: str | None = None
    local_path: Path | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    mime: str | None = None

    def __post_init__(self) -> None:
        if self.url is None and self.local_path is None:
            raise ValueError("ResolvedMedia needs either url or local_path")


@dataclass(slots=True)
class ResolvedPost:
    platform: Platform
    source_id: str
    source_url: str
    author_handle: str | None = None
    author_name: str | None = None
    author_avatar_url: str | None = None
    text: str | None = None
    lang: str | None = None
    created_at: datetime | None = None
    media: list[ResolvedMedia] = field(default_factory=list)
    is_protected: bool = False
    quoted: ResolvedPost | None = None
    resolver: str = ""
    raw: dict[str, object] | None = None

    @property
    def has_video(self) -> bool:
        return any(m.kind in ("video", "gif") for m in self.media)

    @property
    def primary_media(self) -> ResolvedMedia | None:
        for m in self.media:
            if m.kind in ("video", "gif"):
                return m
        return self.media[0] if self.media else None


@runtime_checkable
class Resolver(Protocol):
    name: str

    def supports(self, url: str) -> bool: ...

    def resolve(self, url: str) -> ResolvedPost: ...


# ----------------------------------------------------------------------
# Shared text handling
# ----------------------------------------------------------------------

_TCO = re.compile(r"\s*https?://t\.co/\w+\s*")
_TRAILING_URL = re.compile(r"\s*https?://\S+\s*$")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def clean_text(raw: str | None, *, drop_trailing_urls: bool = True) -> str | None:
    """Normalise post text for display on a card.

    Strips the t.co shortlinks X appends for attached media, decodes HTML
    entities, and collapses runs of blank lines.
    """
    if raw is None:
        return None
    text = html.unescape(raw)
    text = _TCO.sub(" ", text)
    if drop_trailing_urls:
        # Only trailing links — links inside a sentence are part of the joke.
        while True:
            stripped = _TRAILING_URL.sub("", text)
            if stripped == text:
                break
            text = stripped
    text = _MULTI_NEWLINE.sub("\n\n", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip() or None


_URL_IDS = (
    re.compile(r"(?:twitter|x)\.com/(?P<handle>[^/]+)/status(?:es)?/(?P<id>\d+)"),
    re.compile(r"(?:twitter|x)\.com/i/web/status/(?P<id>\d+)"),
)


def parse_x_url(url: str) -> tuple[str | None, str] | None:
    """Return (handle, tweet_id) for an X/Twitter status URL, or None."""
    for pattern in _URL_IDS:
        m = pattern.search(url)
        if m:
            groups = m.groupdict()
            return groups.get("handle"), groups["id"]
    return None


def detect_platform(url: str) -> Platform | None:
    lowered = url.lower()
    if "twitter.com" in lowered or "x.com" in lowered or "fxtwitter.com" in lowered:
        return "x"
    if "instagram.com" in lowered:
        return "instagram"
    if "tiktok.com" in lowered:
        return "tiktok"
    return None


__all__ = [
    "MediaKind",
    "Platform",
    "ProtectedContentError",
    "ResolveError",
    "ResolvedMedia",
    "ResolvedPost",
    "Resolver",
    "clean_text",
    "detect_platform",
    "parse_x_url",
]
