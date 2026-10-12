"""Spread approved items over fixed daily slots.

Publishing a batch back-to-back suppresses reach and reads as automation, so
approved items queue up and go out at a handful of fixed times instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from smauto.config import get_settings
from smauto.db import states
from smauto.db.models import Item
from smauto.db.repo import utc
from smauto.logging import get_logger

log = get_logger(__name__)

#: How far ahead the planner will look before giving up.
MAX_DAYS_AHEAD = 14


def next_free_slot(session: Session, *, now: datetime | None = None) -> datetime | None:
    """First slot (in UTC) that is still in the future and not already taken."""
    settings = get_settings()
    tz = settings.tz
    now = (now or datetime.now(UTC)).astimezone(tz)

    taken = _taken_slots(session)

    for day_offset in range(MAX_DAYS_AHEAD):
        day = (now + timedelta(days=day_offset)).date()
        used_today = sum(1 for slot in taken if slot.astimezone(tz).date() == day)
        if used_today >= settings.max_per_day:
            continue

        for slot_time in settings.slots:
            candidate = datetime.combine(day, slot_time, tzinfo=tz)
            if candidate <= now:
                continue
            if any(abs((candidate - t).total_seconds()) < 60 for t in taken):
                continue
            return candidate.astimezone(UTC)

    return None


def schedule(session: Session, item: Item, *, now: datetime | None = None) -> datetime | None:
    """Give an approved item a publication time."""
    slot = next_free_slot(session, now=now)
    if slot is None:
        log.warning("no free publish slot found", item_id=item.id)
        return None
    item.scheduled_for = slot
    return slot


def due_for_publish(session: Session, *, now: datetime | None = None, limit: int = 5) -> list[Item]:
    now = now or datetime.now(UTC)
    rows = session.scalars(
        select(Item)
        .where(Item.status == states.SCHEDULED)
        .where(Item.scheduled_for.is_not(None))
        .order_by(Item.scheduled_for.asc())
        .limit(limit * 4)
    ).all()
    return [i for i in rows if i.scheduled_for and utc(i.scheduled_for) <= now][:limit]


def _taken_slots(session: Session) -> list[datetime]:
    rows = session.scalars(
        select(Item.scheduled_for)
        .where(Item.scheduled_for.is_not(None))
        .where(Item.status.in_((states.SCHEDULED, states.PUBLISHING, states.PUBLISHED)))
    ).all()
    return [utc(r) for r in rows if r is not None]


__all__ = ["MAX_DAYS_AHEAD", "due_for_publish", "next_free_slot", "schedule"]
