"""End-to-end pipeline behaviour with the network stubbed out."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from smauto.config import Settings
from smauto.db import states
from smauto.db.models import Item
from smauto.db.repo import add_asset, block
from smauto.media.probe import run_ffmpeg
from smauto.pipeline import _display_text, _post_for_card, process_once
from smauto.resolve import chain
from smauto.resolve.base import (
    ProtectedContentError,
    ResolvedMedia,
    ResolvedPost,
)
from smauto.telegram import delivery
from tests.conftest import FakeTelegram

CHAT = 42


@pytest.fixture
def photo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("src") / "photo.jpg"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1200x900", "-frames:v", "1", str(path)])
    return path


@pytest.fixture
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("src") / "clip.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    return path


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    """Route every outbound Telegram call to a recorder."""
    fake = FakeTelegram()
    monkeypatch.setattr("smauto.telegram.delivery.TelegramClient", lambda *a, **k: fake)
    return fake


def stub_resolver(monkeypatch: pytest.MonkeyPatch, post: ResolvedPost) -> None:
    monkeypatch.setattr(
        chain, "resolve", lambda url, resolvers=None: chain.ChainResult(post=post, attempts=[])
    )


def stub_resolver_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def _raise(url: str, resolvers=None):  # type: ignore[no-untyped-def]
        raise exc

    monkeypatch.setattr(chain, "resolve", _raise)


def queue_item(session: Session, url: str = "https://x.com/ornek/status/1") -> Item:
    item = Item(
        source_platform="x",
        source_url=url,
        source_id=url.rsplit("/", 1)[-1],
        status=states.QUEUED,
        telegram_chat_id=CHAT,
    )
    session.add(item)
    session.flush()
    return item


def make_post(media: list[ResolvedMedia], text: str = "komik bir şey 😂") -> ResolvedPost:
    return ResolvedPost(
        platform="x",
        source_id="1",
        source_url="https://x.com/ornek/status/1",
        author_handle="ornek",
        author_name="Örnek",
        text=text,
        media=media,
        resolver="stub",
    )


def drain(session: Session, times: int = 6) -> None:
    for _ in range(times):
        process_once(session, limit=10)
        session.flush()


# ----------------------------------------------------------------------
def test_photo_post_reaches_approval(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, photo: Path
) -> None:
    stub_resolver(monkeypatch, make_post([ResolvedMedia(kind="image", local_path=photo)]))
    item = queue_item(session)

    drain(session)

    session.refresh(item)
    assert item.status == states.AWAITING_APPROVAL
    assert item.asset("feed") is not None
    assert item.asset("card") is not None
    assert item.phash
    assert telegram.photos, "the preview should have been delivered"
    assert item.telegram_message_id


def test_video_post_produces_a_reel(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, clip: Path
) -> None:
    stub_resolver(monkeypatch, make_post([ResolvedMedia(kind="video", local_path=clip)]))
    item = queue_item(session)

    drain(session)

    session.refresh(item)
    assert item.status == states.AWAITING_APPROVAL
    reel = item.asset("reel")
    assert reel is not None
    assert (reel.width, reel.height) == (1080, 1920)
    assert telegram.videos


def test_text_only_post_produces_a_feed_image(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    stub_resolver(monkeypatch, make_post([], text="Pazartesi, haftanın reklamı."))
    item = queue_item(session)

    drain(session)

    session.refresh(item)
    assert item.status == states.AWAITING_APPROVAL
    assert item.asset("feed") is not None


def test_protected_source_is_rejected_without_retrying(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    stub_resolver_raising(monkeypatch, ProtectedContentError("private"))
    item = queue_item(session)

    drain(session, times=2)

    session.refresh(item)
    assert item.status == states.REJECTED
    assert item.attempts == 0


def test_exhausted_chain_asks_for_a_manual_upload(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    stub_resolver_raising(monkeypatch, chain.ChainExhaustedError([]))
    item = queue_item(session)

    drain(session, times=2)

    session.refresh(item)
    assert item.status == states.NEEDS_MANUAL
    assert any("indiremedim" in t for t in telegram.texts)


def test_stage_failure_retries_then_gives_up(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, settings: Settings
) -> None:
    stub_resolver_raising(monkeypatch, RuntimeError("network down"))
    item = queue_item(session)

    process_once(session, limit=5)
    session.flush()
    session.refresh(item)
    assert item.status == states.FAILED
    assert item.attempts == 1
    assert item.next_attempt_at is not None

    # Backoff has not elapsed, so nothing should happen.
    process_once(session, limit=5)
    session.refresh(item)
    assert item.attempts == 1


def test_blocked_author_is_rejected(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, photo: Path
) -> None:
    block(session, kind="author", value="ornek")
    stub_resolver(monkeypatch, make_post([ResolvedMedia(kind="image", local_path=photo)]))
    item = queue_item(session)

    drain(session, times=3)

    session.refresh(item)
    assert item.status == states.REJECTED
    assert item.reject_reason is not None and "engel" in item.reject_reason


def test_duplicate_media_is_rejected(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, photo: Path
) -> None:
    stub_resolver(monkeypatch, make_post([ResolvedMedia(kind="image", local_path=photo)]))
    first = queue_item(session, "https://x.com/ornek/status/1")
    drain(session)
    session.refresh(first)
    assert first.phash

    second = queue_item(session, "https://x.com/ornek/status/2")
    drain(session)

    session.refresh(second)
    assert second.status == states.REJECTED
    assert second.dupe_of_item_id == first.id


def test_manual_upload_skips_straight_to_screening(
    session: Session, telegram: FakeTelegram, photo: Path
) -> None:
    item = Item(
        source_platform="manual",
        source_url="telegram://1",
        source_id="tg-1",
        status=states.FETCHED,
        telegram_chat_id=CHAT,
        text="elle yüklendi",
    )
    session.add(item)
    session.flush()
    add_asset(session, item, kind="source", variant="original", local_path=str(photo))
    session.flush()

    drain(session)

    session.refresh(item)
    assert item.status == states.AWAITING_APPROVAL


def test_preview_caption_includes_credit_and_warnings(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, photo: Path
) -> None:
    stub_resolver(monkeypatch, make_post([ResolvedMedia(kind="image", local_path=photo)]))
    item = queue_item(session)
    drain(session)

    session.refresh(item)
    text = delivery.preview_text(item)
    assert "@ornek" in text


def test_process_is_idempotent_when_the_queue_is_empty(session: Session) -> None:
    summary = process_once(session)
    assert summary.processed == 0
    assert summary.errors == []


def test_ai_disabled_still_produces_a_caption(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, photo: Path
) -> None:
    stub_resolver(monkeypatch, make_post([ResolvedMedia(kind="image", local_path=photo)]))
    item = queue_item(session)
    drain(session)

    session.refresh(item)
    assert item.ai_caption
    assert item.hashtags


# ----------------------------------------------------------------------
# Own-account rebranding — the card and caption must never point at the
# source's identity, and a quoted tweet must not surface a second author.
# ----------------------------------------------------------------------
def test_quoted_text_is_folded_in_without_a_separate_attribution(
    session: Session, monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, photo: Path
) -> None:
    post = make_post([ResolvedMedia(kind="image", local_path=photo)], text="ana metin")
    post.quoted = ResolvedPost(
        platform="x",
        source_id="2",
        source_url="https://x.com/baska/status/2",
        author_handle="baskahesap",
        author_name="Başka Hesap",
        text="alıntılanan tweet metni",
    )
    stub_resolver(monkeypatch, post)
    item = queue_item(session)

    drain(session)

    session.refresh(item)
    assert item.text is not None
    assert "ana metin" in item.text
    assert "alıntılanan tweet metni" in item.text


def test_display_text_merges_quoted_text() -> None:
    post = make_post([], text="ana metin")
    post.quoted = ResolvedPost(
        platform="x", source_id="2", source_url="https://x.com/b/status/2", text="alıntı metni"
    )
    assert _display_text(post) == "ana metin\n\nalıntı metni"


def test_card_uses_own_account_identity_not_the_source(
    session: Session, settings: Settings
) -> None:
    settings.own_account_name = "Benim Hesabım"
    settings.own_account_handle = "benimhesabim"
    settings.own_account_avatar = "https://example.com/me.jpg"

    item = queue_item(session)
    item.author_handle = "baskasi"
    item.author_name = "Başkası"
    item.author_avatar_url = "https://example.com/baskasi.jpg"
    item.text = "espri"

    post = _post_for_card(item)

    assert post.author_handle == "benimhesabim"
    assert post.author_name == "Benim Hesabım"
    assert post.author_avatar_url == "https://example.com/me.jpg"
    assert post.quoted is None


def test_card_falls_back_to_source_identity_when_own_account_unconfigured(
    session: Session, settings: Settings
) -> None:
    item = queue_item(session)
    item.author_handle = "baskasi"
    item.author_name = "Başkası"

    post = _post_for_card(item)

    assert post.author_handle == "baskasi"
    assert post.author_name == "Başkası"
