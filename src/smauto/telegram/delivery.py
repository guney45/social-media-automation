"""Send finished items back to Telegram for review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from smauto.config import get_settings
from smauto.db import states
from smauto.db.models import Item
from smauto.db.repo import log_event, transition
from smauto.logging import get_logger
from smauto.telegram.client import (
    MAX_UPLOAD_BYTES,
    InlineButton,
    TelegramClient,
    TelegramError,
    keyboard,
)

log = get_logger(__name__)

ACTION_APPROVE = "ok"
ACTION_CAPTION = "cap"
ACTION_REDO = "redo"
ACTION_REJECT = "no"


def review_keyboard(item_id: int) -> dict[str, Any]:
    return keyboard(
        [
            [
                InlineButton("✅ Onayla", f"a:{item_id}:{ACTION_APPROVE}"),
                InlineButton("✏️ Caption", f"a:{item_id}:{ACTION_CAPTION}"),
            ],
            [
                InlineButton("🔁 Yeniden çiz", f"a:{item_id}:{ACTION_REDO}"),
                InlineButton("❌ Sil", f"a:{item_id}:{ACTION_REJECT}"),
            ],
        ]
    )


def final_caption(item: Item) -> str:
    """Caption plus the mandatory credit line and hashtags."""
    body = (item.caption_override or item.ai_caption or "").strip()
    parts: list[str] = [body] if body else []

    if item.author_handle:
        parts.append(f"📍 @{item.author_handle} via {_platform_label(item.source_platform)}")

    tags = item.hashtags
    if tags:
        parts.append(" ".join(tags))

    return "\n\n".join(parts).strip()


def preview_text(item: Item) -> str:
    kind = "🎬 Reels" if item.asset("reel") else "🖼 Görsel"
    bits = [f"{kind} hazır"]
    if item.author_handle:
        bits.append(f"@{item.author_handle}")
    if item.ai_score is not None:
        bits.append(f"komiklik {item.ai_score}/100")
    header = "  ·  ".join(bits)

    body = final_caption(item)
    notes = item.note_list
    warn = "\n".join(f"⚠️ {n}" for n in notes)

    return "\n\n".join(p for p in (header, body, warn) if p)


def send_for_review(session: Session, item: Item, *, client: TelegramClient | None = None) -> None:
    """Deliver the rendered item with approval buttons, then park it."""
    settings = get_settings()
    chat_id = item.telegram_chat_id or settings.telegram_target_chat_id
    if chat_id is None:
        raise ValueError("no chat to deliver to (set TELEGRAM_TARGET_CHAT_ID)")

    own_client = client is None
    tg = client or TelegramClient()
    try:
        markup = review_keyboard(item.id)
        caption = preview_text(item)
        message = _send_best_asset(tg, chat_id, item, caption=caption, markup=markup)

        item.telegram_chat_id = chat_id
        item.telegram_message_id = message.get("message_id") if message else None
        transition(
            session,
            item,
            states.AWAITING_APPROVAL,
            stage="deliver",
            message="sent to Telegram for review",
        )
    finally:
        if own_client:
            tg.close()


def _send_best_asset(
    tg: TelegramClient,
    chat_id: int,
    item: Item,
    *,
    caption: str,
    markup: dict[str, Any],
) -> dict[str, Any] | None:
    reel = item.asset("reel")
    feed = item.asset("feed")

    if reel and reel.local_path:
        path = Path(reel.local_path)
        if path.exists():
            if path.stat().st_size <= MAX_UPLOAD_BYTES:
                return tg.send_video(
                    chat_id,
                    path,
                    caption=caption,
                    reply_markup=markup,
                    width=reel.width,
                    height=reel.height,
                    duration_s=int((reel.duration_ms or 0) / 1000) or None,
                )
            # Too big for the bot upload limit: send the still and point at the file.
            log.warning("reel exceeds Telegram upload limit", item_id=item.id, path=str(path))
            note = (
                f"\n\n⚠️ Video {path.stat().st_size / 1e6:.0f} MB — Telegram'ın 50 MB bot "
                f"sınırını aşıyor. Dosya: `{path}`"
            )
            if feed and feed.local_path and Path(feed.local_path).exists():
                return tg.send_photo(
                    chat_id, Path(feed.local_path), caption=caption + note, reply_markup=markup
                )
            return tg.send_message(chat_id, caption + note, reply_markup=markup)

    if feed and feed.local_path:
        path = Path(feed.local_path)
        if path.exists():
            return tg.send_photo(chat_id, path, caption=caption, reply_markup=markup)

    return tg.send_message(chat_id, caption, reply_markup=markup)


def notify(chat_id: int | None, text: str, *, client: TelegramClient | None = None) -> None:
    """Best-effort user notification. Never raises — notifications are not the job."""
    settings = get_settings()
    target = chat_id or settings.telegram_target_chat_id
    if target is None or not settings.telegram_bot_token:
        log.info("notification skipped (no chat configured)", text=text[:120])
        return

    own_client = client is None
    try:
        tg = client or TelegramClient()
        try:
            tg.send_message(target, text)
        finally:
            if own_client:
                tg.close()
    except (TelegramError, ValueError) as exc:
        log.warning("notification failed", error=str(exc)[:200])


def report_problem(session: Session, item: Item, stage: str, error: str, *, retrying: bool) -> None:
    """Tell the user an item broke, and whether the system will try again."""
    label = states.LABELS.get(stage, stage)
    tail = "Tekrar denenecek." if retrying else "Deneme hakkı bitti."
    text = f"⚠️ İşlem hatası ({label})\n{item.source_url}\n\n{error[:400]}\n\n{tail}"
    notify(item.telegram_chat_id, text)
    log_event(session, stage=stage, message="user notified of failure", item=item, level="warning")


def _platform_label(platform: str) -> str:
    return {"x": "X", "instagram": "Instagram", "tiktok": "TikTok"}.get(platform, platform)


__all__ = [
    "ACTION_APPROVE",
    "ACTION_CAPTION",
    "ACTION_REDO",
    "ACTION_REJECT",
    "final_caption",
    "notify",
    "preview_text",
    "report_problem",
    "review_keyboard",
    "send_for_review",
]
