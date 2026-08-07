"""Database models. Same schema on SQLite and Postgres."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Item(Base):
    """One piece of content moving through the pipeline."""

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("source_platform", "source_id", name="uq_items_platform_source"),
        Index("ix_items_status", "status"),
        Index("ix_items_scheduled_for", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    source_platform: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(String(128))
    author_handle: Mapped[str | None] = mapped_column(String(128))
    author_name: Mapped[str | None] = mapped_column(String(256))
    author_avatar_url: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(String(8))
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(32), default="queued")
    reject_reason: Mapped[str | None] = mapped_column(Text)

    phash: Mapped[str | None] = mapped_column(String(32))
    dupe_of_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))

    ai_caption: Mapped[str | None] = mapped_column(Text)
    ai_hashtags: Mapped[str | None] = mapped_column(Text)  # JSON array
    ai_score: Mapped[int | None] = mapped_column(Integer)
    ai_flags: Mapped[str | None] = mapped_column(Text)  # JSON array
    ai_ocr_text: Mapped[str | None] = mapped_column(Text)

    caption_override: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)  # user-facing warnings, JSON array

    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ig_media_id: Mapped[str | None] = mapped_column(String(64))
    ig_permalink: Mapped[str | None] = mapped_column(Text)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )

    # -- JSON helpers -------------------------------------------------
    @property
    def hashtags(self) -> list[str]:
        return _json_list(self.ai_hashtags)

    @hashtags.setter
    def hashtags(self, value: list[str]) -> None:
        self.ai_hashtags = json.dumps(value, ensure_ascii=False)

    @property
    def flags(self) -> list[str]:
        return _json_list(self.ai_flags)

    @flags.setter
    def flags(self, value: list[str]) -> None:
        self.ai_flags = json.dumps(value, ensure_ascii=False)

    @property
    def note_list(self) -> list[str]:
        return _json_list(self.notes)

    def add_note(self, note: str) -> None:
        current = self.note_list
        if note not in current:
            current.append(note)
            self.notes = json.dumps(current, ensure_ascii=False)

    def asset(self, variant: str) -> MediaAsset | None:
        for a in self.assets:
            if a.variant == variant:
                return a
        return None

    def source_assets(self) -> list[MediaAsset]:
        return [a for a in self.assets if a.kind == "source"]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Item {self.id} {self.source_platform}:{self.source_id} {self.status}>"


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (Index("ix_media_assets_item", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))

    kind: Mapped[str] = mapped_column(String(16))  # source | render
    variant: Mapped[str] = mapped_column(String(16))  # original|card|reel|feed|story
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    local_path: Mapped[str | None] = mapped_column(Text)
    remote_url: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(Integer)
    mime: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[Item] = relationship(back_populates="assets")

    @property
    def is_video(self) -> bool:
        return bool(self.mime and self.mime.startswith("video")) or bool(self.duration_ms)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_item", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BlocklistEntry(Base):
    __tablename__ = "blocklist"
    __table_args__ = (UniqueConstraint("kind", "value", name="uq_blocklist_kind_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # author | domain | phash
    value: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Token(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), unique=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class KeyValue(Base):
    """Small persistent scratch space (Telegram update offset, cursors, ...)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        loaded: Any = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(loaded, list):
        return [str(x) for x in loaded]
    return []


__all__ = [
    "Base",
    "BlocklistEntry",
    "Event",
    "Item",
    "KeyValue",
    "MediaAsset",
    "Token",
    "utcnow",
]
