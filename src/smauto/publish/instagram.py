"""Publish to Instagram via the Graph API.

Uses the Instagram API with Instagram Login: a Facebook Page is not required,
and while the Meta app stays in Development mode with your own account added as
an Instagram Tester, neither is App Review.

Publishing is always three steps: create a container, poll it until Instagram
has finished ingesting the media, then publish the container.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from smauto.config import get_settings
from smauto.logging import get_logger

log = get_logger(__name__)

GRAPH_VERSION = "v23.0"
GRAPH_ROOT = f"https://graph.instagram.com/{GRAPH_VERSION}"

#: Instagram ingests video asynchronously; this is how long we wait for it.
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 300

MediaKind = Literal["REELS", "IMAGE", "STORIES", "CAROUSEL"]


class InstagramError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class PublishResult:
    media_id: str
    permalink: str | None = None


def publish_reel(video_url: str, caption: str, *, cover_url: str | None = None) -> PublishResult:
    params: dict[str, Any] = {"media_type": "REELS", "video_url": video_url, "caption": caption}
    if cover_url:
        params["cover_url"] = cover_url
    return _create_and_publish(params)


def publish_image(image_url: str, caption: str) -> PublishResult:
    return _create_and_publish({"image_url": image_url, "caption": caption})


def publish_story(media_url: str, *, is_video: bool) -> PublishResult:
    key = "video_url" if is_video else "image_url"
    return _create_and_publish({"media_type": "STORIES", key: media_url})


def publish_carousel(image_urls: list[str], caption: str) -> PublishResult:
    """Up to 10 children, each its own container, then one carousel container."""
    if not 2 <= len(image_urls) <= 10:
        raise InstagramError("a carousel needs between 2 and 10 items", retryable=False)

    children: list[str] = []
    for url in image_urls:
        child = _create_container({"image_url": url, "is_carousel_item": "true"})
        children.append(child)

    container = _create_container(
        {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
    )
    _await_container(container)
    return _publish_container(container)


# ----------------------------------------------------------------------
def _create_and_publish(params: dict[str, Any]) -> PublishResult:
    container = _create_container(params)
    _await_container(container)
    return _publish_container(container)


def _create_container(params: dict[str, Any]) -> str:
    settings = get_settings()
    payload = {**params, "access_token": settings.ig_access_token}
    body = _post(f"{GRAPH_ROOT}/{settings.ig_user_id}/media", payload)
    container_id = body.get("id")
    if not container_id:
        raise InstagramError(f"container creation returned no id: {body}")
    log.info("ig container created", container_id=container_id, kind=params.get("media_type"))
    return str(container_id)


def _await_container(container_id: str, *, timeout_s: int | None = None) -> None:
    """Block until Instagram reports the container is ready to publish."""
    settings = get_settings()
    timeout_s = POLL_TIMEOUT_S if timeout_s is None else timeout_s
    deadline = time.monotonic() + timeout_s

    while True:
        body = _get(
            f"{GRAPH_ROOT}/{container_id}",
            {"fields": "status_code,status", "access_token": settings.ig_access_token},
        )
        status = str(body.get("status_code") or "")

        if status == "FINISHED":
            return
        if status == "ERROR":
            raise InstagramError(
                f"Instagram rejected the media: {body.get('status') or 'unknown'}",
                retryable=False,
            )
        if status == "EXPIRED":
            raise InstagramError("container expired before publishing", retryable=True)

        if time.monotonic() > deadline:
            raise InstagramError(f"container still {status or 'unknown'} after {timeout_s}s")

        time.sleep(POLL_INTERVAL_S)


def _publish_container(container_id: str) -> PublishResult:
    settings = get_settings()
    body = _post(
        f"{GRAPH_ROOT}/{settings.ig_user_id}/media_publish",
        {"creation_id": container_id, "access_token": settings.ig_access_token},
    )
    media_id = body.get("id")
    if not media_id:
        raise InstagramError(f"publish returned no media id: {body}")

    permalink = None
    try:
        info = _get(
            f"{GRAPH_ROOT}/{media_id}",
            {"fields": "permalink", "access_token": settings.ig_access_token},
        )
        permalink = info.get("permalink")
    except InstagramError:  # pragma: no cover - the post is already live
        pass

    log.info("published to instagram", media_id=media_id, permalink=permalink)
    return PublishResult(media_id=str(media_id), permalink=permalink)


def delete_media(media_id: str) -> None:
    """Used by /unpublish when a takedown request comes in."""
    settings = get_settings()
    _request("DELETE", f"{GRAPH_ROOT}/{media_id}", {"access_token": settings.ig_access_token})


# ----------------------------------------------------------------------
def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", url, payload)


def _get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    return _request("GET", url, params)


def _request(method: str, url: str, params: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(timeout=max(settings.http_timeout_seconds, 60)) as client:
            if method == "GET":
                resp = client.get(url, params=params)
            elif method == "DELETE":
                resp = client.delete(url, params=params)
            else:
                resp = client.post(url, data=params)
    except httpx.HTTPError as exc:
        raise InstagramError(f"transport error: {exc!r}") from exc

    try:
        body = resp.json()
    except ValueError as exc:
        raise InstagramError(f"non-JSON response (HTTP {resp.status_code})") from exc

    if resp.status_code >= 400 or "error" in body:
        raise InstagramError(_describe(body, resp.status_code), retryable=_is_retryable(body))
    return dict(body)


def _describe(body: dict[str, Any], status_code: int) -> str:
    error = body.get("error") or {}
    if not isinstance(error, dict):
        return f"HTTP {status_code}: {body}"
    parts = [str(error.get("message") or f"HTTP {status_code}")]
    for key in ("code", "error_subcode", "error_user_msg"):
        if error.get(key):
            parts.append(f"{key}={error[key]}")
    return " | ".join(parts)


def _is_retryable(body: dict[str, Any]) -> bool:
    error = body.get("error") or {}
    code = error.get("code") if isinstance(error, dict) else None
    # 190 = invalid/expired token, 100 = bad parameter. Retrying either is pointless.
    return code not in (190, 100)


__all__ = [
    "GRAPH_ROOT",
    "GRAPH_VERSION",
    "InstagramError",
    "PublishResult",
    "delete_media",
    "publish_carousel",
    "publish_image",
    "publish_reel",
    "publish_story",
]
