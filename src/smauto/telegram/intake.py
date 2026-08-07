"""Read Telegram updates: new links, uploaded files, commands and button taps."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from smauto.config import get_settings
from smauto.db import states
from smauto.db.models import Item
from smauto.db.repo import (
    add_asset,
    block,
    find_by_source,
    kv_get,
    kv_set,
    log_event,
    stats_since,
    status_counts,
    transition,
)
from smauto.logging import get_logger
from smauto.resolve.base import detect_platform, parse_x_url
from smauto.telegram import delivery
from smauto.telegram.client import TelegramClient, TelegramError

log = get_logger(__name__)

OFFSET_KEY = "telegram_update_offset"
AWAITING_CAPTION_KEY = "awaiting_caption"

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_CALLBACK_RE = re.compile(r"^a:(?P<item>\d+):(?P<action>\w+)$")

HELP_TEXT = (
    "Komik bir gönderi gördüğünde *Paylaş → Telegram → bu bot*.\n\n"
    "Link atabilirsin (X, Instagram, TikTok) ya da doğrudan video/foto yükleyebilirsin.\n\n"
    "Komutlar:\n"
    "/status — kuyruk durumu\n"
    "/stats — son 7 gün\n"
    "/block @kullanici — kaynağı engelle\n"
    "/help — bu mesaj"
)


@dataclass(slots=True)
class IngestSummary:
    updates: int = 0
    new_items: int = 0
    duplicates: int = 0
    files_attached: int = 0
    commands: int = 0
    callbacks: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)


def ingest(
    session: Session, *, client: TelegramClient | None = None, limit: int = 50
) -> IngestSummary:
    """Drain pending updates into the queue."""
    settings = get_settings()
    settings.require_telegram()

    own_client = client is None
    tg = client or TelegramClient()
    summary = IngestSummary()

    try:
        raw_offset = kv_get(session, OFFSET_KEY)
        offset = int(raw_offset) + 1 if raw_offset else None
        updates = tg.get_updates(offset, limit=limit)
        summary.updates = len(updates)

        highest = int(raw_offset) if raw_offset else 0
        for update in updates:
            highest = max(highest, int(update.get("update_id", 0)))
            try:
                _dispatch(session, tg, update, summary)
            except Exception as exc:
                summary.errors.append(str(exc)[:200])
                log.warning("update handling failed", error=str(exc)[:300])

        if highest:
            kv_set(session, OFFSET_KEY, str(highest))
    finally:
        if own_client:
            tg.close()

    return summary


def _dispatch(
    session: Session, tg: TelegramClient, update: dict[str, Any], summary: IngestSummary
) -> None:
    if "callback_query" in update:
        _handle_callback(session, tg, update["callback_query"], summary)
        return
    message = update.get("message")
    if isinstance(message, dict):
        _handle_message(session, tg, message, summary)
        return
    summary.ignored += 1


# ----------------------------------------------------------------------
# Messages
# ----------------------------------------------------------------------
def _handle_message(
    session: Session, tg: TelegramClient, message: dict[str, Any], summary: IngestSummary
) -> None:
    settings = get_settings()
    user_id = (message.get("from") or {}).get("id")
    chat_id = (message.get("chat") or {}).get("id")

    if not isinstance(chat_id, int):
        summary.ignored += 1
        return

    if user_id not in settings.allowed_user_ids:
        summary.ignored += 1
        log.info("ignored message from unauthorised user", user_id=user_id)
        return

    text = message.get("text") or message.get("caption") or ""

    if text.startswith("/"):
        summary.commands += 1
        _handle_command(session, tg, chat_id, text)
        return

    if _attach_file(session, tg, message, chat_id, summary):
        return

    pending = kv_get(session, f"{AWAITING_CAPTION_KEY}:{chat_id}")
    if pending and text.strip():
        _apply_caption(session, tg, chat_id, int(pending), text.strip())
        return

    urls = _URL_RE.findall(text)
    if not urls:
        summary.ignored += 1
        tg.send_message(chat_id, "Bir link ya da dosya bekliyorum. /help yazabilirsin.")
        return

    for url in urls:
        _queue_url(session, tg, chat_id, url, summary)


def _queue_url(
    session: Session, tg: TelegramClient, chat_id: int, url: str, summary: IngestSummary
) -> None:
    platform = detect_platform(url)
    if platform is None:
        summary.ignored += 1
        tg.send_message(chat_id, f"Bu platformu tanımıyorum: {url}")
        return

    source_id = _source_id_for(platform, url)
    existing = find_by_source(session, platform, source_id)
    if existing is not None:
        summary.duplicates += 1
        tg.send_message(
            chat_id,
            f"Bunu zaten aldım (#{existing.id}, durum: "
            f"{states.LABELS.get(existing.status, existing.status)}).",
        )
        return

    item = Item(
        source_platform=platform,
        source_url=url,
        source_id=source_id,
        status=states.QUEUED,
        telegram_chat_id=chat_id,
    )
    session.add(item)
    session.flush()
    log_event(session, stage="intake", message="queued from Telegram", item=item)
    summary.new_items += 1
    tg.send_message(chat_id, f"Aldım 👍 (#{item.id}) — sıraya koydum.")


def _attach_file(
    session: Session,
    tg: TelegramClient,
    message: dict[str, Any],
    chat_id: int,
    summary: IngestSummary,
) -> bool:
    """Handle a directly uploaded photo/video, either standalone or as the
    manual fallback for an item whose media could not be downloaded."""
    file_id, kind, suffix = _extract_file(message)
    if file_id is None:
        return False

    settings = get_settings()
    target = _pending_manual_item(session, message, chat_id)

    if target is None:
        target = Item(
            source_platform="manual",
            source_url=f"telegram://{message.get('message_id')}",
            source_id=f"tg-{message.get('message_id')}",
            status=states.QUEUED,
            telegram_chat_id=chat_id,
            text=(message.get("caption") or None),
        )
        session.add(target)
        session.flush()
        summary.new_items += 1

    dest = settings.media_dir / str(target.id) / f"source{suffix}"
    tg.download_file(file_id, dest)
    add_asset(
        session,
        target,
        kind="source",
        variant="original",
        local_path=str(dest),
        mime="video/mp4" if kind == "video" else "image/jpeg",
        size_bytes=dest.stat().st_size,
    )

    if target.status in (states.NEEDS_MANUAL, states.QUEUED):
        if target.status == states.QUEUED:
            transition(session, target, states.FETCHING, stage="intake", message="manual upload")
        transition(
            session, target, states.FETCHED, stage="intake", message="media supplied by user"
        )
        target.attempts = 0
        target.next_attempt_at = None

    summary.files_attached += 1
    tg.send_message(chat_id, f"Dosyayı aldım 👍 (#{target.id}) — işleme alıyorum.")
    return True


def _pending_manual_item(session: Session, message: dict[str, Any], chat_id: int) -> Item | None:
    """Prefer an explicit reply, otherwise the oldest item still waiting on a file."""
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        replied_id = reply.get("message_id")
        found = session.scalars(
            select(Item).where(
                Item.telegram_chat_id == chat_id, Item.telegram_message_id == replied_id
            )
        ).first()
        if found is not None:
            return found

    return session.scalars(
        select(Item)
        .where(Item.status == states.NEEDS_MANUAL, Item.telegram_chat_id == chat_id)
        .order_by(Item.created_at.asc())
    ).first()


def _extract_file(message: dict[str, Any]) -> tuple[str | None, str, str]:
    video = message.get("video")
    if isinstance(video, dict):
        return video.get("file_id"), "video", ".mp4"

    animation = message.get("animation")
    if isinstance(animation, dict):
        return animation.get("file_id"), "video", ".mp4"

    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        largest = max(photos, key=lambda p: p.get("file_size") or 0)
        return largest.get("file_id"), "image", ".jpg"

    document = message.get("document")
    if isinstance(document, dict):
        mime = str(document.get("mime_type") or "")
        if mime.startswith("video/"):
            return document.get("file_id"), "video", ".mp4"
        if mime.startswith("image/"):
            return document.get("file_id"), "image", ".jpg"

    return None, "", ""


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def _handle_command(session: Session, tg: TelegramClient, chat_id: int, text: str) -> None:
    command, _, argument = text.partition(" ")
    command = command.split("@")[0].lower()
    argument = argument.strip()

    if command in ("/start", "/help"):
        tg.send_message(chat_id, HELP_TEXT)
        return

    if command == "/status":
        counts = status_counts(session)
        if not counts:
            tg.send_message(chat_id, "Kuyruk boş.")
            return
        lines = [
            f"{states.LABELS.get(status, status)}: {count}"
            for status, count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        tg.send_message(chat_id, "📊 Kuyruk\n" + "\n".join(lines))
        return

    if command == "/stats":
        s = stats_since(session, 7)
        tg.send_message(
            chat_id,
            f"📈 Son 7 gün\nAlınan: {s['received']}\n"
            f"Reddedilen: {s['rejected']}\nYayınlanan: {s['published']}",
        )
        return

    if command == "/block":
        handle = argument.lstrip("@").strip()
        if not handle:
            tg.send_message(chat_id, "Kullanım: /block @kullaniciadi")
            return
        block(session, kind="author", value=handle, reason="blocked via Telegram")
        tg.send_message(chat_id, f"🚫 @{handle} engellendi.")
        return

    tg.send_message(chat_id, f"Bilinmeyen komut: {command}\n\n{HELP_TEXT}")


# ----------------------------------------------------------------------
# Inline buttons
# ----------------------------------------------------------------------
def _handle_callback(
    session: Session, tg: TelegramClient, callback: dict[str, Any], summary: IngestSummary
) -> None:
    settings = get_settings()
    summary.callbacks += 1

    callback_id = callback.get("id", "")
    user_id = (callback.get("from") or {}).get("id")
    if user_id not in settings.allowed_user_ids:
        tg.answer_callback(callback_id, "Yetkin yok.")
        return

    match = _CALLBACK_RE.match(str(callback.get("data") or ""))
    if match is None:
        tg.answer_callback(callback_id, "Anlaşılmayan buton.")
        return

    item = session.get(Item, int(match.group("item")))
    if item is None:
        tg.answer_callback(callback_id, "Kayıt bulunamadı.")
        return

    action = match.group("action")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id") or item.telegram_chat_id
    message_id = message.get("message_id")
    if not isinstance(chat_id, int):
        tg.answer_callback(callback_id, "Sohbet bulunamadı.")
        return

    if action == delivery.ACTION_APPROVE:
        _approve(session, tg, item, callback_id, chat_id, message_id)
    elif action == delivery.ACTION_REJECT:
        _reject(session, tg, item, callback_id, chat_id, message_id)
    elif action == delivery.ACTION_CAPTION:
        kv_set(session, f"{AWAITING_CAPTION_KEY}:{chat_id}", str(item.id))
        tg.answer_callback(callback_id, "Yeni caption'ı yaz.")
        tg.send_message(chat_id, f"#{item.id} için yeni caption'ı gönder.")
    elif action == delivery.ACTION_REDO:
        _redo(session, tg, item, callback_id, chat_id, message_id)
    else:
        tg.answer_callback(callback_id, "Bilinmeyen işlem.")


def _approve(
    session: Session,
    tg: TelegramClient,
    item: Item,
    callback_id: str,
    chat_id: int | None,
    message_id: int | None,
) -> None:
    if item.status != states.AWAITING_APPROVAL:
        tg.answer_callback(callback_id, f"Zaten {states.LABELS.get(item.status, item.status)}.")
        return

    transition(session, item, states.APPROVED, stage="approve", message="approved by user")
    tg.answer_callback(callback_id, "Onaylandı ✅")
    _clear_buttons(tg, chat_id, message_id)

    settings = get_settings()
    if settings.auto_publish and settings.delivery_mode == "instagram":
        tg.send_message(chat_id, f"#{item.id} onaylandı — yayın sırasına alınıyor.")  # type: ignore[arg-type]
    else:
        tg.send_message(
            chat_id,  # type: ignore[arg-type]
            f"#{item.id} onaylandı 👍 Videoyu indirip Instagram'da paylaşabilirsin.",
        )


def _reject(
    session: Session,
    tg: TelegramClient,
    item: Item,
    callback_id: str,
    chat_id: int | None,
    message_id: int | None,
) -> None:
    if item.status in states.TERMINAL:
        tg.answer_callback(callback_id, "Zaten kapanmış.")
        return
    transition(
        session,
        item,
        states.REJECTED,
        stage="approve",
        message="rejected by user",
        reason="user rejected",
    )
    tg.answer_callback(callback_id, "Silindi ❌")
    _clear_buttons(tg, chat_id, message_id)


def _redo(
    session: Session,
    tg: TelegramClient,
    item: Item,
    callback_id: str,
    chat_id: int | None,
    message_id: int | None,
) -> None:
    if item.status != states.AWAITING_APPROVAL:
        tg.answer_callback(callback_id, "Şu an yeniden çizilemez.")
        return
    # Flip the theme so a redo visibly changes something.
    item.add_note("Tema değiştirilerek yeniden çizildi.")
    settings = get_settings()
    current = _theme_override(session, item) or settings.card_theme
    kv_set(session, f"theme:{item.id}", "light" if current == "dark" else "dark")

    transition(session, item, states.SCREENED, stage="render", message="re-render requested")
    tg.answer_callback(callback_id, "Yeniden çiziliyor 🔁")
    _clear_buttons(tg, chat_id, message_id)


def _theme_override(session: Session, item: Item) -> str | None:
    return kv_get(session, f"theme:{item.id}")


def _apply_caption(
    session: Session, tg: TelegramClient, chat_id: int, item_id: int, caption: str
) -> None:
    item = session.get(Item, item_id)
    kv_set(session, f"{AWAITING_CAPTION_KEY}:{chat_id}", "")
    if item is None:
        tg.send_message(chat_id, "Kayıt bulunamadı.")
        return

    item.caption_override = caption
    log_event(session, stage="caption", message="caption overridden by user", item=item)

    if item.telegram_message_id:
        try:
            tg.edit_message_caption(
                chat_id,
                item.telegram_message_id,
                delivery.preview_text(item),
                reply_markup=delivery.review_keyboard(item.id),
            )
        except TelegramError as exc:
            log.info("caption edit failed", error=str(exc)[:200])
    tg.send_message(chat_id, f"#{item.id} caption güncellendi ✍️")


def _clear_buttons(tg: TelegramClient, chat_id: int | None, message_id: int | None) -> None:
    if chat_id is None or message_id is None:
        return
    try:
        tg.edit_reply_markup(chat_id, message_id, None)
    except TelegramError as exc:  # pragma: no cover - cosmetic
        log.debug("could not clear buttons", error=str(exc)[:160])


def _source_id_for(platform: str, url: str) -> str:
    if platform == "x":
        parsed = parse_x_url(url)
        if parsed:
            return parsed[1]
    trimmed = url.split("?")[0].rstrip("/")
    return trimmed.rsplit("/", 1)[-1] or trimmed


def theme_for(session: Session, item: Item) -> str:
    """Card theme for this item, honouring a `🔁 Yeniden çiz` override."""
    override = _theme_override(session, item)
    return override or get_settings().card_theme


__all__ = [
    "AWAITING_CAPTION_KEY",
    "HELP_TEXT",
    "OFFSET_KEY",
    "IngestSummary",
    "ingest",
    "theme_for",
]
