from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from smauto.config import Settings, set_settings
from smauto.db import states
from smauto.db.models import Item
from smauto.db.repo import is_blocked, kv_get
from smauto.telegram import delivery
from smauto.telegram.intake import ingest
from tests.conftest import FakeTelegram

CHAT = 42
USER = 42


def message_update(update_id: int, text: str, *, user: int = USER, **extra: Any) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "from": {"id": user},
            "chat": {"id": CHAT},
            "text": text,
            **extra,
        },
    }


def callback_update(update_id: int, item_id: int, action: str, *, user: int = USER) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "from": {"id": user},
            "data": f"a:{item_id}:{action}",
            "message": {"message_id": 555, "chat": {"id": CHAT}},
        },
    }


def make_item(session: Session, status: str = states.AWAITING_APPROVAL) -> Item:
    item = Item(
        source_platform="x",
        source_url="https://x.com/a/status/1",
        source_id="1",
        status=status,
        telegram_chat_id=CHAT,
        telegram_message_id=555,
        ai_caption="komik",
    )
    session.add(item)
    session.flush()
    return item


# ----------------------------------------------------------------------
def test_link_is_queued(session: Session, fake_telegram: FakeTelegram) -> None:
    fake_telegram.updates = [message_update(1, "şuna bak https://x.com/ornek/status/123")]
    summary = ingest(session, client=fake_telegram)

    assert summary.new_items == 1
    item = session.query(Item).one()
    assert item.source_platform == "x"
    assert item.source_id == "123"
    assert item.status == states.QUEUED
    assert any("Aldım" in t for t in fake_telegram.texts)


def test_offset_is_persisted_so_updates_are_not_replayed(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    fake_telegram.updates = [message_update(7, "https://x.com/a/status/1")]
    ingest(session, client=fake_telegram)
    assert kv_get(session, "telegram_update_offset") == "7"

    summary = ingest(session, client=fake_telegram)
    assert summary.new_items == 0


def test_duplicate_link_is_reported_not_requeued(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    fake_telegram.updates = [message_update(1, "https://x.com/a/status/55")]
    ingest(session, client=fake_telegram)

    fake_telegram.updates = [message_update(2, "https://x.com/a/status/55")]
    summary = ingest(session, client=fake_telegram)

    assert summary.duplicates == 1
    assert session.query(Item).count() == 1


def test_unauthorised_user_is_ignored(session: Session, fake_telegram: FakeTelegram) -> None:
    fake_telegram.updates = [message_update(1, "https://x.com/a/status/1", user=9999)]
    summary = ingest(session, client=fake_telegram)

    assert summary.new_items == 0
    assert summary.ignored == 1
    assert session.query(Item).count() == 0
    assert fake_telegram.messages == []


def test_unknown_platform_is_refused(session: Session, fake_telegram: FakeTelegram) -> None:
    fake_telegram.updates = [message_update(1, "https://example.com/lol")]
    ingest(session, client=fake_telegram)
    assert session.query(Item).count() == 0
    assert any("tanımıyorum" in t for t in fake_telegram.texts)


def test_multiple_links_in_one_message(session: Session, fake_telegram: FakeTelegram) -> None:
    fake_telegram.updates = [
        message_update(1, "https://x.com/a/status/1 ve https://x.com/b/status/2")
    ]
    summary = ingest(session, client=fake_telegram)
    assert summary.new_items == 2


# ----------------------------------------------------------------------
# Manual upload fallback
# ----------------------------------------------------------------------
def test_uploaded_video_creates_a_manual_item(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    update = message_update(1, "", video={"file_id": "vid1", "file_size": 1000})
    update["message"].pop("text")
    fake_telegram.updates = [update]

    summary = ingest(session, client=fake_telegram)

    assert summary.files_attached == 1
    item = session.query(Item).one()
    assert item.source_platform == "manual"
    assert item.status == states.FETCHED
    assert item.source_assets()


def test_uploaded_file_completes_an_item_waiting_for_media(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    waiting = make_item(session, status=states.NEEDS_MANUAL)

    update = message_update(1, "", video={"file_id": "vid1"})
    update["message"].pop("text")
    fake_telegram.updates = [update]
    ingest(session, client=fake_telegram)

    session.refresh(waiting)
    assert waiting.status == states.FETCHED
    assert waiting.source_assets()
    assert session.query(Item).count() == 1


def test_largest_photo_size_is_chosen(session: Session, fake_telegram: FakeTelegram) -> None:
    update = message_update(
        1,
        "",
        photo=[
            {"file_id": "small", "file_size": 100},
            {"file_id": "big", "file_size": 9000},
        ],
    )
    update["message"].pop("text")
    fake_telegram.updates = [update]
    ingest(session, client=fake_telegram)
    assert fake_telegram.downloads == ["big"]


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def test_status_command(session: Session, fake_telegram: FakeTelegram) -> None:
    make_item(session, status=states.QUEUED)
    fake_telegram.updates = [message_update(1, "/status")]
    ingest(session, client=fake_telegram)
    assert any("Kuyruk" in t for t in fake_telegram.texts)


def test_block_command_adds_to_blocklist(session: Session, fake_telegram: FakeTelegram) -> None:
    fake_telegram.updates = [message_update(1, "/block @spamci")]
    ingest(session, client=fake_telegram)
    assert is_blocked(session, kind="author", value="spamci") is not None


def test_help_command(session: Session, fake_telegram: FakeTelegram) -> None:
    fake_telegram.updates = [message_update(1, "/help")]
    ingest(session, client=fake_telegram)
    assert any("Paylaş" in t for t in fake_telegram.texts)


# ----------------------------------------------------------------------
# Inline buttons
# ----------------------------------------------------------------------
def test_approve_button(session: Session, fake_telegram: FakeTelegram) -> None:
    item = make_item(session)
    fake_telegram.updates = [callback_update(1, item.id, delivery.ACTION_APPROVE)]
    ingest(session, client=fake_telegram)

    session.refresh(item)
    assert item.status == states.APPROVED
    assert fake_telegram.callbacks[0][1] == "Onaylandı ✅"


def test_reject_button(session: Session, fake_telegram: FakeTelegram) -> None:
    item = make_item(session)
    fake_telegram.updates = [callback_update(1, item.id, delivery.ACTION_REJECT)]
    ingest(session, client=fake_telegram)

    session.refresh(item)
    assert item.status == states.REJECTED


def test_redo_button_sends_it_back_to_render_and_flips_the_theme(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    item = make_item(session)
    fake_telegram.updates = [callback_update(1, item.id, delivery.ACTION_REDO)]
    ingest(session, client=fake_telegram)

    session.refresh(item)
    assert item.status == states.SCREENED
    assert kv_get(session, f"theme:{item.id}") == "light"


def test_caption_button_then_reply_updates_the_caption(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    item = make_item(session)

    fake_telegram.updates = [callback_update(1, item.id, delivery.ACTION_CAPTION)]
    ingest(session, client=fake_telegram)

    fake_telegram.updates = [message_update(2, "elle yazılmış caption")]
    ingest(session, client=fake_telegram)

    session.refresh(item)
    assert item.caption_override == "elle yazılmış caption"
    assert fake_telegram.edits


def test_button_from_unauthorised_user_is_refused(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    item = make_item(session)
    fake_telegram.updates = [callback_update(1, item.id, delivery.ACTION_APPROVE, user=9999)]
    ingest(session, client=fake_telegram)

    session.refresh(item)
    assert item.status == states.AWAITING_APPROVAL
    assert fake_telegram.callbacks[0][1] == "Yetkin yok."


def test_double_approve_is_harmless(session: Session, fake_telegram: FakeTelegram) -> None:
    item = make_item(session)
    fake_telegram.updates = [
        callback_update(1, item.id, delivery.ACTION_APPROVE),
        callback_update(2, item.id, delivery.ACTION_APPROVE),
    ]
    ingest(session, client=fake_telegram)

    session.refresh(item)
    assert item.status == states.APPROVED


def test_one_bad_update_does_not_stall_the_rest(
    session: Session, fake_telegram: FakeTelegram
) -> None:
    broken = {"update_id": 1, "message": {"from": {"id": USER}}}  # no chat
    fake_telegram.updates = [broken, message_update(2, "https://x.com/a/status/9")]

    summary = ingest(session, client=fake_telegram)
    assert summary.new_items == 1


# ----------------------------------------------------------------------
# Caption composition
# ----------------------------------------------------------------------
def test_final_caption_always_credits_the_source(session: Session) -> None:
    item = make_item(session)
    item.author_handle = "ornek"
    item.hashtags = ["#mizah", "#komik"]

    caption = delivery.final_caption(item)
    assert "@ornek via X" in caption
    assert "#mizah" in caption
    assert caption.startswith("komik")


def test_caption_override_wins(session: Session) -> None:
    item = make_item(session)
    item.caption_override = "elle"
    assert delivery.final_caption(item).startswith("elle")


def test_require_telegram_rejects_an_open_bot(settings: Settings) -> None:
    settings.telegram_allowed_user_ids = ""
    set_settings(settings)
    with pytest.raises(ValueError, match="ALLOWED_USER_IDS"):
        settings.require_telegram()
