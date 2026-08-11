"""Schedule approved items and publish the ones that are due."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from smauto.config import get_settings
from smauto.db import states
from smauto.db.models import Item
from smauto.db.repo import log_event, mark_failed, transition, utc
from smauto.logging import get_logger
from smauto.publish import instagram, tokens
from smauto.schedule import planner
from smauto.storage.base import Storage, content_type_for, get_storage
from smauto.telegram import delivery

log = get_logger(__name__)


@dataclass
class PublishSummary:
    scheduled: int = 0
    published: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def run(session: Session, *, limit: int = 5, now: datetime | None = None) -> PublishSummary:
    settings = get_settings()
    summary = PublishSummary()

    # Approved items first get a slot.
    for item in session.scalars(
        select(Item).where(Item.status == states.APPROVED).order_by(Item.created_at.asc())
    ).all():
        slot = planner.schedule(session, item, now=now)
        if slot is None:
            summary.skipped += 1
            continue
        transition(
            session,
            item,
            states.SCHEDULED,
            stage="schedule",
            message=f"scheduled for {slot.isoformat()}",
        )
        summary.scheduled += 1

    if settings.delivery_mode != "instagram" or not settings.auto_publish:
        log.info(
            "auto-publish disabled; items stay scheduled",
            delivery_mode=settings.delivery_mode,
            auto_publish=settings.auto_publish,
        )
        return summary

    for item in planner.due_for_publish(session, now=now, limit=limit):
        try:
            publish_item(session, item)
            summary.published += 1
        except Exception as exc:
            retrying = mark_failed(
                session,
                item,
                stage="publishing",
                error=str(exc),
                max_attempts=settings.max_attempts,
            )
            summary.failed += 1
            summary.errors.append(f"#{item.id}: {exc}"[:200])
            delivery.report_problem(session, item, "publishing", str(exc), retrying=retrying)
        session.flush()

    return summary


def publish_item(session: Session, item: Item) -> None:
    """Upload the rendered media somewhere public, then publish it."""
    settings = get_settings()
    transition(session, item, states.PUBLISHING, stage="publishing")

    # Always use the freshest token, not the one baked into the environment.
    settings.ig_access_token = tokens.current_token(session)

    storage = get_storage()
    if not storage.supports_public_urls():
        raise RuntimeError(
            "Instagram needs a public media URL. Set STORAGE_BACKEND=r2 with the R2_* variables."
        )

    caption = delivery.final_caption(item)
    reel_url = _public_url(storage, item, "reel", "mp4")
    feed_url = _public_url(storage, item, "feed", "jpg")

    if reel_url:
        result = instagram.publish_reel(reel_url, caption, cover_url=feed_url)
    elif feed_url:
        result = instagram.publish_image(feed_url, caption)
    else:
        raise RuntimeError(
            "item has no reachable media to publish — the render was neither "
            "uploaded to storage nor still on disk"
        )

    item.ig_media_id = result.media_id
    item.ig_permalink = result.permalink
    item.published_at = datetime.now(UTC)
    transition(session, item, states.PUBLISHED, stage="publishing", message="published")

    delivery.notify(
        item.telegram_chat_id,
        f"🚀 #{item.id} yayınlandı.\n{result.permalink or result.media_id}",
    )


def _public_url(storage: Storage, item: Item, variant: str, suffix: str) -> str | None:
    """Prefer the URL recorded at render time; fall back to uploading now.

    Rendering and publishing happen in separate runs, so by the time we get
    here the local file may be long gone — on GitHub Actions it always is.
    """
    asset = item.asset(variant)
    if asset is None:
        return None
    if asset.remote_url:
        return asset.remote_url

    if asset.local_path and Path(asset.local_path).exists():
        path = Path(asset.local_path)
        url = storage.put(
            path, f"{item.id}/{variant}.{suffix}", content_type=content_type_for(path)
        )
        asset.remote_url = url
        return url

    log.warning("render is gone and was never uploaded", item_id=item.id, variant=variant)
    return None


def unpublish(session: Session, item: Item, *, block_author: bool = True) -> None:
    """Take a post down and stop the source from coming back."""
    from smauto.db.repo import block

    if item.ig_media_id:
        instagram.delete_media(item.ig_media_id)
        log_event(session, stage="unpublish", message="deleted from Instagram", item=item)

    if block_author and item.author_handle:
        block(session, kind="author", value=item.author_handle, reason="takedown request")
    if item.phash:
        block(session, kind="phash", value=item.phash, reason="takedown request")

    transition(
        session,
        item,
        states.REJECTED,
        stage="unpublish",
        message="unpublished",
        reason="takedown",
    )


def pending_publishes(session: Session) -> list[Item]:
    rows = session.scalars(
        select(Item)
        .where(Item.status.in_((states.APPROVED, states.SCHEDULED)))
        .order_by(Item.scheduled_for.asc().nullsfirst())
    ).all()
    return list(rows)


def next_publish_time(session: Session) -> datetime | None:
    for item in pending_publishes(session):
        if item.scheduled_for:
            return utc(item.scheduled_for)
    return None


__all__ = [
    "PublishSummary",
    "next_publish_time",
    "pending_publishes",
    "publish_item",
    "run",
    "unpublish",
]
