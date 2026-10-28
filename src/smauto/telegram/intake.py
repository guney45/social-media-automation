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
_COMMAND_RE = re.compile(r"^(?P<command>\S+)\s*(?P<argument>.*)$", re.DOTALL)

HELP_TEXT = (
    "Komik bir gönderi gördüğünde *Paylaş → Telegram → bu bot*.\n\n"
    "X (Twitter), Instagram ve TikTok linkleri destekleniyor. "
    "Doğrudan video/foto da yükleyebilirsin.\n\n"
    "Instagram ve TikTok gönderilerinde karta metin basılmaz: video + takip kartı gelir, "
    "hemen ardından gönderinin kendi açıklaması tek başına bir mesaj olarak "
    "düşer — basılı tutup kopyalayabilirsin.\n\n"
    "Açıklama listesi (doluysa her videonun ardından buradan rastgele biri gelir):\n"
    "/ekle <metin> — listeye açıklama ekle (bir mesajı yanıtlayarak da olur)\n"
    "/sabitle <metin> — listeyi sadece bu açıklama yap\n"
    "/liste — açıklamaları göster\n"
    "/sil <numara> — bir açıklamayı sil\n"
    "/temizle — listeyi boşalt (kaynağın kendi açıklamasına döner)\n\n"
    "Komutlar:\n"
    "/status — kuyruk durumu\n"
    "/stats — son 7 gün\n"
    "/block @kullanici — kaynağı engelle\n"
    "/help — bu mesaj"
)

SUPPORTED_PLATFORMS = frozenset({"x", "instagram", "tiktok"})


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
        # No stored offset on the read: after a week without updates Telegram
        # restarts update_id at a random value, so a stale offset can sit above
        # every new id — and passing it would confirm (delete) them unread.
        # Unconfirmed updates are exactly what is pending, so read those and
        # confirm what we handled afterwards.
        updates = tg.get_updates(None, limit=limit)
        summary.updates = len(updates)

        highest = 0
        for update in updates:
            highest = max(highest, int(update.get("update_id", 0)))
            try:
                _dispatch(session, tg, update, summary)
            except Exception as exc:
                summary.errors.append(str(exc)[:200])
                log.warning("update handling failed", error=str(exc)[:300])

        if updates:
            tg.get_updates(highest + 1, limit=1)
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
        reply = message.get("reply_to_message") or {}
        reply_text = (reply.get("text") or reply.get("caption") or "").strip()
        _handle_command(session, tg, chat_id, text, reply_text)
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

    if platform not in SUPPORTED_PLATFORMS:
        summary.ignored += 1
        tg.send_message(
            chat_id,
            "Şu an X (Twitter), Instagram ve TikTok destekleniyor.",
        )
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
def _handle_command(
    session: Session, tg: TelegramClient, chat_id: int, text: str, reply_text: str = ""
) -> None:
    # Split on any whitespace, not just a space: a description pasted after
    # /ekle often starts on the next line, and its own line breaks must stay.
    match = _COMMAND_RE.match(text.strip())
    command = match.group("command") if match else text
    command = command.split("@")[0].lower()
    argument = match.group("argument").strip() if match else ""

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

    if command in ("/ekle", "/sabitle", "/liste", "/sil", "/temizle"):
        _handle_caption_pool(session, tg, chat_id, command, argument or reply_text)
        return

    tg.send_message(chat_id, f"Bilinmeyen komut: {command}\n\n{HELP_TEXT}")


def _handle_caption_pool(
    session: Session, tg: TelegramClient, chat_id: int, command: str, argument: str
) -> None:
    pool = delivery.caption_pool(session)

    if command in ("/ekle", "/sabitle"):
        if not argument:
            tg.send_message(
                chat_id, f"Kullanım: {command} <açıklama> — ya da bir mesajı yanıtlayarak yaz."
            )
            return
        pool = [argument] if command == "/sabitle" else [*pool, argument]
        delivery.set_caption_pool(session, pool)
        if len(pool) == 1:
            tg.send_message(chat_id, "📌 Sabitlendi. Bundan sonra her videoda bu açıklama gelecek.")
        else:
            tg.send_message(
                chat_id,
                f"➕ Eklendi. Listede {len(pool)} açıklama var, "
                "her seferinde biri rastgele gelecek.",
            )
        return

    if command == "/liste":
        if not pool:
            tg.send_message(
                chat_id, "Liste boş — videolarla birlikte kaynağın kendi açıklaması geliyor."
            )
            return
        lines = [f"{n}. {caption}" for n, caption in enumerate(pool, start=1)]
        tg.send_message(chat_id, "📝 Açıklamalar\n\n" + "\n\n".join(lines))
        return

    if command == "/sil":
        if not argument.isdigit() or not 1 <= int(argument) <= len(pool):
            tg.send_message(chat_id, "Kullanım: /sil <numara> — numaraları /liste gösterir.")
            return
        del pool[int(argument) - 1]
        delivery.set_caption_pool(session, pool)
        tg.send_message(chat_id, f"🗑 Silindi. Listede {len(pool)} açıklama kaldı.")
        return

    delivery.set_caption_pool(session, [])
    tg.send_message(chat_id, "🧹 Liste boşaltıldı — kaynağın kendi açıklamasına dönüldü.")


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
    "SUPPORTED_PLATFORMS",
    "IngestSummary",
    "ingest",
    "theme_for",
]
