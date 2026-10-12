"""Resolve X/Twitter posts through the FixTweet (fxtwitter) JSON API.

Free, unauthenticated and fast. It is the first link in the chain; when it is
down or rate-limited the chain falls through to yt-dlp/gallery-dl and finally
to a manual upload, so nothing here is load-bearing on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from smauto.config import get_settings
from smauto.logging import get_logger
from smauto.resolve.base import (
    ProtectedContentError,
    ResolvedMedia,
    ResolvedPost,
    ResolveError,
    clean_text,
    parse_x_url,
)

log = get_logger(__name__)

#: Tried in order — both projects expose the same shape for `/status/<id>`.
ENDPOINTS = (
    "https://api.fxtwitter.com/status/{id}",
    "https://api.vxtwitter.com/i/status/{id}",
)


class FxTwitterResolver:
    name = "fxtwitter"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def supports(self, url: str) -> bool:
        return parse_x_url(url) is not None

    def resolve(self, url: str) -> ResolvedPost:
        parsed = parse_x_url(url)
        if parsed is None:
            raise ResolveError(f"not an X status url: {url}")
        _handle, tweet_id = parsed

        settings = get_settings()
        client = self._client or httpx.Client(
            timeout=settings.http_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "smauto/0.1 (+https://github.com/guney45/social-media-automation)"
            },
        )
        owns_client = self._client is None

        errors: list[str] = []
        try:
            for template in ENDPOINTS:
                endpoint = template.format(id=tweet_id)
                try:
                    resp = client.get(endpoint)
                except httpx.HTTPError as exc:
                    errors.append(f"{endpoint}: {exc!r}")
                    continue

                if resp.status_code == 401:
                    raise ProtectedContentError("tweet is protected or requires login")
                if resp.status_code != 200:
                    errors.append(f"{endpoint}: HTTP {resp.status_code}")
                    continue

                try:
                    payload = resp.json()
                except ValueError as exc:
                    errors.append(f"{endpoint}: invalid json ({exc})")
                    continue

                try:
                    return _parse(payload, fallback_url=url, tweet_id=tweet_id)
                except ProtectedContentError:
                    raise
                except Exception as exc:
                    errors.append(f"{endpoint}: unexpected shape ({exc})")
                    continue
        finally:
            if owns_client:
                client.close()

        raise ResolveError("; ".join(errors) or "no endpoint answered")


def _parse(payload: dict[str, Any], *, fallback_url: str, tweet_id: str) -> ResolvedPost:
    tweet = _tweet_node(payload)
    if tweet is None:
        raise ResolveError("no tweet object in response")

    post = _to_post(tweet, fallback_url=fallback_url, tweet_id=tweet_id)
    quoted_raw = tweet.get("quote") or tweet.get("quoted_status")
    if isinstance(quoted_raw, dict):
        try:
            post.quoted = _to_post(quoted_raw, fallback_url="", tweet_id="")
        except Exception:
            post.quoted = None
    return post


def _tweet_node(payload: dict[str, Any]) -> dict[str, Any] | None:
    # fxtwitter: {"code":200,"tweet":{...}}   vxtwitter: {...} flat
    if isinstance(payload.get("tweet"), dict):
        return payload["tweet"]  # type: ignore[no-any-return]
    if "text" in payload or "full_text" in payload:
        return payload
    return None


def _to_post(node: dict[str, Any], *, fallback_url: str, tweet_id: str) -> ResolvedPost:
    author = _as_dict(node.get("author"))

    handle = (
        author.get("screen_name")
        or author.get("screenName")
        or node.get("user_screen_name")
        or None
    )
    name = author.get("name") or node.get("user_name") or None
    avatar = (
        author.get("avatar_url")
        or author.get("avatar")
        or node.get("user_profile_image_url")
        or None
    )

    if author.get("protected") is True:
        raise ProtectedContentError("author account is protected")

    source_id = str(node.get("id") or node.get("id_str") or tweet_id)
    url = node.get("url") or node.get("tweetURL") or fallback_url
    if not url and handle and source_id:
        url = f"https://x.com/{handle}/status/{source_id}"

    return ResolvedPost(
        platform="x",
        source_id=source_id,
        source_url=str(url),
        author_handle=str(handle) if handle else None,
        author_name=str(name) if name else None,
        author_avatar_url=str(avatar) if avatar else None,
        text=clean_text(node.get("text") or node.get("full_text")),
        lang=_str_or_none(node.get("lang")),
        created_at=_parse_created_at(node),
        media=_parse_media(node),
        resolver="fxtwitter",
        raw=node,
    )


def _parse_media(node: dict[str, Any]) -> list[ResolvedMedia]:
    out: list[ResolvedMedia] = []
    media = node.get("media")

    # fxtwitter: {"media": {"all": [...], "photos": [...], "videos": [...]}}
    entries: list[Any] = []
    if isinstance(media, dict):
        if isinstance(media.get("all"), list):
            entries = list(media["all"])
        else:
            for key in ("videos", "photos"):
                if isinstance(media.get(key), list):
                    entries.extend(media[key])
    elif isinstance(media, list):  # vxtwitter: {"media_extended": [...]}
        entries = list(media)

    if not entries and isinstance(node.get("media_extended"), list):
        entries = list(node["media_extended"])

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_type = str(entry.get("type") or "").lower()
        if raw_type in ("video", "gif", "animated_gif"):
            kind = "gif" if "gif" in raw_type else "video"
        elif raw_type in ("photo", "image"):
            kind = "image"
        else:
            continue

        url = entry.get("url") or entry.get("media_url_https") or entry.get("thumbnail_url")
        if not url:
            continue

        duration = entry.get("duration") or entry.get("duration_millis")
        duration_ms: int | None = None
        if isinstance(duration, (int, float)):
            # fxtwitter reports seconds, the legacy field reports milliseconds.
            duration_ms = int(duration * 1000) if duration < 10_000 else int(duration)

        size = _as_dict(entry.get("size"))
        out.append(
            ResolvedMedia(
                kind=kind,  # type: ignore[arg-type]
                url=str(url),
                width=_int_or_none(entry.get("width") or size.get("width")),
                height=_int_or_none(entry.get("height") or size.get("height")),
                duration_ms=duration_ms,
            )
        )

    # Deduplicate while preserving order (fxtwitter lists videos twice sometimes).
    seen: set[str] = set()
    unique: list[ResolvedMedia] = []
    for m in out:
        key = m.url or ""
        if key in seen:
            continue
        seen.add(key)
        unique.append(m)
    return unique


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_created_at(node: dict[str, Any]) -> datetime | None:
    epoch = node.get("created_timestamp") or node.get("date_epoch")
    if isinstance(epoch, (int, float)):
        return datetime.fromtimestamp(float(epoch), tz=UTC)

    raw = node.get("created_at") or node.get("date")
    if isinstance(raw, str):
        for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(raw, fmt)
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and value else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


__all__ = ["ENDPOINTS", "FxTwitterResolver"]
