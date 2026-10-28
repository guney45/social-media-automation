"""Data access helpers shared by every pipeline stage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smauto.db import states
from smauto.db.models import BlocklistEntry, Event, Item, KeyValue, MediaAsset, Token, utcnow
from smauto.logging import get_logger

log = get_logger(__name__)

#: Backoff schedule between attempts, in seconds.
BACKOFF_SECONDS = (60, 300, 1500)


def log_event(
    session: Session,
    *,
    stage: str,
    message: str,
    item: Item | None = None,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> Event:
    ev = Event(
        item_id=item.id if item else None,
        stage=stage,
        level=level,
        message=message,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
    )
    session.add(ev)
    log.info(
        message, stage=stage, item_id=item.id if item else None, level=level, **(payload or {})
    )
    return ev


def transition(
    session: Session,
    item: Item,
    target: str,
    *,
    stage: str | None = None,
    message: str | None = None,
    reason: str | None = None,
) -> None:
    """Move an item to a new status, refusing transitions the state machine forbids."""
    if not states.can_transition(item.status, target):
        raise states.InvalidTransitionError(item.status, target)

    previous = item.status
    item.status = target
    item.updated_at = utcnow()
    if reason is not None:
        item.reject_reason = reason
    if target not in (states.FAILED,):
        item.last_error = None

    log_event(
        session,
        stage=stage or target,
        message=message or f"{previous} -> {target}",
        item=item,
        level="warning" if target in (states.FAILED, states.REJECTED) else "info",
        payload={"from": previous, "to": target, **({"reason": reason} if reason else {})},
    )


def mark_failed(
    session: Session,
    item: Item,
    *,
    stage: str,
    error: str,
    max_attempts: int,
) -> bool:
    """Record a stage failure.

    Returns True when the item will be retried, False when it is exhausted.
    """
    item.attempts += 1
    item.last_error = error[:4000]

    exhausted = item.attempts >= max_attempts
    if exhausted:
        item.next_attempt_at = None
    else:
        delay = BACKOFF_SECONDS[min(item.attempts - 1, len(BACKOFF_SECONDS) - 1)]
        item.next_attempt_at = utcnow() + timedelta(seconds=delay)

    if states.can_transition(item.status, states.FAILED):
        transition(
            session,
            item,
            states.FAILED,
            stage=stage,
            message=f"{stage} failed: {error[:200]}",
        )
    else:  # pragma: no cover - defensive
        item.status = states.FAILED

    return not exhausted


def claim_retry(session: Session, item: Item, *, now: datetime | None = None) -> bool:
    """Send a failed item back to the stage it should retry from."""
    now = now or utcnow()
    if item.status != states.FAILED:
        return False
    if item.next_attempt_at and item.next_attempt_at > now:
        return False

    # Reconstruct the retry target from the last failing stage.
    last = session.scalars(
        select(Event)
        .where(Event.item_id == item.id, Event.level == "warning")
        .order_by(Event.id.desc())
        .limit(1)
    ).first()
    stage = last.stage if last else states.FETCHING
    target = states.RETRY_FROM.get(stage, states.QUEUED)

    if not states.can_transition(item.status, target):
        return False
    transition(session, item, target, stage="retry", message=f"retrying from {target}")
    return True


def due_items(session: Session, *, limit: int = 20) -> list[Item]:
    """Items a `process` run should work on, oldest first."""
    now = utcnow()
    stmt = (
        select(Item)
        .where(Item.status.in_(tuple(states.ACTIVE)))
        .where((Item.next_attempt_at.is_(None)) | (Item.next_attempt_at <= now))
        .order_by(Item.created_at.asc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def find_by_source(session: Session, platform: str, source_id: str) -> Item | None:
    return session.scalars(
        select(Item).where(Item.source_platform == platform, Item.source_id == source_id)
    ).first()


def add_asset(
    session: Session,
    item: Item,
    *,
    kind: str,
    variant: str,
    local_path: str | None = None,
    remote_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_ms: int | None = None,
    mime: str | None = None,
    size_bytes: int | None = None,
    order_index: int = 0,
) -> MediaAsset:
    """Add an asset, replacing any existing one with the same variant and index.

    Removing it from the collection is enough — the relationship cascades
    delete-orphan. Calling `session.delete()` here would blow up on an asset
    added earlier in the same flush cycle, which happens on a re-render.
    """
    for existing in list(item.assets):
        if existing.variant == variant and existing.order_index == order_index:
            item.assets.remove(existing)

    asset = MediaAsset(
        item=item,
        kind=kind,
        variant=variant,
        order_index=order_index,
        local_path=local_path,
        remote_url=remote_url,
        width=width,
        height=height,
        duration_ms=duration_ms,
        mime=mime,
        bytes=size_bytes,
    )
    session.add(asset)
    return asset


def is_blocked(session: Session, *, kind: str, value: str) -> BlocklistEntry | None:
    return session.scalars(
        select(BlocklistEntry).where(
            BlocklistEntry.kind == kind, func.lower(BlocklistEntry.value) == value.lower()
        )
    ).first()


def block(session: Session, *, kind: str, value: str, reason: str | None = None) -> BlocklistEntry:
    existing = is_blocked(session, kind=kind, value=value)
    if existing:
        return existing
    entry = BlocklistEntry(kind=kind, value=value, reason=reason)
    session.add(entry)
    return entry


def kv_get(session: Session, key: str) -> str | None:
    row = session.get(KeyValue, key)
    return row.value if row else None


def kv_set(session: Session, key: str, value: str) -> None:
    row = session.get(KeyValue, key)
    if row:
        row.value = value
        row.updated_at = utcnow()
    else:
        session.add(KeyValue(key=key, value=value))


def get_token(session: Session, provider: str) -> Token | None:
    return session.scalars(select(Token).where(Token.provider == provider)).first()


def save_token(
    session: Session,
    provider: str,
    *,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
) -> Token:
    tok = get_token(session, provider)
    if tok is None:
        tok = Token(provider=provider)
        session.add(tok)
    tok.access_token = access_token
    if refresh_token is not None:
        tok.refresh_token = refresh_token
    tok.expires_at = expires_at
    tok.updated_at = utcnow()
    return tok


def status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(Item.status, func.count(Item.id)).group_by(Item.status)).all()
    return {str(status): int(count) for status, count in rows}


def stats_since(session: Session, days: int = 7) -> dict[str, int]:
    since = utcnow() - timedelta(days=days)
    total = session.scalar(select(func.count(Item.id)).where(Item.created_at >= since)) or 0
    rejected = (
        session.scalar(
            select(func.count(Item.id)).where(
                Item.created_at >= since, Item.status == states.REJECTED
            )
        )
        or 0
    )
    published = (
        session.scalar(
            select(func.count(Item.id)).where(
                Item.published_at.is_not(None), Item.published_at >= since
            )
        )
        or 0
    )
    return {"received": int(total), "rejected": int(rejected), "published": int(published)}


def recent_phashes(
    session: Session, *, limit: int = 2000, exclude_id: int | None = None
) -> list[tuple[int, str]]:
    stmt = (
        select(Item.id, Item.phash)
        .where(Item.phash.is_not(None))
        .order_by(Item.id.desc())
        .limit(limit)
    )
    if exclude_id is not None:
        stmt = stmt.where(Item.id != exclude_id)
    return [(int(i), str(h)) for i, h in session.execute(stmt).all() if h]


def utc(dt: datetime) -> datetime:
    """Normalise a possibly-naive datetime to UTC (SQLite loses tzinfo)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


__all__ = [
    "BACKOFF_SECONDS",
    "add_asset",
    "block",
    "claim_retry",
    "due_items",
    "find_by_source",
    "get_token",
    "is_blocked",
    "kv_get",
    "kv_set",
    "log_event",
    "mark_failed",
    "recent_phashes",
    "save_token",
    "stats_since",
    "status_counts",
    "transition",
    "utc",
]
