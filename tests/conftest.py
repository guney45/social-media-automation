from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from smauto.config import Settings, set_settings
from smauto.db.models import Base
from smauto.db.session import create_all, get_session_factory, reset_engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def settings(tmp_path: Path) -> Iterator[Settings]:
    """Isolated configuration + a scratch database for every test."""
    cfg = Settings(
        env="dev",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        telegram_bot_token="test-token",
        telegram_allowed_user_ids="42",
        telegram_target_chat_id=42,
        ai_enabled=False,
        anthropic_api_key="",
        delivery_mode="telegram",
        storage_backend="local",
        auto_publish=False,
        max_attempts=3,
        # Explicitly isolated from the real .env — otherwise a developer's own
        # OWN_ACCOUNT_* values (or lack of them) would leak into test results.
        own_account_name="",
        own_account_handle="",
        own_account_avatar="",
        own_account_verified=False,
    )
    set_settings(cfg)
    reset_engine()
    create_all()
    yield cfg
    reset_engine()
    set_settings(None)


@pytest.fixture
def session() -> Iterator[Session]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_tables() -> set[str]:
    return set(Base.metadata.tables)


class FakeTelegram:
    """Records calls instead of talking to Telegram."""

    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        self.updates = updates or []
        self.messages: list[dict[str, Any]] = []
        self.videos: list[dict[str, Any]] = []
        self.photos: list[dict[str, Any]] = []
        self.callbacks: list[tuple[str, str | None]] = []
        self.edits: list[dict[str, Any]] = []
        self.downloads: list[str] = []
        self._next_id = 1000
        self.download_payload = b"fake-bytes"

    # -- API surface used by the code under test ----------------------
    def get_me(self) -> dict[str, Any]:
        return {"username": "testbot", "id": 1}

    def get_updates(self, offset: int | None = None, *, limit: int = 50, timeout_s: int = 0):
        pending = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        return pending[:limit]

    def _record(self, bucket: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        payload["message_id"] = self._next_id
        bucket.append(payload)
        return {"message_id": self._next_id}

    def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        return self._record(self.messages, {"chat_id": chat_id, "text": text, **kwargs})

    def send_video(self, chat_id: int, path: Path, **kwargs: Any) -> dict[str, Any]:
        return self._record(self.videos, {"chat_id": chat_id, "path": path, **kwargs})

    def send_photo(self, chat_id: int, path: Path, **kwargs: Any) -> dict[str, Any]:
        return self._record(self.photos, {"chat_id": chat_id, "path": path, **kwargs})

    def send_document(self, chat_id: int, path: Path, **kwargs: Any) -> dict[str, Any]:
        return self._record(self.messages, {"chat_id": chat_id, "path": path, **kwargs})

    def edit_message_caption(self, chat_id: int, message_id: int, caption: str, **kw: Any) -> None:
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "caption": caption})

    def edit_reply_markup(self, chat_id: int, message_id: int, reply_markup: Any) -> None:
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "markup": reply_markup})

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.callbacks.append((callback_id, text))

    def download_file(self, file_id: str, dest: Path) -> Path:
        self.downloads.append(file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.download_payload)
        return dest

    def close(self) -> None:
        pass

    # -- helpers ------------------------------------------------------
    @property
    def texts(self) -> list[str]:
        return [m["text"] for m in self.messages if "text" in m]


@pytest.fixture
def fake_telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def no_notify(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Silence outbound notifications and capture their text."""
    sent: list[str] = []

    def _notify(chat_id: int | None, text: str, **_kw: Any) -> None:
        sent.append(text)

    monkeypatch.setattr("smauto.telegram.delivery.notify", _notify)
    return sent


@pytest.fixture(scope="session")
def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session")
def has_chromium() -> bool:
    from smauto.render.browser import find_chromium

    if find_chromium() is not None:
        return True
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False
