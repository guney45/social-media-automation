"""The processing loop: resolve → screen → render → caption → deliver.

Each stage is a function that takes one item and either advances it or records
why it could not. `process_once` never raises for a single bad item — one
broken post must not stall the queue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from smauto.caption import writer
from smauto.config import Settings, get_settings
from smauto.db import states
from smauto.db.models import Item, MediaAsset
from smauto.db.repo import (
    add_asset,
    claim_retry,
    due_items,
    is_blocked,
    log_event,
    mark_failed,
    transition,
)
from smauto.logging import get_logger
from smauto.media.probe import FFmpegError, MediaInfo, extract_frame, probe
from smauto.render.card import render_follow_card, render_tweet_card
from smauto.render.image import render_feed
from smauto.render.layout import FEED, REEL, max_card_height
from smauto.render.video import render_reel
from smauto.resolve import chain
from smauto.resolve.base import ProtectedContentError, ResolvedPost
from smauto.resolve.download import download_media
from smauto.screen import ai_gate, dedupe
from smauto.storage.base import get_storage
from smauto.telegram import delivery, intake

log = get_logger(__name__)


@dataclass
class ProcessSummary:
    processed: int = 0
    advanced: int = 0
    rejected: int = 0
    failed: int = 0
    delivered: int = 0
    errors: list[str] = field(default_factory=list)


def process_once(session: Session, *, limit: int = 10) -> ProcessSummary:
    """Advance every due item by one stage."""
    summary = ProcessSummary()

    for item in due_items(session, limit=limit):
        summary.processed += 1
        try:
            _advance(session, item, summary)
            session.flush()
        except Exception as exc:
            session.rollback()
            summary.errors.append(f"#{item.id}: {exc}"[:200])
            log.error("unhandled stage error", item_id=item.id, error=str(exc)[:300])

    return summary


def _advance(session: Session, item: Item, summary: ProcessSummary) -> None:
    status = item.status

    if status == states.FAILED:
        if claim_retry(session, item):
            status = item.status
        else:
            return

    if status == states.QUEUED:
        _stage(session, item, summary, stage="fetching", fn=fetch)
    elif status == states.FETCHED:
        _stage(session, item, summary, stage="screening", fn=screen)
    elif status == states.SCREENED:
        _stage(session, item, summary, stage="rendering", fn=render)
    elif status == states.RENDERED:
        _stage(session, item, summary, stage="captioning", fn=caption_and_deliver)


StageFn = Callable[[Session, Item], None]


def _stage(
    session: Session,
    item: Item,
    summary: ProcessSummary,
    *,
    stage: str,
    fn: StageFn,
) -> None:
    settings = get_settings()
    try:
        fn(session, item)
    except ProtectedContentError as exc:
        transition(
            session,
            item,
            states.REJECTED,
            stage=stage,
            message="source is protected",
            reason=f"korumalı/özel hesap: {exc}"[:300],
        )
        summary.rejected += 1
        delivery.notify(item.telegram_chat_id, f"🔒 #{item.id} özel bir hesaptan, atlandı.")
    except _RejectedError as exc:
        transition(
            session, item, states.REJECTED, stage=stage, message="rejected", reason=exc.reason
        )
        summary.rejected += 1
        delivery.notify(item.telegram_chat_id, f"🚫 #{item.id} elendi: {exc.reason}")
    except Exception as exc:
        retrying = mark_failed(
            session, item, stage=stage, error=str(exc), max_attempts=settings.max_attempts
        )
        summary.failed += 1
        delivery.report_problem(session, item, stage, str(exc), retrying=retrying)
    else:
        summary.advanced += 1
        if item.status == states.AWAITING_APPROVAL:
            summary.delivered += 1


class _RejectedError(Exception):
    """Raised by a stage to reject an item for a stated reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ----------------------------------------------------------------------
# Stages
# ----------------------------------------------------------------------
def fetch(session: Session, item: Item) -> None:
    transition(session, item, states.FETCHING, stage="fetching")
    settings = get_settings()

    try:
        result = chain.resolve(item.source_url)
    except chain.ChainExhaustedError as exc:
        transition(
            session,
            item,
            states.NEEDS_MANUAL,
            stage="fetching",
            message=f"all resolvers failed: {exc}"[:300],
        )
        delivery.notify(
            item.telegram_chat_id,
            f"📥 #{item.id} medyasını indiremedim.\n{item.source_url}\n\n"
            "Videoyu/fotoğrafı buraya atarsan devam ederim.",
        )
        return

    post = result.post
    item.author_handle = post.author_handle or item.author_handle
    item.author_name = post.author_name or item.author_name
    item.author_avatar_url = post.author_avatar_url or item.author_avatar_url
    item.text = _display_text(post) or item.text
    item.lang = post.lang or item.lang
    item.source_created_at = post.created_at or item.source_created_at

    media_dir = settings.media_dir / str(item.id)
    for index, media in enumerate(post.media):
        path = download_media(media, media_dir, stem=f"source{index}")
        info = _safe_probe(path)
        add_asset(
            session,
            item,
            kind="source",
            variant="original",
            order_index=index,
            local_path=str(path),
            width=info.width if info else media.width,
            height=info.height if info else media.height,
            duration_ms=info.duration_ms if info else media.duration_ms,
            mime=media.mime,
            size_bytes=path.stat().st_size if path.exists() else None,
        )

    log_event(
        session,
        stage="fetching",
        message="resolved",
        item=item,
        payload={"resolver": post.resolver, "media": len(post.media)},
    )
    transition(session, item, states.FETCHED, stage="fetching")


def screen(session: Session, item: Item) -> None:
    transition(session, item, states.SCREENING, stage="screening")
    settings = get_settings()

    if item.author_handle and is_blocked(session, kind="author", value=item.author_handle):
        raise _RejectedError(f"@{item.author_handle} engel listesinde")

    primary = _primary_source(item)
    still: Path | None = None

    if primary and primary.local_path:
        path = Path(primary.local_path)
        work = settings.media_dir / str(item.id)
        still = _still_frame(path, work)

        result = dedupe.check(session, path, exclude_id=item.id, workdir=work)
        item.phash = result.phash
        if result.duplicate_of is not None:
            item.dupe_of_item_id = result.duplicate_of
            raise _RejectedError(
                f"#{result.duplicate_of} ile aynı içerik (mesafe {result.distance})"
            )

        if result.phash and is_blocked(session, kind="phash", value=result.phash):
            raise _RejectedError("içerik engel listesinde")

    verdict = ai_gate.safe_screen(item.text, still)
    item.ai_score = verdict.funny_score
    item.flags = verdict.flags
    item.ai_ocr_text = verdict.ocr_text or None

    if verdict.skipped and verdict.reason:
        item.add_note(verdict.reason)
    for warning in verdict.warnings:
        item.add_note(f"AI uyarısı: {warning}")

    accepted, reason = ai_gate.decide(verdict)
    if not accepted:
        raise _RejectedError(reason or "içerik filtresi")

    transition(session, item, states.SCREENED, stage="screening")


def render(session: Session, item: Item) -> None:
    transition(session, item, states.RENDERING, stage="rendering")
    settings = get_settings()

    out_dir = settings.media_dir / str(item.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    primary = _primary_source(item)
    source_path = Path(primary.local_path) if primary and primary.local_path else None
    source_info = _safe_probe(source_path) if source_path else None
    is_video = bool(source_info and source_info.is_video)

    # Drawn before the tweet card: its size is what tells the card renderer how
    # much height is left, so the card can be drawn shorter but still full-width
    # and legible rather than being scaled down afterwards.
    follow_path: Path | None = None
    follow_size: tuple[int, int] | None = None
    if _follow_enabled(settings):
        follow = render_follow_card(
            out_dir / "follow.png", theme=intake.theme_for(session, item)  # type: ignore[arg-type]
        )
        follow_path = follow.path
        follow_size = (follow.width, follow.height)
        add_asset(
            session,
            item,
            kind="render",
            variant="follow",
            local_path=str(follow.path),
            width=follow.width,
            height=follow.height,
            mime="image/png",
        )

    card_path: Path | None = None
    if item.text or not source_path:
        frame = REEL if is_video else FEED
        card = render_tweet_card(
            _post_for_card(item),
            out_dir / "card.png",
            theme=intake.theme_for(session, item),  # type: ignore[arg-type]
            max_height=max_card_height(frame, follow_size),
            verified=settings.own_account_verified,
        )
        card_path = card.path
        if card.truncated:
            item.add_note("Tweet metni karta sığmadı, kısaltıldı (tamamı caption'da).")
        add_asset(
            session,
            item,
            kind="render",
            variant="card",
            local_path=str(card.path),
            width=card.width,
            height=card.height,
            mime="image/png",
        )

    if is_video and source_path and settings.render_reel:
        result = render_reel(source_path, card_path, out_dir / "reel.mp4", follow=follow_path)
        for note in result.notes:
            item.add_note(note)
        add_asset(
            session,
            item,
            kind="render",
            variant="reel",
            local_path=str(result.path),
            width=result.width,
            height=result.height,
            duration_ms=result.duration_ms,
            mime="video/mp4",
            size_bytes=result.path.stat().st_size,
        )

    if settings.render_feed and (not is_video or not settings.render_reel):
        feed = render_feed(source_path, card_path, out_dir / "feed.jpg", follow=follow_path)
        add_asset(
            session,
            item,
            kind="render",
            variant="feed",
            local_path=str(feed.path),
            width=feed.width,
            height=feed.height,
            mime="image/jpeg",
            size_bytes=feed.path.stat().st_size,
        )

    if not item.asset("reel") and not item.asset("feed"):
        raise RuntimeError("render produced no output")

    transition(session, item, states.RENDERED, stage="rendering")


def caption_and_deliver(session: Session, item: Item) -> None:
    transition(session, item, states.CAPTIONING, stage="captioning")

    if not item.caption_override:
        result = writer.safe_write(item.text, item.ai_ocr_text or "")
        item.ai_caption = result.caption
        item.hashtags = result.hashtags
        if not result.generated:
            item.add_note("Caption AI olmadan üretildi.")

    _upload_renders(session, item)
    delivery.send_for_review(session, item)


def _upload_renders(session: Session, item: Item) -> None:
    """Push rendered media to object storage while the files still exist.

    Publishing happens in a later run — possibly on a different machine — so
    waiting until then would mean uploading files that are already gone. A
    failed upload is not fatal: the item can still be reviewed and posted by
    hand, and `publish` re-uploads from disk when it can.
    """
    if get_settings().delivery_mode != "instagram":
        return

    try:
        storage = get_storage()
        if not storage.supports_public_urls():
            return
    except Exception as exc:
        log.warning("storage unavailable", item_id=item.id, error=str(exc)[:200])
        return

    for variant, suffix, content_type in (
        ("reel", "mp4", "video/mp4"),
        ("feed", "jpg", "image/jpeg"),
    ):
        asset = item.asset(variant)
        if asset is None or asset.remote_url or not asset.local_path:
            continue
        path = Path(asset.local_path)
        if not path.exists():
            continue
        try:
            asset.remote_url = storage.put(
                path, f"{item.id}/{variant}.{suffix}", content_type=content_type
            )
        except Exception as exc:
            log.warning("upload failed", item_id=item.id, variant=variant, error=str(exc)[:200])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _primary_source(item: Item) -> MediaAsset | None:
    sources = sorted(item.source_assets(), key=lambda a: a.order_index)
    if not sources:
        return None
    for asset in sources:
        if asset.is_video:
            return asset
    return sources[0]


def _still_frame(path: Path, workdir: Path) -> Path | None:
    """A representative image of the media, for hashing and vision screening."""
    info = _safe_probe(path)
    if info is None:
        return path if path.exists() else None
    if not info.is_video:
        return path
    try:
        return extract_frame(path, workdir / "still.jpg")
    except FFmpegError as exc:
        log.info("frame extraction failed", path=str(path), error=str(exc)[:160])
        return None


def _safe_probe(path: Path) -> MediaInfo | None:
    try:
        return probe(path)
    except FFmpegError:
        return None


def _follow_enabled(settings: Settings) -> bool:
    """Only add the follow card once there is something to show on it."""
    return bool(settings.own_account_handle or settings.own_account_avatar)


def _display_text(post: ResolvedPost) -> str | None:
    """The card shows one voice — a quoted tweet's text is folded straight in,
    never as a nested card crediting its own author."""
    parts = [t for t in (post.text, post.quoted.text if post.quoted else None) if t and t.strip()]
    return "\n\n".join(parts) or None


def _post_for_card(item: Item) -> ResolvedPost:
    """The card always shows your own account, never the source's."""
    settings = get_settings()
    return ResolvedPost(
        platform=item.source_platform,  # type: ignore[arg-type]
        source_id=item.source_id,
        source_url=item.source_url,
        author_handle=settings.own_account_handle or item.author_handle,
        author_name=settings.own_account_name or item.author_name,
        author_avatar_url=settings.own_account_avatar or item.author_avatar_url,
        text=item.text,
        lang=item.lang,
        created_at=item.created_at,
        media=[],
    )


__all__ = [
    "ProcessSummary",
    "caption_and_deliver",
    "fetch",
    "process_once",
    "render",
    "screen",
]
